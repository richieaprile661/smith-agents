"""The DeepSeek account balance: the app's own key, one fixed endpoint, figures only."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error

from PIL import ImageDraw

from smith_agents import deepseek_account as account, deepseek_usage as usage, hermes_usage_ui as ui
from smith_agents.app import SmithAgentsWidget
from test_core import core
from test_deepseek_usage import log
from test_desktop_apps import T0

KEY = "sk-0123456789abcdef0123456789abcdef"
GRANT = "g12345ab6cde7fg8hi_9j0klmno1pqrstuvwxyzab2c"
CREDENTIALS = """version: 1
records:
  deepseek-account/default-account:
    kind: grant
    payload:
      version: 1
      secret: %s
refs:
  DEEPSEEK_API_KEY: %s
""" % (GRANT, KEY)
PAYLOAD = {"is_available": True, "balance_infos": [
    {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance": "100.00"}]}


def home(text=CREDENTIALS):
    folder = tempfile.TemporaryDirectory()
    if text is not None:
        (Path(folder.name) / ".credentials.yaml").write_text(text, encoding="utf-8")
    return folder


def answering(payload, seen=None):
    def send(request, timeout):
        if seen is not None:
            seen.append((request, timeout))
        return json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    return send


class KeyTests(unittest.TestCase):
    def test_key_is_the_apps_own_reference_and_never_the_sign_in_grant(self):
        with home() as folder:
            self.assertEqual(account.api_key(folder), KEY)

    def test_quoted_and_commented_values_are_read(self):
        for value in ('"%s"' % KEY, "'%s'" % KEY, KEY + "   # rotated in October"):
            with self.subTest(value=value), home(CREDENTIALS.replace(KEY, value)) as folder:
                self.assertEqual(account.api_key(folder), KEY)

    def test_anything_but_a_plain_key_under_refs_is_refused(self):
        outside = "version: 1\nrecords:\n  DEEPSEEK_API_KEY: %s\nrefs:\n  OTHER: x\n" % KEY
        nested = "version: 1\nrefs:\n  group:\n    DEEPSEEK_API_KEY: %s\n" % KEY
        for text in (outside, nested, CREDENTIALS.replace(KEY, GRANT), CREDENTIALS.replace(KEY, ""),
                     CREDENTIALS.replace(KEY, KEY + "\\nHost: example.org"), "", None):
            with self.subTest(text=text), home(text) as folder:
                self.assertIsNone(account.api_key(folder))


class ReadingTests(unittest.TestCase):
    def read(self, payload=PAYLOAD, seen=None, text=CREDENTIALS):
        with home(text) as folder:
            return account.read(folder, send=answering(payload, seen))

    def test_balance_is_asked_of_one_address_with_the_key(self):
        seen = []
        reading = self.read(seen=seen)
        request, timeout = seen[0]
        self.assertEqual((request.full_url, request.get_method()),
                         ("https://api.deepseek.com/user/balance", "GET"))
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertLessEqual(timeout, 30)
        self.assertEqual((reading["left"], reading["currency"], reading["topup_left"],
                          reading["granted_left"], reading["available"]),
                         (110.0, "CNY", 100.0, 10.0, True))

    def test_only_figures_are_kept(self):
        reading = self.read(dict(PAYLOAD, api_key=KEY, user={"email": "me@example.org"}))
        kept = json.dumps(reading)
        self.assertNotIn(KEY, kept)
        self.assertNotIn("example.org", kept)

    def test_first_wallet_with_money_leads_and_the_rest_are_kept(self):
        reading = self.read({"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "0.00", "granted_balance": "0", "topped_up_balance": "0"},
            {"currency": "USD", "total_balance": "5.5", "granted_balance": "0.5", "topped_up_balance": "5"}]})
        self.assertEqual((reading["currency"], reading["left"]), ("USD", 5.5))
        self.assertEqual([(w["currency"], w["left"]) for w in reading["wallets"]], [("CNY", 0.0), ("USD", 5.5)])

    def test_exhausted_account_is_a_reading_not_an_error(self):
        reading = self.read({"is_available": False, "balance_infos": [
            {"currency": "USD", "total_balance": "0.00", "granted_balance": "0.00", "topped_up_balance": "0.00"}]})
        self.assertEqual((reading["left"], reading["available"]), (0.0, False))

    def test_answer_without_a_usable_balance_is_unavailable(self):
        for payload in (b"<html>busy</html>", [], {}, {"balance_infos": []}, {"balance_infos": "x"},
                        {"balance_infos": [{"currency": "USD", "total_balance": "NaN"}]},
                        {"balance_infos": [{"currency": "USD", "total_balance": "Infinity"}]},
                        {"balance_infos": [{"currency": 7, "total_balance": "1"}]},
                        {"balance_infos": [{"currency": "USD", "total_balance": None}]}):
            with self.subTest(payload=payload), self.assertRaises(account.Unavailable):
                self.read(payload)

    def test_without_a_key_nothing_is_sent(self):
        seen = []
        with self.assertRaisesRegex(account.Unavailable, "Not signed in to DeepSeek"):
            self.read(seen=seen, text=None)
        self.assertEqual(seen, [])

    def test_refused_key_and_network_trouble_are_told_apart(self):
        def refused(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        def offline(request, timeout):
            raise urllib.error.URLError("no route")
        with home() as folder:
            with self.assertRaisesRegex(account.Unavailable, "key was refused"):
                account.read(folder, send=refused)
            with self.assertRaisesRegex(account.Unavailable, "retrying"):
                account.read(folder, send=offline)

    def test_money_names_its_currency(self):
        for value, currency, expected in ((110, "CNY", "¥110.00"), (5.5, "USD", "$5.50"),
                                          (3, "EUR", "EUR 3.00"), (None, "USD", "—"), (1, None, "1.00")):
            self.assertEqual(account.money(value, currency), expected)


class RedirectTests(unittest.TestCase):
    """The key goes to the address asked and nowhere a reply points to."""

    def serve(self, handler):
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_port

    def test_redirect_is_not_followed(self):
        received = []

        class Elsewhere(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(PAYLOAD).encode())

            def log_message(self, *args):
                pass
        elsewhere = self.serve(Elsewhere)

        class Redirecting(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", elsewhere + "/user/balance")
                self.end_headers()

            def log_message(self, *args):
                pass
        with home() as folder, patch.object(account, "BALANCE_URL", self.serve(Redirecting) + "/user/balance"):
            with self.assertRaises(account.Unavailable):
                account.read(folder)
        self.assertEqual(received, [])


@unittest.skipUnless(usage.can_read_logs(), "needs zstd from Python 3.14")
class BalanceInWidgetTests(unittest.TestCase):
    def setUp(self):
        from compression import zstd
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name) / "sessions" / "--projects-shop--" / "one"
        folder.mkdir(parents=True)
        (folder / "session.v4.jsonl.zstd").write_bytes(zstd.compress(b"\n".join(log()) + b"\n"))
        widget = SmithAgentsWidget.__new__(SmithAgentsWidget)
        widget.lock = threading.Lock()
        widget.config = dict(core.DEFAULTS, usage_provider="deepseek")
        widget.deepseek_data = usage.Reader().fetch(directory.name, today_start=T0)
        widget.deepseek_session_id, widget.deepseek_model_index = None, 0
        widget.deepseek_account = {"reading": account.reading(PAYLOAD), "updated": 5.0, "error": None,
                                   "spend": {"spent": 0.42, "since": 1.0, "pace": None, "hours_left": None}}
        self.widget = widget

    def words(self, draw):
        pen = Mock(wraps=ImageDraw.Draw(core.console_base((core.CONSOLE_W, core.px(420)))))
        draw(pen)
        return [str(call.args[1]) for call in pen.text.call_args_list]

    def test_usage_tab_shows_the_balance_and_spend_under_the_sessions(self):
        data = self.widget._snapshot()[6]
        words = self.words(lambda pen: core.render_usage(pen, [], None, data, 0))
        self.assertIn("Balance ¥110.00  ·  ¥10.00 granted", words)
        self.assertIn("Spent while open ¥0.42", words)
        self.assertIn("Checkout total", words)      # the saved sessions stay on the tab
        without = dict(data, account={})
        self.assertGreater(core.usage_height([], data), core.usage_height([], without))
        self.assertNotIn("Balance", " ".join(self.words(lambda pen: core.render_usage(pen, [], None, without, 0))))

    def test_header_names_the_balance(self):
        data = self.widget._snapshot()[6]
        chip = core.console_base((core.CONSOLE_W, core.BAR_H))
        pen = Mock(wraps=ImageDraw.Draw(chip))
        core.render_console_bar(chip, pen, [], None, data, {}, 0, False, provider="deepseek")
        self.assertIn("Balance ¥110.00", [call.args[1] for call in pen.text.call_args_list])

    def test_exhausted_balance_is_said_plainly(self):
        self.widget.deepseek_account["reading"] = account.reading({"is_available": False, "balance_infos": [
            {"currency": "USD", "total_balance": "0.00", "granted_balance": "0", "topped_up_balance": "0"}]})
        data = self.widget._snapshot()[6]
        self.assertIn("Balance $0.00  ·  top up to continue",
                      self.words(lambda pen: core.render_usage(pen, [], None, data, 0)))

    def test_deepseek_balance_never_becomes_the_hermes_account(self):
        self.assertIsNone(ui.balance(self.widget._snapshot()[6]))

    def test_balance_loop_keeps_the_last_reading_when_a_read_fails(self):
        widget = self.widget
        widget.stopping = threading.Event()
        widget.deepseek_spend = Mock()
        with patch.object(account, "api_key", return_value=KEY), \
             patch.object(account, "read", side_effect=account.Unavailable("DeepSeek balance unavailable · retrying")), \
             patch.object(widget.stopping, "wait", side_effect=lambda _: widget.stopping.set()) as wait:
            widget._deepseek_account_loop()
        state = widget._snapshot()[6]["account"]
        self.assertEqual((state["reading"]["left"], state["error"]),
                         (110.0, "DeepSeek balance unavailable · retrying"))
        wait.assert_called_once_with(120)
        words = self.words(lambda pen: core.render_usage(pen, [], None, widget._snapshot()[6], 0))
        self.assertIn("Last balance ¥110.00  ·  ¥10.00 granted", words)

    def test_balance_loop_never_starts_without_a_key(self):
        widget = self.widget
        widget.stopping = threading.Event()
        with patch.object(account, "api_key", return_value=None), patch.object(account, "read") as read:
            widget._deepseek_account_loop()
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
