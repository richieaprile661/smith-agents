import os
import tempfile
import time
import unittest

os.environ.setdefault("SMITH_AGENTS_CONFIG_DIR", tempfile.mkdtemp(prefix="widget-codex-reset-"))

from smith_agents import codex_usage, core, tucked
from smith_agents.dashboard import account


def payload(credits=None, grants=()):
    return {"rateLimits": {"primary": {"usedPercent": 100, "windowDurationMins": 10080},
                           **({"credits": credits} if credits else {})},
            "rateLimitResetCredits": {"availableCount": len(grants), "credits": list(grants)}}


LATER = time.time() + 86400 * 20


class CodexReset(unittest.TestCase):
    def test_an_available_reset_is_shown_without_a_balance(self):
        credits = codex_usage.build_credits(payload(grants=[{"status": "available", "expiresAt": LATER,
                                                             "id": "RateLimitResetCredit_x", "title": "Full reset"}]))
        self.assertIsNone(credits["text"])
        self.assertEqual(credits["reset"], {"count": 1, "expires_at": LATER})
        self.assertTrue(codex_usage.reset_text(credits["reset"]).startswith("1 free reset · until "))
        self.assertEqual(codex_usage.reset_text(credits["reset"], wide=False), "1 free reset")

    def test_used_or_expired_resets_and_none_at_all_show_nothing(self):
        for grants in ([], [{"status": "used", "expiresAt": LATER}],
                       [{"status": "available", "expiresAt": time.time() - 60}]):
            with self.subTest(grants=grants):
                self.assertIsNone(codex_usage.build_credits(payload(grants=grants)))

    def test_a_balance_keeps_its_place_and_carries_the_reset(self):
        credits = codex_usage.build_credits(payload(
            {"hasCredits": True, "unlimited": False, "balance": "1240"},
            [{"status": "available", "expiresAt": LATER}, {"status": "available", "expiresAt": LATER + 5}]))
        self.assertEqual(credits["text"], "1,240")
        self.assertEqual(credits["reset"]["count"], 2)
        self.assertEqual(codex_usage.reset_text(credits["reset"], wide=False), "2 free resets")

    def test_every_view_draws_a_reset_without_a_balance(self):
        p = payload(grants=[{"status": "available", "expiresAt": LATER}])
        credits, metrics, now = codex_usage.build_credits(p), codex_usage.build_metrics(p), time.time()
        core.render_console(metrics, credits, codex_usage.build_stats({}), [], now, tab="usage", provider="codex")
        for side in ("right", "top"):
            tucked.render([], metrics, metrics[0], now, side=side, provider="codex", usage_open=True,
                          usage_data=credits, max_width=core.px(900), max_height=core.px(700))
        self.assertEqual(tucked._credits("codex", credits), "1 free reset")
        self.assertTrue(tucked._credits("codex", credits, horizontal=True).startswith("1 free reset · until"))

    def test_the_dashboard_keeps_only_the_count_and_expiry(self):
        clean = account.Readings.credits({"text": None, "on_credits": False,
                                          "reset": {"count": 1, "expires_at": LATER, "id": "secret"}})
        self.assertEqual(clean, {"text": None, "on_credits": False, "reset": {"count": 1, "expires_at": LATER}})
        self.assertIsNone(account.Readings.credits({"text": None, "reset": {"count": 0}}))


if __name__ == "__main__":
    unittest.main()
