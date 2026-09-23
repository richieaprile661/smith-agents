"""The dashboard the widget's "Open dashboard" action opens.

One operation, `launch`, is shared by the footer button, the tray menu and the
context menu, so every entry point starts the same single server and opens the
same page. Call it off the UI thread: starting a listener is blocking work.
Showing the page is the platform's job, in the widget's own dashboard window.
"""
from .service import SERVICE

__all__ = ['SERVICE', 'DashboardError', 'launch', 'shutdown']


class DashboardError(Exception):
    """Something the person can act on, phrased for them."""


def launch(project_path=None):
    """Return the dashboard's address, starting the local server on the first
    call. The caller shows it with `platform.open_dashboard_window`.

    `project_path` is the workspace to select. It is turned into an opaque id
    here, and the path itself stays in this process. An unknown or deleted
    workspace is not an error: the page falls back to the most active one and
    keeps its project picker.
    """
    try:
        project = SERVICE.remember(project_path) if project_path else None
        url = SERVICE.url(project)
    except OSError as error:
        raise DashboardError(
            'Could not start the local dashboard server. %s' % error) from error
    return url


def shutdown():
    """Close the listener and release its port. Safe when never started."""
    SERVICE.stop()
