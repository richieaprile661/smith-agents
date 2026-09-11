"""AppKit shell and macOS services for the shared Pillow widget.

AppKit is imported only when creating the shell. Data and rendering tests do
not need a window server, Keychain access, or a running event loop.
"""
import errno
import fcntl
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from .session_windows import WindowTargets, title_matches_project
from . import macos_windows
from .branding import APP_NAME, config_override

RASTER_SCALE = 2.0  # render in Retina pixels; all native geometry uses points
# Keep startup and bundle identity stable across the public-name change.
LABEL = "io.github.richieaprile661.claude-usage-widget"
_instance_lock = None
_classes = None
_permission_target_class = None


def display_scale():
    return 192, RASTER_SCALE


def config_dir():
    # Preserve settings, account caches, and the existing single-instance lock.
    return str(Path.home() / "Library/Application Support/Claude Usage Widget")


def font_candidates(names):
    bold = any("bd" in name.lower() or "bold" in name.lower() for name in names)
    face = "Arial Bold.ttf" if bold else "Arial.ttf"
    return [str(Path("/System/Library/Fonts/Supplemental") / face),
            "/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"]


def read_credentials(path):
    """Read Claude's credential only; never refresh, copy, or log its token."""
    # A custom Claude config directory must not borrow the default account.
    default_dir = Path.home() / ".claude"
    if Path(path).parent == default_dir:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            if isinstance(payload, dict) and payload.get("claudeAiOauth", {}).get("accessToken"):
                return payload
        elif result.returncode != 44:  # 44 = item not found; cancellation stays cancelled
            raise OSError("Claude Keychain access was not granted")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def process_started(pid):
    import psutil
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def session_process_start(data, pid):
    """Capture Unix process identity and reject a PID reused after the session."""
    actual = process_started(pid)
    if actual is None:
        return 0
    session_started = float(data.get("startedAt") or 0) / 1000.0
    if session_started and actual > session_started + 5:
        return -1  # explicitly mismatched identity, not 'identity unavailable'
    return actual


def pid_alive(pid, started=None):
    import psutil
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if started and process.create_time() != started:
            return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def terminate_agent(agent):
    """The shared controller asks for confirmation before calling this."""
    import psutil
    pid, started = agent.get("pid"), agent.get("started_at")
    if agent.get("sub") or not started or not pid_alive(pid, started):
        return False
    try:
        process = psutil.Process(pid)
        if process.create_time() != started:
            return False
        if process.uids().real != os.getuid():
            return False
        command = process.cmdline()
        if not any("claude" in Path(arg).name.lower() or "/@anthropic-ai/claude-code/" in arg
                   for arg in command[:3]):
            return False
        # psutil verifies process identity again before delivering the signal.
        process.send_signal(signal.SIGTERM)
        return True
    except psutil.Error:
        return False


def vscode_session_url(agent, window_id=None):
    from urllib.parse import urlencode
    from uuid import UUID
    session = agent.get("parent") if agent.get("sub") else agent.get("id")
    try:
        UUID(session)
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(window_id, int) or isinstance(window_id, bool) or window_id <= 0:
        return None  # An unscoped link can open a copy in the wrong window.
    return "vscode://anthropic.claude-code/open?" + urlencode({"session": session, "windowId": window_id})


def vscode_window_id(ancestors, logs_root=None):
    """Match the live extension-host PID and start time to VS Code's window log."""
    from datetime import datetime
    import re
    import psutil
    hosts = {}
    for process in ancestors:
        try:
            if process.name() == "Code Helper (Plugin)":
                hosts[process.pid] = process.create_time()
        except psutil.Error:
            continue
    if not hosts:
        return None
    root = Path(logs_root or Path.home() / "Library/Application Support/Code/logs")
    matches = set()
    for path in root.glob("*/window*/exthost/exthost.log"):
        try:
            with path.open() as handle:
                header = handle.readline(512)
            match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) \[info\] Extension host with pid (\d+) started", header)
            if not match or int(match[2]) not in hosts:
                continue
            started = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S.%f").timestamp()
            if abs(started - hosts[int(match[2])]) > 10:
                continue  # Never match a historical process that reused this PID.
            window = path.parent.parent.name
            if re.fullmatch(r"window[1-9]\d*", window):
                matches.add(int(window[6:]))
        except (OSError, ValueError):
            continue
    return next(iter(matches)) if len(matches) == 1 else None


