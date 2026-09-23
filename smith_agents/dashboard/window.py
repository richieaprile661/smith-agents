"""The dashboard's own window on Windows: a WebView2 view, like the macOS one.

It runs in its own process, because the widget's main thread belongs to Tk and
a WebView2 window needs a UI thread of its own. The widget writes the address
on this process's stdin, one line per "Open dashboard"; the key therefore
never appears on a command line. Each later line brings the window forward and
switches it to that address. When the widget exits, stdin closes and the
window goes with it.
"""
import json
import sys

from ..branding import APP_NAME

TITLE = "%s Dashboard" % APP_NAME
# The page's own background, so opening never flashes white.
BACKGROUND = "#161a1e"


def follow(window, lines, current):
    """Apply every later address to the open window; close it on end of input."""
    for line in lines:
        url = line.strip()
        if not url:
            continue
        if url != current:
            current = url
            # Same server, another project. A fragment change alone does not
            # reload, and the page reads its key and project when it loads.
            window.evaluate_js("location.replace(%s); location.reload();" % json.dumps(url))
        bring_forward(window)
    window.destroy()


def bring_forward(window):
    window.restore()
    window.show()
    # WebView2 windows have no "activate"; a brief always-on-top raises it.
    window.on_top = True
    window.on_top = False


def main(stdin=sys.stdin):
    url = stdin.readline().strip()
    if not url:
        return 2
    try:
        import webview
        window = webview.create_window(TITLE, url, width=1280, height=860,
                                       min_size=(720, 560), background_color=BACKGROUND)
        webview.start(follow, (window, stdin, url), gui="edgechromium")
    except Exception as error:                  # no WebView2: a browser still shows it
        from ..core import log_line
        from ..platform_win32 import open_in_browser
        log_line("dashboard webview failed: %s: %s" % (type(error).__name__, error))
        open_in_browser(url)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
