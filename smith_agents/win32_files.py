"""The disk files a Windows process has open, without psutil's open_files().

psutil 7.2.2 names every handle in a new thread and kills that thread with
TerminateThread when it takes over 100 ms. A handle on an idle pipe always
does - Codex's app-server talks to VS Code over one - and a thread killed
while it is starting up, or holding a heap lock, leaves the loader lock
owned by nobody (every later thread start or exit hangs: the widget freezes)
or memory freed under it (the widget crashes). psutil fixed this in #2894,
which is not in a release yet.

Nothing here runs in a thread of its own or is ever killed. Only handles of
the File object type are looked at, and of those only disk files are named:
asking a pipe for its type does not wait on a read that is pending on it, so
the idle pipe is passed over rather than queried. The type query can wait on
a network file system, which is why callers keep this off the UI thread.
"""
import ctypes
from ctypes import wintypes
from functools import lru_cache
import msvcrt
import os
import tempfile

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x2
FILE_TYPE_DISK = 1
PROCESS_HANDLE_INFORMATION = 51                     # Windows 8 and later
# NTSTATUS values arrive as signed 32-bit integers.
STATUS_INFO_LENGTH_MISMATCH = -0x3FFFFFFC           # 0xC0000004
STATUS_BUFFER_TOO_SMALL = -0x3FFFFFDD               # 0xC0000023
MAX_SNAPSHOT = 64 * 1024 * 1024


class _Entry(ctypes.Structure):
    """PROCESS_HANDLE_TABLE_ENTRY_INFO."""
    _fields_ = [("handle", wintypes.HANDLE), ("handle_count", ctypes.c_size_t),
                ("pointer_count", ctypes.c_size_t), ("access", wintypes.DWORD),
                ("type_index", wintypes.ULONG), ("attributes", wintypes.ULONG),
                ("reserved", wintypes.ULONG)]


# PROCESS_HANDLE_SNAPSHOT_INFORMATION: two pointer-sized counts, then entries.
_HEADER = 2 * ctypes.sizeof(ctypes.c_size_t)


@lru_cache(maxsize=1)
def _api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    signatures = (
        (kernel32, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (kernel32, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        (kernel32, "GetCurrentProcess", [], wintypes.HANDLE),
        (kernel32, "DuplicateHandle", [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                       wintypes.BOOL, wintypes.DWORD], wintypes.BOOL),
        (kernel32, "GetFileType", [wintypes.HANDLE], wintypes.DWORD),
        (kernel32, "GetFinalPathNameByHandleW", [wintypes.HANDLE, wintypes.LPWSTR,
                                                 wintypes.DWORD, wintypes.DWORD], wintypes.DWORD),
        (ntdll, "NtQueryInformationProcess", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)],
         ctypes.c_long),
    )
    for library, name, args, result in signatures:
        function = getattr(library, name)
        function.argtypes, function.restype = args, result
    return kernel32, ntdll


def _snapshot(process):
    """(handle value, object type index) for every handle the process holds."""
    _, ntdll = _api()
    size = 64 * 1024
    while True:
        buffer = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG()
        status = ntdll.NtQueryInformationProcess(process, PROCESS_HANDLE_INFORMATION,
                                                 buffer, size, ctypes.byref(needed))
        if status in (STATUS_INFO_LENGTH_MISMATCH, STATUS_BUFFER_TOO_SMALL) and size < MAX_SNAPSHOT:
            size = max(size * 2, needed.value + 4096)
            continue
        if status < 0:
            raise OSError("could not list the process's handles (NTSTATUS %#x)" % (status & 0xFFFFFFFF))
        count = ctypes.c_size_t.from_buffer(buffer).value
        count = min(count, (size - _HEADER) // ctypes.sizeof(_Entry))
        entries = (_Entry * count).from_buffer(buffer, _HEADER)
        return [(entry.handle, entry.type_index) for entry in entries]


@lru_cache(maxsize=1)
def file_type_index():
    """The File object type's index, read off a file this process opens.

    Type indexes are fixed for the life of the system, so one lookup does."""
    kernel32, _ = _api()
    with tempfile.TemporaryFile() as probe:
        own = msvcrt.get_osfhandle(probe.fileno())
        for handle, index in _snapshot(kernel32.GetCurrentProcess()):
            if handle == own:
                return index
    raise OSError("could not find the File object type")


def _path(kernel32, handle):
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0)
        if not length:
            return None
        if length < size:
            break
        size = length + 1
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def open_files(pid):
    """Paths of the disk files the process has open; raises OSError when it
    cannot be read, so the caller can tell "none" from "unknown"."""
    kernel32, _ = _api()
    wanted = file_type_index()
    process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_DUP_HANDLE, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    found = []
    try:
        current = kernel32.GetCurrentProcess()
        for handle, index in _snapshot(process):
            if index != wanted or not handle:
                continue
            copy = wintypes.HANDLE()
            if not kernel32.DuplicateHandle(process, handle, current, ctypes.byref(copy),
                                            0, False, DUPLICATE_SAME_ACCESS):
                continue
            try:
                if kernel32.GetFileType(copy) != FILE_TYPE_DISK:
                    continue
                path = _path(kernel32, copy)
                if path:
                    found.append(path)
            finally:
                kernel32.CloseHandle(copy)
    finally:
        kernel32.CloseHandle(process)
    # Like psutil: each file once, and only files (a working directory is
    # an open handle too).
    return [path for path in dict.fromkeys(found) if os.path.isfile(path)]
