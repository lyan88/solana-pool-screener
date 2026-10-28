#!/usr/bin/env python3
"""Uji format alert (Inggris & Indonesia) tanpa mengirim apa pun."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import i18n  # noqa: E402
import telegram  # noqa: E402


POOL = {
    "pool": "Pool111",
    "mint": "Mint111",
    "name": "Test / SOL",
    "symbol": "TEST",
    "dex": "pumpswap",
    "age_sec": 252,
    "liq_usd": 22139.0,
    "mc_usd": 74470.0,
    "price_usd": 0.00004347,
}

SEC = {
    "top10_pct": 22.5,
    "details": {
        "lp_status": "burned",
        "lp_pct": 100.0,
        "mint_revoked": True,
        "freeze_revoked": True,
    },
}

BUYERS = {"window_sec": 120, "buyers": 23, "buys": 31, "sells": 4}


class EnglishAlertTests(unittest.TestCase):
    def test_alert_contains_all_metrics(self):
        msg = telegram.format_alert(POOL, SEC, BUYERS, lang="en")
        self.assertIn("$TEST", msg)
        self.assertNotIn("SCREENER HIT", msg)
        self.assertIn("age 4m12s", msg)
        self.assertIn("MC <b>$74.5k</b>", msg)
        self.assertIn("Unique buyers 120s: <b>23</b> (31 buys / 4 sells)", msg)
        self.assertIn("Top 10: <b>22.5%</b> (excl. pool/burn)", msg)
        self.assertIn("Mint auth: revoked ✅ · Freeze auth: revoked ✅", msg)
        self.assertIn("LP: <b>burned 100.0%</b>", msg)
        self.assertIn("Price: $0.00004347", msg)
        self.assertNotIn("e-", msg)
        self.assertIn("dexscreener.com/solana/Pool111", msg)
        self.assertIn("rugcheck.xyz/tokens/Mint111", msg)

    def test_authority_status_is_not_hardcoded(self):
        sec = {
            "top10_pct": 10.0,
            "details": {
                "lp_status": "locked",
                "lp_pct": 100.0,
                "mint_revoked": True,
                "freeze_revoked": False,
            },
        }
        msg = telegram.format_alert(POOL, sec, BUYERS, lang="en")
        self.assertIn("Mint auth: revoked ✅ · Freeze auth: ACTIVE ⚠️", msg)

        # Without data the alert must say unknown, never claim revoked.
        sec["details"].pop("mint_revoked")
        sec["details"].pop("freeze_revoked")
        msg = telegram.format_alert(POOL, sec, BUYERS, lang="en")
        self.assertIn("unknown", msg)
        self.assertNotIn("Mint auth: revoked", msg)

    def test_small_price_is_not_scientific(self):
        pool = dict(POOL, price_usd=9.094216389484412e-06)
        msg = telegram.format_alert(pool, SEC, BUYERS, lang="en")
        self.assertIn("Price: $0.0000090942164", msg)
        self.assertNotIn("e-06", msg)

    def test_delayed_banner_marks_detection_time(self):
        self.assertEqual(telegram._delayed_message("x", 29, lang="en"), "x")
        self.assertIn("Delayed 45s", telegram._delayed_message("x", 45, lang="en"))
        self.assertIn("Delayed 2m", telegram._delayed_message("x", 125, lang="en"))
        self.assertIn(
            "as of detection", telegram._delayed_message("x", 45, lang="en")
        )

    def test_symbol_is_escaped(self):
        pool = dict(POOL, symbol="<SCRIPT>")
        msg = telegram.format_alert(pool, SEC, BUYERS, lang="en")
        self.assertNotIn("<SCRIPT>", msg)
        self.assertIn("&lt;SCRIPT&gt;", msg)


class IndonesianAlertTests(unittest.TestCase):
    def test_alert_uses_indonesian_labels(self):
        sec = {
            "top10_pct": 22.5,
            "details": {
                "lp_status": "unlocked",
                "lp_pct": 12.5,
                "mint_revoked": True,
                "freeze_revoked": False,
            },
        }
        msg = telegram.format_alert(POOL, sec, BUYERS, lang="id")
        self.assertIn("umur 4m12s", msg)
        self.assertIn("Buyer unik 120s: <b>23</b> (31 beli / 4 jual)", msg)
        self.assertIn("Top 10: <b>22.5%</b> (di luar pool/burn)", msg)
        self.assertIn("Mint auth: revoked ✅ · Freeze auth: AKTIF ⚠️", msg)
        self.assertIn("LP: <b>terbuka 12.5%</b>", msg)
        self.assertIn("Harga: $0.00004347", msg)

    def test_indonesian_unknown_and_delay_banner(self):
        sec = {"top10_pct": 10.0, "details": {"lp_status": "unknown"}}
        msg = telegram.format_alert(POOL, sec, BUYERS, lang="id")
        self.assertIn("tidak diketahui", msg)
        self.assertIn("LP: <b>tidak diketahui</b>", msg)

        delayed = telegram._delayed_message("x", 45, lang="id")
        self.assertIn("Tertunda 45s", delayed)
        self.assertIn("saat deteksi", delayed)


class LanguageNormalizationTests(unittest.TestCase):
    def test_supported_and_fallback(self):
        self.assertEqual(i18n.normalize("en"), "en")
        self.assertEqual(i18n.normalize("ID"), "id")
        self.assertEqual(i18n.normalize("xx"), "en")
        self.assertEqual(i18n.normalize(None), "en")

    def test_status_labels(self):
        self.assertEqual(i18n.status_label("en", "unlocked"), "open")
        self.assertEqual(i18n.status_label("id", "unlocked"), "terbuka")
        self.assertEqual(i18n.status_label("en", "weird"), "weird")


if __name__ == "__main__":
    unittest.main(verbosity=2)
