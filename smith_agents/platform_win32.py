"""Windows shell and OS services. GUI imports stay lazy for shared-core tests."""
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import subprocess
import sys
import sysconfig
import time
from PIL import Image, ImageChops
from .session_windows import WindowTargets, title_matches_project
from .branding import APP_NAME, COMMAND, LEGACY_COMMAND

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
except Exception:  # pragma: no cover - older Windows
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    _DPI = ctypes.windll.user32.GetDpiForSystem()
except Exception:
    _DPI = 96
SCALE = max(1.0, _DPI / 96.0)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _sibling_exe(name):
    """The interpreter exe called `name` next to the running one, or the
    running one if there is no such file. python.exe and pythonw.exe ship side
    by side, so this is how the startup dialogs, the autostart shortcut and
    run.cmd all agree on which Python is meant."""
    candidate = os.path.join(os.path.dirname(sys.executable), name)
    return candidate if os.path.exists(candidate) else sys.executable


def _entry_exe():
    """The Smith shim pip writes into the environment's Scripts
    directory, if this is an installed copy. It carries its own interpreter,
    which is what makes it the right thing to put in a shortcut."""
    try:
        directory = sysconfig.get_path("scripts")
    except Exception:
        return None
    for name in (COMMAND, LEGACY_COMMAND):
        path = os.path.join(directory, name + ".exe")
        if os.path.exists(path):
            return path
    return None


def _launch_argv():
    """Command, and the directory to run it in, for starting a second copy.

    Installed there is a shim to call. From a source tree there is not, so it
    goes through the launcher with -m and runs from the folder above the
    package, which is what keeps the package importable."""
    exe = _entry_exe()
    if exe:
        return [exe], os.path.dirname(exe)
    runner = _sibling_exe("pythonw.exe") or sys.executable
    return [runner, "-m", "smith_agents"], os.path.dirname(SCRIPT_DIR)


_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _premultiplied_bgra(image):
    red, green, blue, alpha = image.split()
    red = ImageChops.multiply(red, alpha)
    green = ImageChops.multiply(green, alpha)
    blue = ImageChops.multiply(blue, alpha)
    return Image.merge("RGBA", (blue, green, red, alpha)).tobytes("raw", "RGBA")


