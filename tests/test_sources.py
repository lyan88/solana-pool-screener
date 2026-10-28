#!/usr/bin/env python3
"""Uji adaptor sumber data tanpa jaringan (harga trade, parsing)."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sources  # noqa: E402

NOW = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
MINT = "TokenMint111"
SOL = "So11111111111111111111111111111111111111112"


def trade(kind="buy", age=10, price=0.002, anomalous=False):
    """Trade sudah dinormalisasi seperti keluaran _trade_from_gecko."""
    if kind == "buy":
        from_token, to_token = SOL, MINT
        price_from, price_to = 100.0, price
    else:
        from_token, to_token = MINT, SOL
        price_from, price_to = price, 100.0
    return {
        "ts": NOW - timedelta(seconds=age),
        "kind": kind,
        "wallet": "wallet-%s-%s" % (kind, age),
        "from_token": from_token,
        "to_token": to_token,
        "price_from_usd": price_from,
        "price_to_usd": price_to,
        "anomalous": anomalous,
    }


class TradeParsingTests(unittest.TestCase):
    def test_trade_keeps_price_fields(self):
        item = {
            "attributes": {
                "block_timestamp": "2026-10-28T00:59:50Z",
                "kind": "buy",
                "tx_from_address": "wallet1",
                "tx_hash": "tx1",
                "from_token_address": SOL,
                "to_token_address": MINT,
                "price_from_in_usd": "100.5",
                "price_to_in_usd": "0.000042",
                "volume_in_usd": "12.5",
                "is_anomalous": False,
            }
        }
        parsed = sources._trade_from_gecko(item)
        self.assertEqual(parsed["from_token"], SOL)
        self.assertEqual(parsed["to_token"], MINT)
        self.assertAlmostEqual(parsed["price_from_usd"], 100.5)
        self.assertAlmostEqual(parsed["price_to_usd"], 0.000042)


class LatestTradePriceTests(unittest.TestCase):
    def test_picks_newest_non_anomalous_trade(self):
        trades = [
            trade("buy", age=30, price=0.001),
            trade("sell", age=5, price=0.0042),
            trade("buy", age=1, price=9.9, anomalous=True),
        ]
        price = sources.latest_trade_price(trades, MINT, now=NOW)
        self.assertAlmostEqual(price, 0.0042)

    def test_ignores_trades_without_the_mint(self):
        trades = [trade("buy", age=5, price=0.0042)]
        self.assertIsNone(sources.latest_trade_price(trades, "OtherMint", now=NOW))

    def test_returns_none_when_trades_are_stale(self):
        trades = [trade("buy", age=600, price=0.0042)]
        self.assertIsNone(
            sources.latest_trade_price(trades, MINT, max_age_sec=180, now=NOW)
        )

    def test_returns_none_for_bad_price(self):
        trades = [trade("buy", age=5, price=0)]
        self.assertIsNone(sources.latest_trade_price(trades, MINT, now=NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