_AGENT_WINDOWS = WindowTargets()


def _agent_host_application(process):
    import AppKit
    for parent in [process, *process.parents()]:
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(parent.pid)
        if (app and app.bundleIdentifier() and parent.pid != os.getpid()
                and app.activationPolicy() == AppKit.NSApplicationActivationPolicyRegular):
            return app
    return None


def _match_agent_window(agent, host):
    if host is None or host.isTerminated():
        return None
    windows = macos_windows.windows(host.processIdentifier())
    leaf = os.path.basename(agent.get('cwd', '').rstrip('/'))
    matches = [window for window in windows
               if title_matches_project(window.get('AXTitle') or '', leaf)]
    # An ancestor identifies the app, but never identifies one of several
    # windows by itself. Duplicate project titles are deliberately ambiguous.
    if len(matches) == 1:
        return matches[0]
    if len(windows) == 1:
        return windows[0]
    return None


def raise_agent_window(agent):
    """Reveal one conversation/window; never activate the entire application."""
    import AppKit
    import Foundation
    import psutil
    if not pid_alive(agent.get("pid"), agent.get("started_at")):
        return False
    try:
        process = psutil.Process(agent["pid"])
        if agent.get("provider") == "codex" and process.create_time() != agent.get("process_created"):
            return False
        ancestors = process.parents()
        host = _agent_host_application(process)
        target = _opened_agent_window(agent)
        if target:
            host = target[0]
        window = target[1] if target else _match_agent_window(agent, host)
        opened = False
        if agent.get("entrypoint") == "claude-vscode":
            url = vscode_session_url(agent, vscode_window_id(ancestors))
            if url:
                configuration = AppKit.NSWorkspaceOpenConfiguration.configuration()
                configuration.setActivates_(False)
                # Deliver the scoped link without Launch Services bringing
                # every VS Code window forward as part of app activation.
                def completed(application, error):
                    if error is not None:
                        show_error('Could not open this VS Code session. Open its window manually.')
                AppKit.NSWorkspace.sharedWorkspace().openURL_configuration_completionHandler_(
                    Foundation.NSURL.URLWithString_(url), configuration, completed)
                opened = True
                if host is not None and window is None and not macos_windows.trusted():
                    macos_windows.request_access()
            # A title alone cannot reveal a particular conversation. Do not
            # substitute application activation if its scoped link is missing.
            if not opened:
                return False
        elif window is None and not macos_windows.trusted():
            macos_windows.request_access()
            raise PermissionError('To open or hide one terminal window, enable Accessibility '
                                  'for the widget (Python when running from source) in '
                                  'System Settings → Privacy & Security → Accessibility.')
        if window is not None:
            raised = window.raise_window()
            opened = opened or raised
            if opened:
                _AGENT_WINDOWS.remember(agent, (host, window))
        return opened
    except psutil.Error:
        pass
    return False


def _opened_agent_window(agent):
    target = _AGENT_WINDOWS.get(agent)
    if target is None:
        return None
    host, window = target
    if (host.isTerminated() or not pid_alive(agent.get('pid'), agent.get('started_at'))
            or window.get('AXMinimized') is None):
        _AGENT_WINDOWS.discard(agent)
        return None
    return target


def agent_window_is_visible(agent):
    target = _opened_agent_window(agent)
    return bool(target is not None and not target[0].isHidden()
                and target[1].get('AXMinimized') is False)


def agent_window_state(agent):
    """Observe the host window without raising it or requesting permissions."""
    import psutil
    if agent.get('sub') or agent.get('state') == 'closed' or not pid_alive(agent.get('pid'), agent.get('started_at')):
        return 'unknown'
    target = _opened_agent_window(agent)
    if target is None:
        try:
            process = psutil.Process(agent['pid'])
            if agent.get('provider') == 'codex' and process.create_time() != agent.get('process_created'):
                return 'unknown'
            host = _agent_host_application(process)
            window = _match_agent_window(agent, host)
        except psutil.Error:
            return 'unknown'
        if window is None:
            return 'unknown'
        target = (host, window)
        _AGENT_WINDOWS.remember(agent, target)
    host, window = target
    minimized = window.get('AXMinimized')
    if minimized is None:
        return 'unknown'
    if host.isHidden() or minimized:
        return 'hidden'
    if not host.isActive():
        return 'background'
    focused = macos_windows.is_focused(host.processIdentifier(), window)
    return 'unknown' if focused is None else 'front' if focused else 'background'


