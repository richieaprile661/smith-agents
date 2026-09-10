"""Select the operating-system implementation without importing another GUI toolkit."""
import importlib
import sys
from functools import lru_cache
from .branding import APP_NAME

@lru_cache(maxsize=1)
def backend():
    if sys.platform == "win32":
        name = "platform_win32"
    elif sys.platform == "darwin":
        name = "platform_darwin"
    else:
        raise RuntimeError(APP_NAME + " supports Windows and macOS.")
    return importlib.import_module("." + name, __package__)
