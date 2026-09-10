"""Bounded window targets for sessions opened through the widget."""
from collections import OrderedDict
import re


def title_matches_project(title, leaf):
    """Avoid matching project 'app' inside an unrelated project 'myapp'."""
    return bool(leaf and re.search(r'(?<![\w.-])' + re.escape(leaf) + r'(?![\w.-])',
                                  title, re.IGNORECASE))


class WindowTargets:
    def __init__(self, limit=128):
        self.limit = limit
        self.targets = OrderedDict()

    @staticmethod
    def key(agent):
        return (agent.get('id'), agent.get('pid'), agent.get('started_at'))

    def remember(self, agent, target):
        key = self.key(agent)
        self.targets[key] = target
        self.targets.move_to_end(key)
        while len(self.targets) > self.limit:
            self.targets.popitem(last=False)

    def get(self, agent):
        return self.targets.get(self.key(agent))

    def discard(self, agent):
        self.targets.pop(self.key(agent), None)