def hide_agent_window(agent):
    """Minimize only the recorded window, leaving Claude running."""
    target = _opened_agent_window(agent)
    if target is None:
        return False
    window = target[1]
    return window.get('AXMinimized') is True or window.set_bool('AXMinimized', True)


def permission_alert(agent, item):
    import AppKit as A
    global _permission_target_class
    if _permission_target_class is None:
        class WidgetPermissionTarget(A.NSObject):
            def choose_(self, sender):
                self.choice = {"Allow once": "allow", "Deny": "deny"}.get(str(sender.title()))
                self.clicked = True
                if self.modal:
                    A.NSApplication.sharedApplication().stopModal()
        _permission_target_class = WidgetPermissionTarget
    request = item["request"]
    alert = A.NSAlert.alloc().init()
    alert.setMessageText_("Allow %s once?" % request["tool_name"])
    alert.setInformativeText_("%s\n%s" % (agent.get("name") or "Claude Code", agent.get("cwd") or ""))
    allow = alert.addButtonWithTitle_("Allow once")
    cancel = alert.addButtonWithTitle_("Cancel")
    alert.addButtonWithTitle_("Deny")
    allow.setKeyEquivalent_("")
    cancel.setKeyEquivalent_("\r")
    scroll = A.NSScrollView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 560, 280))
    scroll.setHasVerticalScroller_(True)
    text = A.NSTextView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 540, 280))
    text.setEditable_(False)
    text.setSelectable_(True)
    text.setVerticallyResizable_(True)
    text.setFont_(A.NSFont.userFixedPitchFontOfSize_(12))
    details = {key: request[key] for key in ("tool_name", "input", "description", "decision_reason", "blocked_path") if key in request}
    text.setString_(json.dumps(details, ensure_ascii=False, indent=2))
    scroll.setDocumentView_(text)
    alert.setAccessoryView_(scroll)
    alert.layout()
    target = _permission_target_class.alloc().init()
    target.choice = None
    target.clicked = False
    target.modal = False
    for button in alert.buttons():
        button.setTarget_(target)
        button.setAction_("choose:")
    return alert, target


def review_permission(agent, item):
    import AppKit as A
    alert, target = permission_alert(agent, item)
    target.modal = True
    A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    alert.runModal()
    return target.choice


def tray_size():
    return 44  # a 22-point template image, with a 2x representation


def claim_single_instance(wait=0.0):
    global _instance_lock
    if _instance_lock is not None:
        return True
    directory = Path(config_override() or config_dir())
    directory.mkdir(parents=True, exist_ok=True)
    handle = os.open(directory / "instance.lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _instance_lock = handle
            return True
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                os.close(handle)
                raise
            if time.monotonic() >= deadline:
                os.close(handle)
                return False
            time.sleep(0.1)


def release_single_instance():
    global _instance_lock
    if _instance_lock is not None:
        # Keep the inode in place so another process cannot lock a replaced file.
        fcntl.flock(_instance_lock, fcntl.LOCK_UN)
        os.close(_instance_lock)
        _instance_lock = None


def launch_argv():
    if getattr(sys, "frozen", False):
        return [sys.executable], str(Path(sys.executable).parent)
    return [sys.executable, "-m", "smith_agents"], str(Path(__file__).resolve().parent.parent)


def relaunch(argv, cwd):
    return subprocess.Popen(argv, cwd=cwd, close_fds=True, start_new_session=True)


def startup_path():
    return Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def autostart_enabled():
    return startup_path().exists()


def set_autostart(enabled):
    path = startup_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    argv, cwd = launch_argv()
    payload = {"Label": LABEL, "ProgramArguments": argv, "WorkingDirectory": cwd,
               "RunAtLoad": True, "LimitLoadToSessionType": "Aqua"}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle)
    temporary.replace(path)


def show_error(message):
    import AppKit
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(APP_NAME)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_("OK")
    alert.runModal()