class LayeredSurface:
    """Paints a PIL RGBA image straight onto a Tk toplevel's HWND."""

    def __init__(self, toplevel):
        toplevel.update_idletasks()
        hwnd = _user32.GetParent(toplevel.winfo_id())
        self.hwnd = hwnd or toplevel.winfo_id()
        style = _user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                               style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)

    def paint(self, image, x, y, opacity=1.0):
        width, height = image.size
        data = _premultiplied_bgra(image)

        screen_dc = _user32.GetDC(None)
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height          # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), DIB_RGB_COLORS,
                                         ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, data, len(data))
            old = _gdi32.SelectObject(mem_dc, bitmap)
            size = wintypes.SIZE(width, height)
            src = wintypes.POINT(0, 0)
            dst = wintypes.POINT(int(x), int(y))
            blend = _BLENDFUNCTION(AC_SRC_OVER, 0,
                                   int(max(0, min(255, round(opacity * 255)))),
                                   AC_SRC_ALPHA)
            _user32.UpdateLayeredWindow(self.hwnd, screen_dc, ctypes.byref(dst),
                                        ctypes.byref(size), mem_dc, ctypes.byref(src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
            _gdi32.SelectObject(mem_dc, old)
        finally:
            _gdi32.DeleteObject(bitmap)
            _gdi32.DeleteDC(mem_dc)
            _user32.ReleaseDC(None, screen_dc)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

_STILL_ACTIVE = 259
_SYNCHRONIZE = 0x00100000
_QUERY_LIMITED_INFORMATION = 0x1000


def _process_api():
    kernel32 = ctypes.windll.kernel32
    # Process handles are pointer-sized on 64-bit Windows.
    signatures = (
        ('OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ('GetProcessTimes', [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
        ('GetExitCodeProcess', [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ('QueryFullProcessImageNameW', [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                      ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ('TerminateProcess', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
    )
    for name, args, result in signatures:
        function = getattr(kernel32, name)
        function.argtypes, function.restype = args, result
    return kernel32


def _handle_started(kernel32, handle):
    created = wintypes.FILETIME()
    spare = (wintypes.FILETIME * 3)()
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created),
                                   ctypes.byref(spare[0]), ctypes.byref(spare[1]),
                                   ctypes.byref(spare[2])):
        return None
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def _proc_started(pid):
    """The process's creation time, as the FILETIME integer Windows keeps.

    A PID on its own is not an identity - Windows hands them out again - so
    this is what tells the session that was here from whatever holds its
    number now."""
    kernel32 = _process_api()
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        return _handle_started(kernel32, handle)
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid, started=None):
    """Session files outlive a crashed process, so a dead PID means the row is
    stale rather than a session the user closed.

    `started` is the creation time the session file recorded. Windows reuses a
    PID as soon as it is free, so without that check a stale row could come
    back to life wearing somebody else's process - and the close cross would
    then terminate that process rather than a Claude session."""
    if not pid or pid <= 0:
        return False
    if started:
        actual = _proc_started(pid)
        if actual is not None and actual != started:
            return False
    kernel32 = _process_api()
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
                                  False, pid)
    if not handle:
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return False
    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == _STILL_ACTIVE
        return True         # exists, but we may not read its exit code
    finally:
        kernel32.CloseHandle(handle)


def terminate_agent(agent):
    """Stop only a verified Claude process, using one handle for check and kill."""
    pid, started = agent.get("pid"), agent.get("started_at")
    if agent.get("sub") or not isinstance(pid, int) or pid <= 0 or not started:
        return False
    kernel32 = _process_api()
    PROCESS_TERMINATE = 0x0001
    handle = kernel32.OpenProcess(PROCESS_TERMINATE | _QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        if _handle_started(kernel32, handle) != started:
            return False
        executable = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(executable))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(length)):
            return False
        import ntpath
        if ntpath.basename(executable.value).lower() != 'claude.exe':
            return False  # Never kill VS Code, a terminal, or a shared wrapper.
        # The exit code is ours to choose, and whoever launched the session
        # reads it. Ending a session from here is a deliberate act, not a
        # crash, so it exits zero - killing it with 1 made Claude Code's VS
        # Code extension report "process exited with code 1" every time.
        return bool(kernel32.TerminateProcess(handle, 0))
    finally:
        kernel32.CloseHandle(handle)


_AGENT_WINDOWS = WindowTargets()


def _window_owner(hwnd):
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return pid.value


def raise_agent_window(agent):
    """Bring the window hosting this agent to the front.

    Matching is by title, not PID: every VS Code window shares one process, so
    a PID alone cannot pick between them. Returns True if a window was raised."""
    if not _pid_alive(agent.get('pid'), agent.get('started_at')):
        return False
    user32 = ctypes.windll.user32
    hwnd = _opened_agent_window(agent)
    if hwnd is not None:
        native = ctypes.c_void_p(hwnd)
        if user32.IsIconic(native):
            user32.ShowWindow(native, 9)
        return bool(user32.SetForegroundWindow(native))
    leaf = os.path.basename(agent.get("cwd", "").rstrip("\\/"))
    if not leaf:
        return False

    user32 = ctypes.windll.user32
    found = []
    allowed_owners = None
    if agent.get('provider') == 'codex':
        import psutil
        try:
            process = psutil.Process(agent['pid'])
            if process.create_time() != agent.get('process_created'):
                return False
            allowed_owners = {p.pid for p in [process, *process.parents()]}
        except psutil.Error:
            return False

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        if allowed_owners is not None and _window_owner(hwnd) not in allowed_owners:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_matches_project(buf.value, leaf):
            found.append(hwnd)
        return True

    user32.EnumWindows(proto(visit), None)
    if len(found) != 1:
        return False

    hwnd = found[0]
    SW_RESTORE = 9
    if user32.IsIconic(ctypes.c_void_p(hwnd)):
        user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
    opened = bool(user32.SetForegroundWindow(ctypes.c_void_p(hwnd)))
    owner = _window_owner(hwnd)
    started = _proc_started(owner) if owner else None
    if opened and started is not None:
        # Remember the exact window we opened. Never rerun a title search when
        # hiding, which could minimize a different window with a similar name.
        _AGENT_WINDOWS.remember(agent, (hwnd, owner, started))
    return opened


def _opened_agent_window(agent):
    target = _AGENT_WINDOWS.get(agent)
    if target is None:
        return None
    hwnd, owner, started = target
    user32 = ctypes.windll.user32
    if (not user32.IsWindow(ctypes.c_void_p(hwnd)) or _window_owner(hwnd) != owner
            or _proc_started(owner) != started
            or not _pid_alive(agent.get('pid'), agent.get('started_at'))):
        _AGENT_WINDOWS.discard(agent)
        return None
    return hwnd


def agent_window_is_visible(agent):
    hwnd = _opened_agent_window(agent)
    if hwnd is None:
        return False
    user32 = ctypes.windll.user32
    return bool(user32.IsWindowVisible(ctypes.c_void_p(hwnd))
                and not user32.IsIconic(ctypes.c_void_p(hwnd)))


def hide_agent_window(agent):
    """Minimize only the recorded window; never close or terminate a session."""
    hwnd = _opened_agent_window(agent)
    if hwnd is None:
        return False
    user32 = ctypes.windll.user32
    native = ctypes.c_void_p(hwnd)
    if user32.IsIconic(native):
        return True
    user32.ShowWindow(native, 6)  # SW_MINIMIZE; no WM_CLOSE or process signals.
    return True  # The controller verifies visibility after the window updates.


def tray_size():
    """The box Windows gives a tray icon - 16 at 100%, 20 at 125%, and so on.
    Drawing to it directly is the whole difference between a sharp icon and a
    soft one: anything else gets resized by somebody, and a resized edge at
    this size is a grey smudge rather than a line."""
    try:
        return max(16, int(ctypes.windll.user32.GetSystemMetrics(49)))
    except Exception:
        return 16


MUTEX_NAME = "Local\\ClaudeUsageWidget"
ERROR_ALREADY_EXISTS = 183
_INSTANCE_LOCK = None


def claim_single_instance(wait=0.0):
    """Hold a per-session named mutex for the life of the process, so autostart
    plus a manual launch cannot end up with two chips on the same pixel.
    ``wait`` covers the theme relaunch, where the outgoing process may not have
    let go of the handle yet."""
    global _INSTANCE_LOCK
    deadline = time.time() + wait
    while True:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if handle and ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
            _INSTANCE_LOCK = handle       # kept alive deliberately
            return True
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        if time.time() >= deadline:
            return False
        time.sleep(0.15)


def release_single_instance():
    global _INSTANCE_LOCK
    if _INSTANCE_LOCK:
        ctypes.windll.kernel32.CloseHandle(_INSTANCE_LOCK)
        _INSTANCE_LOCK = None



process_started = _proc_started
pid_alive = _pid_alive

def display_scale():
    return _DPI, SCALE

def config_dir():
    # Retain the existing settings/cache/lock namespace on upgrade.
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "claude-widget")

def font_candidates(names):
    windir = os.environ.get("WINDIR", r"C:\Windows")
    return [p for name in names for p in (os.path.join(windir, "Fonts", name), name)]

def read_credentials(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)

def session_process_start(data, pid):
    recorded = int(data.get("procStart") or 0)
    if recorded:
        return recorded
    actual = _proc_started(pid) if isinstance(pid, int) and pid > 0 else None
    if actual is None:
        return 0
    session_ms = float(data.get("startedAt") or 0)
    process_ms = actual / 10000 - 11644473600000  # FILETIME to Unix milliseconds
    if session_ms and process_ms > session_ms + 5000:
        return -1
    return actual

def show_error(message):
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x50010)

def launch_argv():
    return _launch_argv()

def relaunch(argv, cwd):
    return subprocess.Popen(argv, cwd=cwd, close_fds=True, creationflags=0x00000008)

def startup_path():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                       "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                       "Smith Agents.lnk")

def _legacy_startup_path():
    return os.path.join(os.path.dirname(startup_path()), "ClaudeUsageWidget.lnk")

def autostart_enabled():
    return any(os.path.exists(path) for path in (startup_path(), _legacy_startup_path()))

def set_autostart(enabled):
    path = startup_path()
    if not enabled:
        for candidate in (path, _legacy_startup_path()):
            if os.path.exists(candidate):
                os.remove(candidate)
        return
    argv, cwd = _launch_argv()
    def quote(value):
        return value.replace("'", "''")
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
          "$s.TargetPath = '%s'; $s.Arguments = '%s'; "
          "$s.WorkingDirectory = '%s'; $s.WindowStyle = 7; $s.IconLocation = '%s'; $s.Save()"
          % tuple(quote(v) for v in (path, argv[0], subprocess.list2cmdline(argv[1:]), cwd,
                                    os.path.join(SCRIPT_DIR, "assets", "smith-agents.ico"))))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, creationflags=0x08000000)
    # Remove the old entry only after its replacement has been created.
    legacy = _legacy_startup_path()
    if os.path.exists(legacy):
        os.remove(legacy)

