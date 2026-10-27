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
            "details": {"lp_status": "burned", "lp_pct": 100.0},
        }
        buyers = {"window_sec": 120, "buyers": 23, "buys": 31, "sells": 4}
        msg = telegram.format_alert(POOL, sec, buyers)
        self.assertIn("$TEST", msg)
        self.assertNotIn("SCREENER HIT", msg)
        self.assertIn("4m12s", msg)
        self.assertIn("MC <b>$74.5k</b>", msg)
        self.assertIn("Buyer unik 120s: <b>23</b> (31 beli / 4 jual)", msg)
        self.assertIn("Top 10: <b>22.5%</b>", msg)
        self.assertIn("LP: <b>burned 100.0%</b>", msg)
        self.assertIn("dexscreener.com/solana/Pool111", msg)
        self.assertIn("rugcheck.xyz/tokens/Mint111", msg)

    def test_symbol_is_escaped(self):
        pool = dict(POOL, symbol="<SCRIPT>")
        sec = {"top10_pct": 10.0, "details": {"lp_status": "locked", "lp_pct": 100.0}}
        buyers = {"window_sec": 120, "buyers": 15, "buys": 15, "sells": 0}
        msg = telegram.format_alert(pool, sec, buyers)
        self.assertNotIn("<SCRIPT>", msg)
        self.assertIn("&lt;SCRIPT&gt;", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