def _native_classes():
    global _classes
    if _classes is not None:
        return _classes
    import AppKit as A
    import Foundation as F
    import objc

    class WidgetPanel(A.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

    class WidgetView(A.NSView):
        def isFlipped(self):
            return True

        def acceptsFirstMouse_(self, event):
            return True

        def drawRect_(self, rect):
            image = getattr(self, "image", None)
            if image is not None:
                image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                    self.bounds(), A.NSZeroRect, A.NSCompositingOperationSourceOver,
                    1.0, True, None)

        @objc.python_method
        def event_data(self, event):
            point = self.convertPoint_fromView_(event.locationInWindow(), None)
            screen = self.window().convertPointToScreen_(event.locationInWindow())
            return SimpleNamespace(x=point.x * RASTER_SCALE, y=point.y * RASTER_SCALE,
                                   x_root=screen.x * RASTER_SCALE,
                                   y_root=(_desktop_top() - screen.y) * RASTER_SCALE,
                                   native=event)

        def mouseDown_(self, event):
            if event.modifierFlags() & A.NSEventModifierFlagControl:
                self.rightMouseDown_(event)
                return
            self.widget._on_press(self.event_data(event))

        def mouseDragged_(self, event):
            self.widget._on_drag(self.event_data(event))
            self.widget._repaint()

        def mouseUp_(self, event):
            self.widget._on_release(self.event_data(event))

        def scrollWheel_(self, event):
            data = self.event_data(event)
            from . import core
            factor = RASTER_SCALE if event.hasPreciseScrollingDeltas() else RASTER_SCALE * 12
            if self.widget.config.get('tucked'):
                dx, dy = event.scrollingDeltaX(), event.scrollingDeltaY()
                layout = self.widget._peek_layout
                over_rail = layout and layout.rail[0] <= data.x <= layout.rail[2] and layout.rail[1] <= data.y <= layout.rail[3]
                delta = dx if over_rail and layout.horizontal and abs(dx) > abs(dy) else dy
                self.widget._scroll_tucked_at(data, -delta * factor)
                return
            if data.y >= core.SHADOW_PAD + core.BAR_H + core.TAB_H:
                self.widget._scroll_agents(-event.scrollingDeltaY() * factor)

        def rightMouseDown_(self, event):
            self.widget._on_context(self.event_data(event))

    class ActionTarget(F.NSObject):
        def invoke_(self, sender):
            try:
                self.callback()
            except Exception:
                import traceback
                from .core import log_line
                log_line("menu action failed\n" + traceback.format_exc())

    class MenuDelegate(F.NSObject):
        def menuNeedsUpdate_(self, menu):
            self.tray.refresh_menu(menu)

    _classes = WidgetPanel, WidgetView, ActionTarget, MenuDelegate
    return _classes


def _desktop_top():
    import AppKit
    return AppKit.NSScreen.screens()[0].frame().size.height


def _native_image(image, template=False):
    import AppKit as A
    import Foundation as F
    stream = io.BytesIO()
    # These frames stay in memory. Uncompressed PNG avoids spending the
    # animation budget compressing an image AppKit immediately decodes.
    image.save(stream, format="PNG", compress_level=0)
    raw = stream.getvalue()
    native = A.NSImage.alloc().initWithData_(F.NSData.dataWithBytes_length_(raw, len(raw)))
    native.setSize_(A.NSMakeSize(image.width / RASTER_SCALE, image.height / RASTER_SCALE))
    native.setTemplate_(template)
    return native


