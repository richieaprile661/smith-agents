"""Small Accessibility bridge for individual windows, with owned CF references.

No application activation, hide, close, or process termination is used here.
The framework is loaded lazily so shared tests also run on Windows.
"""
import ctypes as C
from functools import lru_cache


@lru_cache(maxsize=1)
def _api():
    ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ptr = C.c_void_p
    signatures = (
        (ax, 'AXIsProcessTrusted', [], C.c_bool),
        (ax, 'AXUIElementCreateApplication', [C.c_int], ptr),
        (ax, 'AXUIElementCopyAttributeValue', [ptr, ptr, C.POINTER(ptr)], C.c_int),
        (ax, 'AXUIElementSetAttributeValue', [ptr, ptr, ptr], C.c_int),
        (ax, 'AXUIElementPerformAction', [ptr, ptr], C.c_int),
        (ax, 'AXUIElementSetMessagingTimeout', [ptr, C.c_float], C.c_int),
        (cf, 'CFRetain', [ptr], ptr),
        (cf, 'CFRelease', [ptr], None),
        (cf, 'CFGetTypeID', [ptr], C.c_ulong),
        (cf, 'CFStringGetTypeID', [], C.c_ulong),
        (cf, 'CFArrayGetTypeID', [], C.c_ulong),
        (cf, 'CFBooleanGetTypeID', [], C.c_ulong),
        (cf, 'CFStringCreateWithCString', [ptr, C.c_char_p, C.c_uint32], ptr),
        (cf, 'CFStringGetLength', [ptr], C.c_long),
        (cf, 'CFStringGetMaximumSizeForEncoding', [C.c_long, C.c_uint32], C.c_long),
        (cf, 'CFStringGetCString', [ptr, C.c_char_p, C.c_long, C.c_uint32], C.c_bool),
        (cf, 'CFArrayGetCount', [ptr], C.c_long),
        (cf, 'CFArrayGetValueAtIndex', [ptr, C.c_long], ptr),
        (cf, 'CFBooleanGetValue', [ptr], C.c_bool),
    )
    for lib, name, args, result in signatures:
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    return ax, cf


def trusted():
    return bool(_api()[0].AXIsProcessTrusted())


@lru_cache(maxsize=1)
def request_access():
    """Let macOS present its permission prompt once, after an Open click."""
    ax, cf = _api()
    ptr = C.c_void_p
    cf.CFDictionaryCreate.argtypes = [ptr, C.POINTER(ptr), C.POINTER(ptr), C.c_long, ptr, ptr]
    cf.CFDictionaryCreate.restype = ptr
    ax.AXIsProcessTrustedWithOptions.argtypes = [ptr]
    ax.AXIsProcessTrustedWithOptions.restype = C.c_bool
    key = ptr.in_dll(ax, 'kAXTrustedCheckOptionPrompt')
    value = ptr.in_dll(cf, 'kCFBooleanTrue')
    options = cf.CFDictionaryCreate(None, C.byref(key), C.byref(value), 1, None, None)
    try:
        ax.AXIsProcessTrustedWithOptions(options)
    finally:
        cf.CFRelease(options)


class Element:
    def __init__(self, value):
        self.value = value
        self.ax, self.cf = _api()

    def __del__(self):
        if self.value:
            self.cf.CFRelease(self.value)

    def _string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)

    def get(self, attribute):
        key = self._string(attribute)
        value = C.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(self.value, key, C.byref(value))
        finally:
            self.cf.CFRelease(key)
        if error or not value.value:
            return None
        try:
            kind = self.cf.CFGetTypeID(value)
            if kind == self.cf.CFBooleanGetTypeID():
                return bool(self.cf.CFBooleanGetValue(value))
            if kind == self.cf.CFStringGetTypeID():
                size = self.cf.CFStringGetMaximumSizeForEncoding(
                    self.cf.CFStringGetLength(value), 0x08000100) + 1
                buffer = C.create_string_buffer(size)
                if self.cf.CFStringGetCString(value, buffer, size, 0x08000100):
                    return buffer.value.decode('utf-8')
            if kind == self.cf.CFArrayGetTypeID():
                return [Element(self.cf.CFRetain(self.cf.CFArrayGetValueAtIndex(value, i)))
                        for i in range(self.cf.CFArrayGetCount(value))]
        finally:
            self.cf.CFRelease(value)
        return None

    def set_bool(self, attribute, value):
        key = self._string(attribute)
        boolean = C.c_void_p.in_dll(self.cf, 'kCFBooleanTrue' if value else 'kCFBooleanFalse')
        try:
            return self.ax.AXUIElementSetAttributeValue(self.value, key, boolean) == 0
        finally:
            self.cf.CFRelease(key)

    def raise_window(self):
        if not self.set_bool('AXMinimized', False):
            return False
        action = self._string('AXRaise')
        try:
            return self.ax.AXUIElementPerformAction(self.value, action) == 0
        finally:
            self.cf.CFRelease(action)


def windows(pid):
    if not trusted():
        return []
    ax, _ = _api()
    app = Element(ax.AXUIElementCreateApplication(pid))
    ax.AXUIElementSetMessagingTimeout(app.value, 0.3)
    return app.get('AXWindows') or []
