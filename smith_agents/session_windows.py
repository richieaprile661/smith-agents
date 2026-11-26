"""Resolve session owners and retain bounded, verified host window targets."""
from collections import OrderedDict
import re
import threading


# Providers whose sessions live inside a host this widget does not own: their
# own app ends them, their readings come from their own parser, and a row is
# trusted only while its process is the one that was scanned.
HOSTED_PROVIDERS = ("codex", "hermes", "deepseek")


def window_agent(agent, agents):
    """A spawned subagent uses its ancestor's window, not a separate chat."""
    seen = set()
    while agent and agent.get('sub'):
        key = (agent.get('provider'), agent.get('id'))
        if key in seen:
            return None
        seen.add(key)
        agent = next((parent for parent in agents
                      if parent.get('id') == agent.get('parent')
                      and parent.get('provider') == agent.get('provider')), None)
    return agent


def claude_desktop_session_url(agent):
    """The Claude desktop app's own link to one of its local sessions."""
    session = agent.get("host_session")
    if not isinstance(session, str) or not re.fullmatch(
            r"local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", session):
        return None  # Never build a link from anything but a plain session id.
    return "claude://claude.ai/epitaxy/" + session


def codex_desktop_thread_url(agent):
    """The ChatGPT app's own link to one of its Codex threads."""
    from uuid import UUID
    thread = agent.get("session_id")
    try:
        if not isinstance(thread, str) or str(UUID(thread)) != thread.lower():
            return None
    except ValueError:
        return None
    return "codex://threads/" + thread


def desktop_app_url(agent):
    """(link, app name) for a session that lives in a desktop app's one window.

    The window's title never names a session; only the app's own link shows
    one. The name alone, with no link, still says whose window it is."""
    entrypoint = agent.get("entrypoint")
    if entrypoint == "claude-desktop":
        return claude_desktop_session_url(agent), 'Claude'
    if entrypoint == "codex-desktop":
        return codex_desktop_thread_url(agent), 'ChatGPT'
    if agent.get("provider") == "deepseek":
        # DeepSeek Harness has no link to one session; this shows its window.
        return "dsh://open", 'DeepSeek'
    return None, None


def title_matches_project(title, leaf):
    """Avoid matching project 'app' inside an unrelated project 'myapp'."""
    return bool(leaf and re.search(r'(?<![\w.-])' + re.escape(leaf) + r'(?![\w.-])',
                                  title, re.IGNORECASE))


class WindowTargets:
    def __init__(self, limit=128):
        self.limit = limit
        self.lock = threading.Lock()
        self.targets = OrderedDict()

    @staticmethod
    def key(agent):
        return (agent.get('id'), agent.get('pid'), agent.get('started_at'))

    def remember(self, agent, target):
        key = self.key(agent)
        with self.lock:
            self.targets[key] = target
            self.targets.move_to_end(key)
            while len(self.targets) > self.limit:
                self.targets.popitem(last=False)

    def get(self, agent):
        with self.lock:
            return self.targets.get(self.key(agent))

    def discard(self, agent):
        with self.lock:
            self.targets.pop(self.key(agent), None)