class MacWindow:
    """Small event/surface adapter used by the common controller."""
    def __init__(self, widget):
        import AppKit as A
        import Foundation as F
        F.NSProcessInfo.processInfo().setProcessName_(APP_NAME)
        Panel, View, _Target, _Delegate = _native_classes()
        self.application = A.NSApplication.sharedApplication()
        self.application.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
        self.panel = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 320, 80),
            A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel,
            A.NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setTitle_(APP_NAME)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(A.NSColor.clearColor())
        self.panel.setHasShadow_(False)  # the shared renderer already draws it
        self.panel.setLevel_(A.NSFloatingWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(A.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                          A.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = View.alloc().initWithFrame_(A.NSMakeRect(0, 0, 320, 80))
        self.view.widget = widget
        self.panel.setContentView_(self.view)
        self.visible = widget.config["visible"]
        self.xy = (0, 0)
        self.timers = set()
        if self.visible:
            self.panel.orderFrontRegardless()

    def geometry(self, value):
        pass  # paint() updates native size and origin in one transaction

    def paint(self, image, x, y, opacity=1.0):
        import AppKit as A
        self.xy = (x, y)
        width, height = image.width / RASTER_SCALE, image.height / RASTER_SCALE
        self.panel.setFrame_display_(A.NSMakeRect(x / RASTER_SCALE,
            _desktop_top() - y / RASTER_SCALE - height, width, height), False)
        self.view.setFrame_(A.NSMakeRect(0, 0, width, height))
        self.view.image = _native_image(image)
        # A selected tucked panel leaves a large transparent area around the
        # strip. Let clicks there reach the desktop; repainting keeps hover
        # detection active even while this nonactivating window ignores input.
        layout = getattr(self.view.widget, '_peek_layout', None)
        ignore_mouse = False
        if self.view.widget.tuck_anim >= 1.0 and layout is not None and not self.view.widget._drag:
            point = A.NSEvent.mouseLocation()
            local_x = point.x * RASTER_SCALE - x
            local_y = (_desktop_top() - point.y) * RASTER_SCALE - y
            ignore_mouse = not any(rect and rect[0] <= local_x <= rect[2]
                                   and rect[1] <= local_y <= rect[3]
                                   for rect in (layout.rail, layout.panel))
        self.panel.setIgnoresMouseEvents_(ignore_mouse)
        self.panel.setAlphaValue_(opacity)
        self.view.setNeedsDisplay_(True)
        if self.visible:
            self.panel.orderFrontRegardless()

    def after(self, delay, callback):
        import Foundation as F
        def fire(timer):
            self.timers.discard(timer)
            callback()
        timer = F.NSTimer.timerWithTimeInterval_repeats_block_(max(delay / 1000, 0.001), False, fire)
        self.timers.add(timer)
        F.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, F.NSRunLoopCommonModes)
        return timer

    def after_cancel(self, timer):
        timer.invalidate()
        self.timers.discard(timer)

    def winfo_x(self):
        return self.xy[0]

    def winfo_y(self):
        return self.xy[1]

    def withdraw(self):
        self.visible = False
        self.panel.orderOut_(None)

    def deiconify(self):
        self.visible = True
        self.panel.orderFrontRegardless()

    def mainloop(self):
        from PyObjCTools import AppHelper
        AppHelper.runEventLoop()

    def destroy(self):
        import AppKit as A
        for timer in list(self.timers):
            timer.invalidate()
        self.timers.clear()
        self.panel.orderOut_(None)
        # Return through Python's finally blocks so the lock and log close.
        # terminate_ exits the process directly and bypasses that cleanup.
        self.application.stop_(None)
        wake = A.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            A.NSEventTypeApplicationDefined, A.NSZeroPoint, 0, 0, 0, None, 0, 0, 0)
        self.application.postEvent_atStart_(wake, True)


def screen_bounds(root, position=None):
    """Usable screen in shared top-left, Retina-pixel coordinates.

    visibleFrame excludes the menu bar/notch and Dock. Free placement follows
    the monitor containing the widget and recovers if that monitor disappears.
    """
    import AppKit as A
    screens = list(A.NSScreen.screens())
    selected = root.panel.screen() or screens[0]
    if position is not None:
        point = A.NSMakePoint((position[0] + 80) / RASTER_SCALE,
                              _desktop_top() - (position[1] + 80) / RASTER_SCALE)
        selected = next((s for s in screens if A.NSPointInRect(point, s.frame())), selected)
    frame = selected.visibleFrame()
    return (round(frame.origin.x * RASTER_SCALE),
            round((_desktop_top() - frame.origin.y - frame.size.height) * RASTER_SCALE),
            round(frame.size.width * RASTER_SCALE), round(frame.size.height * RASTER_SCALE))


def create_shell(widget, core):
    root = MacWindow(widget)
    return root, root


