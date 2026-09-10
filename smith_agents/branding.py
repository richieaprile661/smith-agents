"""Public names, separate from stable settings and integration identifiers."""

APP_NAME = "Smith Agents"
WORDMARK_TITLE = "Smith"
WORDMARK_SUBTITLE = "agents"
COMMAND = "smith-agents"
LEGACY_COMMAND = "claude-widget"


def config_override():
    """Accept the previous development override during existing installations' upgrades."""
    import os
    return os.environ.get("SMITH_AGENTS_CONFIG_DIR") or os.environ.get("CLAUDE_WIDGET_CONFIG_DIR")