def create_shell(widget, core):
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.title(core.APP_NAME)
    root.tk.call("tk", "scaling", _DPI / 72.0)
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry("%dx%d+0+0" % widget._bar_size)
    root.deiconify()
    surface = LayeredSurface(root)
    if not widget.config["visible"]:
        root.withdraw()
    for event, callback in (("<ButtonPress-1>", widget._on_press),
                            ("<B1-Motion>", widget._on_drag),
                            ("<ButtonRelease-1>", widget._on_release),
                            ("<Button-3>", widget._on_context),
                            ("<MouseWheel>", widget._on_agent_wheel),
                            ("<Motion>", widget._on_motion)):
        root.bind(event, callback)
    try:
        root.iconbitmap(default=core.ICON_PATH)
    except Exception:
        pass
    return root, surface

def screen_bounds(root, position=None):
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - int(48 * SCALE)

def start_tray(tray):
    import threading
    threading.Thread(target=tray.run, daemon=True).start()

def build_tray(self):
    import tkinter as tk
    import pystray
    import webbrowser
    from .core import APP_NAME, DOCKS, ZOOM, ZOOM_STEPS, THEMES, THEME_NAME, USAGE_HELP_URL, render_tray
    def ui(func):
        return lambda *_a: self.root.after(0, func)

    def dock_item(name):
        return pystray.MenuItem(
            name.replace("-", " ").title(),
            ui(lambda n=name: self.set_dock(n)),
            checked=lambda _i, n=name: self.config["dock"] == n,
            radio=True,
        )

    def opacity_item(value):
        return pystray.MenuItem(
            "%d%%" % int(value * 100),
            ui(lambda v=value: self.set_opacity(v)),
            checked=lambda _i, v=value: abs(self.config["opacity"] - v) < 0.01,
            radio=True,
        )

    menu = pystray.Menu(
        pystray.MenuItem("Refresh now", ui(self.refresh_now), default=True),
        pystray.MenuItem("Show console", ui(self.toggle_bar),
                         checked=lambda _i: self.config["visible"]),
        pystray.MenuItem("Tuck to edge", ui(self.toggle_tuck),
                         checked=lambda _i: self.config["tucked"]),
        pystray.MenuItem("Tuck position", pystray.Menu(*[
            pystray.MenuItem(edge.title(), ui(lambda edge=edge: self.set_tuck(True, side=edge)),
                             checked=lambda _i, edge=edge: self.config['tucked'] and self.config['tuck_side'] == edge)
            for edge in ('top', 'left', 'right')])),
        pystray.MenuItem("Dock", pystray.Menu(*[dock_item(d) for d in DOCKS])),
        pystray.MenuItem("Opacity", pystray.Menu(*[opacity_item(v)
                                                   for v in (1.0, 0.94, 0.85, 0.7)])),
        pystray.MenuItem("Size", pystray.Menu(*[
            pystray.MenuItem(
                "%d%%" % round(v * 100),
                ui(lambda z=v: self.set_zoom(z)),
                checked=lambda _i, z=v: abs(ZOOM - z) < 0.01,
                radio=True)
            for v in ZOOM_STEPS])),
        pystray.MenuItem("Theme", pystray.Menu(*[
            pystray.MenuItem(
                THEMES[key]["label"],
                ui(lambda k=key: self.set_theme(k)),
                checked=lambda _i, k=key: THEME_NAME == k,
                radio=True)
            for key in THEMES])),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows", ui(self.toggle_autostart),
                         checked=lambda _i: autostart_enabled()),
        pystray.MenuItem("About usage limits",
                         lambda *_a: webbrowser.open(USAGE_HELP_URL)),
        pystray.MenuItem("Connect Codex status…", ui(self.connect_codex)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", ui(self.quit)),
    )
    return pystray.Icon("smith-agents", render_tray(None, False, None), APP_NAME, menu)