class MacTray:
    def __init__(self, widget):
        import AppKit as A
        self.widget = widget
        self.item = A.NSStatusBar.systemStatusBar().statusItemWithLength_(A.NSVariableStatusItemLength)
        self.menu = A.NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        _Panel, _View, _Target, Delegate = _native_classes()
        self.delegate = Delegate.alloc().init()
        self.delegate.tray = self
        self.menu.setDelegate_(self.delegate)
        self.item.setMenu_(self.menu)
        self.targets = []
        self._icon = None
        self._title = APP_NAME
        self.refresh_menu(self.menu)

    @property
    def icon(self):
        return self._icon

    @icon.setter
    def icon(self, image):
        from PIL import Image
        from .core import render_tray
        # Keep the project's figure mark. AppKit tints its alpha for light/dark
        # mode; an explicit ! preserves the alert even in a monochrome menu bar.
        if image is None:
            image = render_tray(None, False, None)
        symbol = Image.new("RGBA", image.size, "black")
        symbol.putalpha(image.getchannel("A"))
        self._icon = image
        self.item.button().setImage_(_native_image(symbol, template=True))
        alert = self.widget.error or any(m["pct"] >= 90 for m in self.widget.metrics)
        self.item.button().setTitle_("!" if alert else "")

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self._title = value
        self.item.button().setToolTip_(value)

    def refresh_menu(self, menu):
        import AppKit as A
        from . import core as c
        import webbrowser
        _Panel, _View, Target, _Delegate = _native_classes()
        menu.removeAllItems()
        self.targets = []

        def item(parent, label, callback=None, checked=None):
            entry = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, None, "")
            if callback:
                target = Target.alloc().init()
                target.callback = callback
                self.targets.append(target)
                entry.setTarget_(target)
                entry.setAction_("invoke:")
            if checked is not None:
                entry.setState_(A.NSControlStateValueOn if checked else A.NSControlStateValueOff)
            parent.addItem_(entry)
            return entry

        def submenu(label, options):
            entry = item(menu, label)
            child = A.NSMenu.alloc().initWithTitle_(label)
            child.setAutoenablesItems_(False)
            for text, callback, checked in options:
                item(child, text, callback, checked)
            entry.setSubmenu_(child)

        w = self.widget
        if w.demo:
            item(menu, "Demo — sample data").setEnabled_(False)
        item(menu, "Refresh now", w.refresh_now)
        item(menu, "Show console", w.toggle_bar, w.config["visible"])
        item(menu, "Tuck to edge", w.toggle_tuck, w.config["tucked"])
        submenu("Tuck position", [(edge.title(), lambda edge=edge: w.set_tuck(True, side=edge),
                                    w.config['tucked'] and w.config['tuck_side'] == edge)
                                   for edge in ('top', 'left', 'right')])
        submenu("Dock", [(d.replace("-", " ").title(), lambda d=d: w.set_dock(d),
                           w.config["dock"] == d) for d in c.DOCKS])
        submenu("Opacity", [("%d%%" % (v * 100), lambda v=v: w.set_opacity(v),
                              abs(w.config["opacity"] - v) < 0.01) for v in (1., .94, .85, .7)])
        submenu("Size", [("%d%%" % round(v * 100), lambda v=v: w.set_zoom(v),
                           abs(c.ZOOM - v) < 0.01) for v in c.ZOOM_STEPS])
        submenu("Theme", [(c.THEMES[k]["label"], lambda k=k: w.set_theme(k),
                            c.THEME_NAME == k) for k in c.THEMES])
        item(menu, "Demo figures", w.toggle_demo, w.config["demo_figures"])
        menu.addItem_(A.NSMenuItem.separatorItem())
        item(menu, "Start at login", w.toggle_autostart, autostart_enabled())
        item(menu, "Connect Claude Code…", w.connect_claude)
        item(menu, "Connect Codex status…", w.connect_codex)
        item(menu, "About usage limits", lambda: webbrowser.open(c.USAGE_HELP_URL))
        menu.addItem_(A.NSMenuItem.separatorItem())
        item(menu, "Quit", w.quit)

    def stop(self):
        import AppKit as A
        A.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)


def build_context_menu(widget):
    return None  # the context menu shares the menu-bar actions


def build_tray(widget):
    return MacTray(widget)


def start_tray(tray):
    # AppKit's status item runs on the same main loop as the floating panel.
    tray.icon = None


def popup_menu(widget, event):
    import AppKit as A
    widget.tray.refresh_menu(widget.tray.menu)
    A.NSMenu.popUpContextMenu_withEvent_forView_(widget.tray.menu, event.native, widget.root.view)
