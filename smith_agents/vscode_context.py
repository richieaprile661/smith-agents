"""Pass Claude's VS Code protocol through and retain only reported capacities."""
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import threading

from .context_bridge import _api_model, capture


class CapacityStream:
    MAX_LINE = 8 * 1024 * 1024

    def __init__(self, directory, on_record=None):
        self.directory = directory
        self.on_record = on_record
        self.buffer = bytearray()
        self.skipping = False
        self.models = {}

    def feed(self, chunk):
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not self.skipping:
                    try:
                        data = json.loads(self.buffer)
                        if self.on_record:
                            try:
                                self.on_record(data)
                            except (OSError, ValueError, TypeError, AttributeError):
                                pass  # Capacity still works when the local relay is unavailable.
                        self.record(data)
                    except (ValueError, TypeError, AttributeError, OSError):
                        pass  # Observation must never interrupt the VS Code protocol.
                self.buffer.clear()
                self.skipping = False
            if not self.skipping:
                if len(self.buffer) + len(part) > self.MAX_LINE:
                    self.buffer.clear()
                    self.skipping = True
                else:
                    self.buffer.extend(part)

    def record(self, data):
        if not isinstance(data, dict) or data.get("parent_tool_use_id") is not None:
            return  # A child response must not replace its parent's capacity.
        session = data.get("session_id")
        if not isinstance(session, str) or not session:
            return
        kind = data.get("type")
        model = None
        if kind == "system" and data.get("subtype") == "init":
            model = data.get("model")
            self.models.pop(session, None)
        elif kind == "assistant":
            model = (data.get("message") or {}).get("model")
        if isinstance(model, str) and model and model != "<synthetic>":
            self.models[session] = model
            if len(self.models) > 32:
                del self.models[next(iter(self.models))]
        if kind != "result":
            return
        model = self.models.get(session)
        usage = data.get("modelUsage")
        if not model or not isinstance(usage, dict):
            return
        matches = [(name, item) for name, item in usage.items()
                   if isinstance(name, str) and _api_model(name) == _api_model(model)
                   and isinstance(item, dict)]
        if len(matches) != 1:
            return
        name, item = matches[0]
        capture({"session_id": session, "model": {"id": name},
                 "context_window": {"context_window_size": item.get("contextWindow")}},
                self.directory, "main")


def input_chunks(fd, stopped):
    """Read protocol bytes without holding sys.stdin's BufferedReader lock.

    Poll pipes so shutdown does not depend on the client sending EOF. Windows
    select() only accepts sockets, so anonymous pipes use PeekNamedPipe there.
    Plain redirected files can be read directly. A raw console read may remain
    blocked, but cannot hold a Python buffered-I/O lock during finalization.
    """
    pipe_handle = None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFileType.restype = wintypes.DWORD
        kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                        ctypes.POINTER(wintypes.DWORD)]
        kernel.PeekNamedPipe.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(fd)
        if kernel.GetFileType(handle) == 3:  # FILE_TYPE_PIPE, including anonymous pipes
            pipe_handle = handle
    while not stopped.is_set():
        size = 65536
        if pipe_handle is not None:
            available = wintypes.DWORD()
            if not kernel.PeekNamedPipe(pipe_handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error in (109, 232):  # ERROR_BROKEN_PIPE / ERROR_NO_DATA: client closed
                    return
                raise ctypes.WinError(error)
            if not available.value:
                stopped.wait(.05)
                continue
            size = min(size, available.value)
        elif sys.platform != "win32":
            if not select.select([fd], [], [], .05)[0]:
                continue
        raw = os.read(fd, size)
        if not raw or stopped.is_set():
            return
        yield raw


def input_lines(chunks, limit):
    """Match bounded readline framing, including partial lines at EOF."""
    pending = bytearray()
    for chunk in chunks:
        pending.extend(chunk)
        while pending:
            newline = pending.find(b"\n", 0, limit)
            if newline >= 0:
                length = newline + 1
            elif len(pending) >= limit:
                length = limit
            else:
                break
            yield bytes(pending[:length])
            del pending[:length]
    if pending:
        yield bytes(pending)


def main(argv):
    if sys.platform not in ("darwin", "win32"):
        print("The VS Code widget launcher supports macOS and Windows.", file=sys.stderr)
        return 2
    if not argv:
        print("Expected the Claude executable and its arguments.", file=sys.stderr)
        return 2
    if sys.platform == "win32":
        # Preserve UTF-8 protocol bytes and LF across the Windows CRT streams.
        import msvcrt
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            msvcrt.setmode(stream.fileno(), os.O_BINARY)
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "widget-context"
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                                 creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except OSError as exc:
        print("Could not launch Claude: %s" % exc, file=sys.stderr)
        return 127

    from .permissions import PermissionRelay
    from .claude_activity import TaskStream
    import sqlite3
    try:
        import psutil
        owner = {"pid": child.pid, "started": psutil.Process(child.pid).create_time()}
    except ImportError:
        owner = None
    except psutil.Error:
        owner = None
    activity = TaskStream(directory, owner)
    output_lock = threading.Lock()

    def send_child(raw):
        child.stdin.write(raw)
        child.stdin.flush()

    def send_client(raw):
        with output_lock:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    relay = PermissionRelay(directory, send_child, send_client)
    def observe(data):
        try:
            activity.record(data)
        except (OSError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error):
            pass  # An unavailable observer cannot interfere with Claude.
        relay.observe(data)

    observer = CapacityStream(directory, on_record=observe)

    input_stopped = threading.Event()
    input_fd = sys.stdin.fileno()

    def forward_stdin():
        try:
            chunks = input_chunks(input_fd, input_stopped)
            for raw in input_lines(chunks, CapacityStream.MAX_LINE + 1):
                if input_stopped.is_set():
                    break
                relay.forward_input(raw)
        except (OSError, ValueError):
            pass
        finally:
            with relay.lock:
                try:
                    child.stdin.close()
                except OSError:
                    pass  # Claude can exit before a buffered input write finishes.

    input_thread = threading.Thread(target=forward_stdin, name="vscode-input", daemon=True)
    input_thread.start()

    def forward_signal(number, _frame):
        try:
            child.send_signal(number)
        except ProcessLookupError:
            pass

    previous = {}
    for number in (signal.SIGTERM, signal.SIGINT) + ((signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ()):
        previous[number] = signal.signal(number, forward_signal)
    try:
        while chunk := child.stdout.read1(65536):
            send_client(chunk)
            observer.feed(chunk)
        # Accept a final JSON record without a trailing newline.
        observer.feed(b"\n")
        code = child.wait()
        return code if code >= 0 else 128 - code
    except BrokenPipeError:
        child.terminate()
        return 1
    finally:
        input_stopped.set()
        # End the child before joining/closing the relay: its exit also releases
        # any input writer blocked on a full child pipe.
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        input_thread.join(timeout=1)
        activity.close()
        relay.close()
        try:
            child.stdin.close()
        except OSError:
            pass
        child.stdout.close()
        for number, handler in previous.items():
            signal.signal(number, handler)
