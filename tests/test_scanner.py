#!/usr/bin/env python3
"""Uji orkestrasi scanner dengan API palsu: antrean, watchlist, isolasi error."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402
import scanner  # noqa: E402

NOW = datetime.now(timezone.utc)
POOL = "PoolScanner1"
MINT = "MintScanner1"


def make_pool(mint=MINT, pool=POOL, age=120):
    return {
        "pool": pool,
        "mint": mint,
        "name": "Scan / SOL",
        "symbol": "SCAN",
        "dex": "pumpswap",
        "age_sec": age,
        "liq_usd": 20_000,
        "mc_usd": 50_000,
        "buyers5": 30,
        "price_usd": 0.001,
        "created_at": NOW - timedelta(seconds=age),
    }


def make_report(top_pct=20.0, lp_pct=100.0):
    holders = [{"owner": POOL, "address": "ata0", "pct": 30.0}]
    # 10 holder @ top_pct/10 supaya total setelah pool dibuang = top_pct.
    each = top_pct / 10.0
    holders += [
        {"owner": "w%d" % i, "address": "ata%d" % (i + 1), "pct": each}
        for i in range(10)
    ]
    return {
        "mint": MINT,
        "mintAuthority": None,
        "freezeAuthority": None,
        "rugged": False,
        "knownAccounts": {POOL: {"type": "AMM"}},
        "topHolders": holders,
        "markets": [{
            "pubkey": POOL,
            "lp": {"lpLockedPct": lp_pct, "holders": []},
        }],
    }


def make_trades(count=30):
    return [
        {
            "ts": NOW - timedelta(seconds=10 + i),
            "kind": "buy",
            "wallet": "buyer%d" % i,
            "anomalous": False,
        }
        for i in range(count)
    ]


class ScannerFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "scan.db"))
        db.init(self.conn)
        self._db = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "scan.db")
        self._data = mock.patch.object(config, "DATA_DIR", Path(self.tmp.name))
        self._db.start()
        self._data.start()
        # Jangan pernah kirim Telegram sungguhan saat unit test.
        self._send = mock.patch.object(
            scanner.telegram, "send", return_value=(True, "terkirim")
        )
        self._send.start()

    def tearDown(self):
        self._send.stop()
        self._db.stop()
        self._data.stop()
        self.conn.close()
        self.tmp.cleanup()

    def _stats(self):
        return {
            "evaluated": 0, "passed": 0, "queued": 0,
            "watched": 0, "errors": 0, "alerted": 0, "failed": 0,
        }

    def test_passing_pool_is_queued_then_sent(self):
        pool = make_pool()
        with mock.patch.object(scanner.sources, "fetch_rugcheck", return_value=make_report()), \
             mock.patch.object(scanner.sources, "fetch_trades", return_value=make_trades()):
            stats = self._stats()
            scanner._process_pool(self.conn, pool, NOW, False, False, stats)

        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["queued"], 1)
        # Antrean langsung dikuras supaya harga tidak basi.
        self.assertEqual(stats["alerted"], 1)
        self.assertFalse(db.has_pending_alert(self.conn, MINT))
        self.assertTrue(db.has_alerted(self.conn, MINT))

    def test_alert_uses_latest_trade_price_not_stale_snapshot(self):
        pool = make_pool()
        pool["price_usd"] = 0.001  # snapshot lama dari new_pools
        trades = make_trades(30)
        for item in trades:
            item["from_token"] = MINT
            item["to_token"] = "So11111111111111111111111111111111111111112"
            item["price_from_usd"] = 0.002
            item["price_to_usd"] = 100.0
        # make_trades() membuat trade terbaru di indeks 0.
        trades[0]["price_from_usd"] = 0.0015

        with mock.patch.object(scanner.sources, "fetch_rugcheck", return_value=make_report()), \
             mock.patch.object(scanner.sources, "fetch_trades", return_value=trades):
            row, _sec, _buyers, passed = scanner._evaluate_pool(
                self.conn, pool, NOW, verbose=False
            )

        self.assertTrue(passed)
        self.assertAlmostEqual(row["price_usd"], 0.0015)
        self.assertAlmostEqual(pool["price_usd"], 0.0015)
        # MC snapshot $50k @ 0.001 -> $75k @ 0.0015.
        self.assertAlmostEqual(row["mc_usd"], 75_000.0)

    def test_near_miss_enters_watchlist(self):
        pool = make_pool()
        # Top10 37%: di atas ambang 35 tetapi masih dalam margin watch 5%.
        report = make_report(top_pct=37.0)
        with mock.patch.object(scanner.sources, "fetch_rugcheck", return_value=report), \
             mock.patch.object(scanner.sources, "fetch_trades", return_value=make_trades()):
            stats = self._stats()
            scanner._process_pool(self.conn, pool, NOW, False, False, stats)

        self.assertEqual(stats["passed"], 0)
        self.assertEqual(stats["watched"], 1)
        row = self.conn.execute(
            "SELECT status, reasons FROM watchlist WHERE mint=?", (MINT,)
        ).fetchone()
        self.assertEqual(row["status"], "watching")
        self.assertIn("top10", row["reasons"])

    def test_hard_reject_does_not_enter_watchlist(self):
        pool = make_pool()
        report = make_report()
        report["mintAuthority"] = "StillActive"
        with mock.patch.object(scanner.sources, "fetch_rugcheck", return_value=report), \
             mock.patch.object(scanner.sources, "fetch_trades", return_value=make_trades()):
            stats = self._stats()
            scanner._process_pool(self.conn, pool, NOW, False, False, stats)

        self.assertEqual(stats["watched"], 0)
        self.assertEqual(db.watch_stats(self.conn), {})

    def test_watchlist_recheck_can_queue_alert(self):
        pool = make_pool()
        db.watch_upsert(self.conn, pool, ["top10"], 1.0, ttl=600, interval=0)

        with mock.patch.object(scanner.sources, "fetch_pool", return_value=pool), \
             mock.patch.object(scanner.sources, "fetch_rugcheck", return_value=make_report()), \
             mock.patch.object(scanner.sources, "fetch_trades", return_value=make_trades()):
            stats = self._stats()
            scanner._process_watchlist(self.conn, NOW, False, False, stats, set())

        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(stats["alerted"], 1)
        self.assertTrue(db.has_alerted(self.conn, MINT))
        status = self.conn.execute(
            "SELECT status FROM watchlist WHERE mint=?", (MINT,)
        ).fetchone()["status"]
        self.assertEqual(status, "queued")

    def test_one_bad_candidate_does_not_stop_round(self):
        p1, p2 = make_pool("MintA", "PoolA"), make_pool("MintB", "PoolB")
        calls = []

        def fake_process(conn, pool, now, dry_run, verbose, stats):
            calls.append(pool["mint"])
            if pool["mint"] == "MintA":
                raise RuntimeError("data rusak")
            return True

        with mock.patch.object(
            scanner.sources, "fetch_new_pools", return_value=([p1, p2], [])
        ), mock.patch.object(scanner, "_process_pool", side_effect=fake_process), \
             mock.patch.object(
                 scanner.telegram, "drain_queue", return_value={"sent": 0, "failed": 0}
             ):
            code = scanner.scan_once()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["MintA", "MintB"])
        saved = self.conn.execute(
            "SELECT errors FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(saved["errors"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
