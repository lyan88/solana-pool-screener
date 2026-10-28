#!/usr/bin/env python3
"""Uji format alert tanpa mengirim apa pun."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class TelegramFormatTests(unittest.TestCase):
    def test_alert_contains_all_metrics(self):
        sec = {
            "top10_pct": 22.5,
            "details": {
                "lp_status": "burned",
                "lp_pct": 100.0,
                "mint_revoked": True,
                "freeze_revoked": True,
            },
        }
        buyers = {"window_sec": 120, "buyers": 23, "buys": 31, "sells": 4}
        msg = telegram.format_alert(POOL, sec, buyers)
        self.assertIn("$TEST", msg)
        self.assertNotIn("SCREENER HIT", msg)
        self.assertIn("4m12s", msg)
        self.assertIn("MC <b>$74.5k</b>", msg)
        self.assertIn("Buyer unik 120s: <b>23</b> (31 beli / 4 jual)", msg)
        self.assertIn("Top 10: <b>22.5%</b>", msg)
        self.assertIn("Mint auth: revoked ✅ · Freeze auth: revoked ✅", msg)
        self.assertIn("LP: <b>burned 100.0%</b>", msg)
        self.assertIn("Harga: $0.00004347", msg)
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
        buyers = {"window_sec": 120, "buyers": 15, "buys": 15, "sells": 0}
        msg = telegram.format_alert(POOL, sec, buyers)
        self.assertIn("Mint auth: revoked ✅ · Freeze auth: AKTIF ⚠️", msg)

        # Tanpa data, alert harus jujur "tidak diketahui", bukan revoked.
        sec["details"].pop("mint_revoked")
        sec["details"].pop("freeze_revoked")
        msg = telegram.format_alert(POOL, sec, buyers)
        self.assertIn("tidak diketahui", msg)
        self.assertNotIn("Mint auth: revoked", msg)

    def test_small_price_is_not_scientific(self):
        pool = dict(POOL, price_usd=9.094216389484412e-06)
        sec = {
            "top10_pct": 10.0,
            "details": {
                "lp_status": "burned", "lp_pct": 100.0,
                "mint_revoked": True, "freeze_revoked": True,
            },
        }
        buyers = {"window_sec": 120, "buyers": 15, "buys": 15, "sells": 0}
        msg = telegram.format_alert(pool, sec, buyers)
        self.assertIn("Harga: $0.0000090942164", msg)
        self.assertNotIn("e-06", msg)

    def test_delayed_banner_marks_detection_time(self):
        self.assertEqual(telegram._delayed_message("x", 29), "x")
        self.assertIn("Tertunda 45s", telegram._delayed_message("x", 45))
        self.assertIn("Tertunda 2m", telegram._delayed_message("x", 125))
        self.assertIn("saat deteksi", telegram._delayed_message("x", 45))

    def test_symbol_is_escaped(self):
        pool = dict(POOL, symbol="<SCRIPT>")
        sec = {"top10_pct": 10.0, "details": {"lp_status": "locked", "lp_pct": 100.0}}
        buyers = {"window_sec": 120, "buyers": 15, "buys": 15, "sells": 0}
        msg = telegram.format_alert(pool, sec, buyers)
        self.assertNotIn("<SCRIPT>", msg)
        self.assertIn("&lt;SCRIPT&gt;", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
