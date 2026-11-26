"""The DeepSeek account balance behind DeepSeek Harness.

The desktop app keeps its API key in its own credential file. That key is read
here, sent only to DeepSeek's documented balance endpoint, and never stored,
logged, or returned: only currency and balance figures leave this module. The
app's sign-in grant in the same file is not read.
"""
import json
import math
from pathlib import Path
import re
import urllib.error
import urllib.request

from .deepseek_sessions import home

BALANCE_URL = "https://api.deepseek.com/user/balance"
READ_TIMEOUT = 15
MAX_FILE = 64 * 1024
MAX_REPLY = 64 * 1024
_KEY = re.compile(r"sk-[A-Za-z0-9]{16,128}")
_CURRENCY = re.compile(r"[A-Z]{3,8}")
_SYMBOLS = {"USD": "$", "CNY": "¥"}


class Unavailable(Exception):
    pass


def api_key(root=None):
    """The key the app refers to as DEEPSEEK_API_KEY, or None.

    The file is a small YAML document of a known shape; only a plain key
    directly under the top-level ``refs`` is accepted, so nothing else in the
    file - the sign-in grant least of all - can be mistaken for it."""
    path = (Path(root) if root is not None else home()) / ".credentials.yaml"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE + 1)
    except OSError:
        return None
    if len(raw) > MAX_FILE:
        return None
    section, child_indent = None, None
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            section, child_indent = line.split(":", 1)[0].strip(), None
            continue
        if section != "refs":
            continue
        child_indent = indent if child_indent is None else child_indent
        name, colon, value = line.strip().partition(":")
        if indent != child_indent or not colon or name != "DEEPSEEK_API_KEY":
            continue
        value = re.sub(r"\s+#.*$", "", value.strip())
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value if _KEY.fullmatch(value) else None
    return None


def _amount(value):
    """A balance as DeepSeek prints it: a decimal string, sometimes a number."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, str) and not re.fullmatch(r"\s*-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*", value):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def reading(payload):
    """Keep the figures and nothing else from a balance reply."""
    infos = payload.get("balance_infos") if isinstance(payload, dict) else None
    wallets = []
    for info in infos[:16] if isinstance(infos, list) else ():
        if not isinstance(info, dict):
            continue
        currency, left = info.get("currency"), _amount(info.get("total_balance"))
        if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency) or left is None:
            continue
        wallets.append({"currency": currency, "left": left,
                        "granted_left": _amount(info.get("granted_balance")),
                        "topup_left": _amount(info.get("topped_up_balance"))})
    if not wallets:
        raise Unavailable("DeepSeek balance unavailable · retrying")
    first = next((wallet for wallet in wallets if wallet["left"] > 0), wallets[0])
    available = payload.get("is_available")
    return dict(first, wallets=wallets,
                available=available if isinstance(available, bool) else first["left"] > 0)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key to wherever the reply points."""

    def redirect_request(self, *args, **kwargs):
        return None


def _send(request, timeout):
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
        return response.read(MAX_REPLY + 1)


def read(root=None, send=None):
    key = api_key(root)
    if key is None:
        raise Unavailable("Not signed in to DeepSeek")
    request = urllib.request.Request(BALANCE_URL, method="GET", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json",
        "User-Agent": "smith-agents"})
    try:
        raw = (send or _send)(request, READ_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise Unavailable("DeepSeek key was refused · sign in again") from None
        raise Unavailable("DeepSeek balance unavailable · retrying") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise Unavailable("DeepSeek balance unavailable · retrying") from None
    try:
        payload = json.loads(raw) if len(raw) <= MAX_REPLY else None
    except ValueError:
        payload = None
    return reading(payload)


def money(value, currency=None):
    """An amount in its own currency, as the widget prints it."""
    if value is None:
        return "—"
    symbol = _SYMBOLS.get(currency)
    return (symbol or (currency + " " if currency else "")) + "%.2f" % value


def demo_account():
    return {"reading": reading({"is_available": True, "balance_infos": [
        {"currency": "USD", "total_balance": "18.40", "granted_balance": "0.00", "topped_up_balance": "18.40"}]}),
        "spend": {"spent": 0.31, "since": None, "pace": None, "hours_left": None},
        "updated": None, "error": None}