def build_context_menu(self):
    import tkinter as tk
    import pystray
    import webbrowser
    from .core import APP_NAME, DOCKS, ZOOM, ZOOM_STEPS, THEMES, THEME_NAME, USAGE_HELP_URL, render_tray
    menu = tk.Menu(self.root, tearoff=0)
    self._demo_var = tk.BooleanVar(value=self.config["demo_figures"])
    menu.add_command(label="Refresh now", command=self.refresh_now)
    menu.add_command(label="Connect Codex status…", command=self.connect_codex)
    dock_menu = tk.Menu(menu, tearoff=0)
    for name in DOCKS:
        dock_menu.add_command(label=name.replace("-", " ").title(),
                              command=lambda n=name: self.set_dock(n))
    menu.add_cascade(label="Dock", menu=dock_menu)
    # Theme lived only in the tray, which is where Windows hides icons it
    # decides are uninteresting. The console is what you are right-clicking.
    theme_menu = tk.Menu(menu, tearoff=0)
    for key in THEMES:
        theme_menu.add_command(
            label=THEMES[key]["label"] + (" ✓" if key == THEME_NAME else ""),
            command=lambda k=key: self.set_theme(k))
    menu.add_cascade(label="Theme", menu=theme_menu)
    zoom_menu = tk.Menu(menu, tearoff=0)
    for value in ZOOM_STEPS:
        zoom_menu.add_command(
            label="%d%%" % round(value * 100)
                  + (" ✓" if abs(ZOOM - value) < 0.01 else ""),
            command=lambda v=value: self.set_zoom(v))
    menu.add_cascade(label="Size", menu=zoom_menu)
    menu.add_checkbutton(label="Demo figures", command=self.toggle_demo,
                         variable=self._demo_var)
    menu.add_separator()
    menu.add_command(label="Tuck to edge", command=lambda: self.set_tuck(True))
    tuck_menu = tk.Menu(menu, tearoff=0)
    for edge in ('top', 'left', 'right'):
        tuck_menu.add_command(label=edge.title(), command=lambda edge=edge: self.set_tuck(True, side=edge))
    menu.add_cascade(label="Tuck position", menu=tuck_menu)
    menu.add_command(label="Hide console", command=self.toggle_bar)
    menu.add_command(label="Quit", command=self.quit)
    return menu

def popup_menu(widget, event):
    try:
        widget.menu.tk_popup(event.x_root, event.y_root)
    finally:
        widget.menu.grab_release()


def permission_dialog(agent, item):
    """Build a modal review with complete, selectable input and safe defaults."""
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    dialog = tk.Toplevel()
    dialog.title("Review Claude request")
    dialog.attributes("-topmost", True)
    dialog.geometry("680x440")
    dialog.minsize(440, 300)
    result = {"choice": None}

    def choose(choice=None):
        result["choice"] = choice
        dialog.destroy()

    request = item["request"]
    ttk.Label(dialog, text="Allow %s once?" % request["tool_name"],
              font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(16, 8))
    ttk.Label(dialog, text="%s\n%s" % (agent.get("name") or "Claude Code", agent.get("cwd") or "")).pack(anchor="w", padx=16)
    text = ScrolledText(dialog, wrap="word", font=("Consolas", 10), height=12)
    text.pack(fill="both", expand=True, padx=16, pady=12)
    details = {key: request[key] for key in ("tool_name", "input", "description", "decision_reason", "blocked_path") if key in request}
    text.insert("1.0", json.dumps(details, ensure_ascii=False, indent=2))
    text.configure(state="disabled")
    buttons = ttk.Frame(dialog)
    buttons.pack(fill="x", padx=16, pady=(0, 16))
    ttk.Button(buttons, text="Allow once", command=lambda: choose("allow")).pack(side="right")
    cancel = ttk.Button(buttons, text="Cancel", command=choose)
    cancel.pack(side="right", padx=8)
    ttk.Button(buttons, text="Deny", command=lambda: choose("deny")).pack(side="right")
    dialog.bind("<Escape>", lambda _event: choose())
    dialog.bind("<Return>", lambda _event: choose())
    dialog.protocol("WM_DELETE_WINDOW", choose)
    cancel.focus_set()
    return dialog, result


def review_permission(agent, item):
    dialog, result = permission_dialog(agent, item)
    dialog.grab_set()
    dialog.wait_window()
    return result["choice"]
