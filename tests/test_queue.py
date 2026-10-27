#!/usr/bin/env python3
"""Uji antrean alert, watchlist, dan skor prioritas tanpa jaringan."""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402
import filters  # noqa: E402
import telegram  # noqa: E402


def _row(mint="MintQueue", symbol="QUEUE", price=0.001):
    return {
        "mint": mint,
        "pool": "PoolQueue",
        "name": "Queue / SOL",
        "symbol": symbol,
        "dex": "pumpswap",
        "age_sec": 120,
        "liq_usd": 20_000,
        "mc_usd": 50_000,
        "buyers5": 40,
        "top10_pct": 20.0,
        "lp_status": "burned",
        "lp_pct": 100.0,
        "price_usd": price,
        "buyers2": 50,
        "buys2": 60,
        "sells2": 10,
        "passed": True,
        "reasons": [],
    }


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "queue.db"))
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_queue_orders_by_score(self):
        db.queue_alert(self.conn, _row("Low"), 1.0, "low")
        db.queue_alert(self.conn, _row("High"), 9.0, "high")
        rows = db.pending_queue(self.conn, limit=10)
        self.assertEqual([r["mint"] for r in rows], ["High", "Low"])

    def test_queue_is_idempotent_and_keeps_sent(self):
        row = _row()
        self.assertTrue(db.queue_alert(self.conn, row, 5.0, "first"))
        self.assertFalse(db.has_alerted(self.conn, row["mint"]))
        self.assertTrue(db.has_pending_alert(self.conn, row["mint"]))
        db.mark_queue_sent(self.conn, row["mint"])
        self.assertFalse(db.has_pending_alert(self.conn, row["mint"]))
        # Alert yang sudah terkirim tidak boleh masuk antrean lagi.
        self.assertFalse(db.queue_alert(self.conn, row, 9.0, "second"))

    def test_expired_queue_is_not_delivered(self):
        db.queue_alert(self.conn, _row("Old"), 5.0, "old", ttl=60)
        self.conn.execute(
            "UPDATE alert_queue SET expires_at=? WHERE mint='Old'",
            (int(time.time()) - 1,),
        )
        self.conn.commit()
        self.assertEqual(db.pending_queue(self.conn), [])
        self.assertEqual(db.expire_queue(self.conn), 1)
        self.assertEqual(db.watch_stats(self.conn), {})

    def test_drain_sends_and_records_alert(self):
        row = _row(price=0.0042)
        db.queue_alert(self.conn, row, 7.0, "hello")
        # Evaluation dibuat dulu seperti alur scanner.
        db.upsert_evaluation(self.conn, dict(row, passed=True))

        with mock.patch.object(telegram, "send", return_value=(True, "terkirim")) as send:
            result = telegram.drain_queue(self.conn)

        self.assertEqual(result, {"sent": 1, "failed": 0})
        send.assert_called_once()
        saved = self.conn.execute(
            "SELECT * FROM alerts WHERE mint=?", (row["mint"],)
        ).fetchone()
        self.assertIsNotNone(saved)
        self.assertAlmostEqual(saved["price_usd"], 0.0042)
        alerted = self.conn.execute(
            "SELECT alerted_at FROM evaluations WHERE mint=?", (row["mint"],)
        ).fetchone()["alerted_at"]
        self.assertIsNotNone(alerted)

    def test_drain_drops_price_when_delayed(self):
        row = _row(price=0.0042)
        db.queue_alert(self.conn, row, 7.0, "later")
        self.conn.execute(
            "UPDATE alert_queue SET created_at=? WHERE mint=?",
            (int(time.time()) - config.ALERT_QUEUE_MAX_PRICE_AGE_SEC - 30, row["mint"]),
        )
        self.conn.commit()

        with mock.patch.object(telegram, "send", return_value=(True, "terkirim")):
            result = telegram.drain_queue(self.conn)

        self.assertEqual(result["sent"], 1)
        saved = self.conn.execute(
            "SELECT price_usd FROM alerts WHERE mint=?", (row["mint"],)
        ).fetchone()
        self.assertIsNone(saved["price_usd"])

    def test_quota_failure_keeps_item_pending(self):
        row = _row()
        db.queue_alert(self.conn, row, 7.0, "hello")
        with mock.patch.object(
            telegram, "send", return_value=(False, "batas 40 pesan/jam tercapai")
        ):
            result = telegram.drain_queue(self.conn)
        self.assertEqual(result, {"sent": 0, "failed": 0})
        saved = self.conn.execute(
            "SELECT status, attempts FROM alert_queue WHERE mint=?", (row["mint"],)
        ).fetchone()
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["attempts"], 0)


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "watch.db"))
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _pool(self):
        return {
            "mint": "MintWatch", "pool": "PoolWatch", "name": "Watch / SOL",
            "symbol": "WATCH", "dex": "pumpswap", "age_sec": 200,
        }

    def test_watch_is_due_after_interval(self):
        db.watch_upsert(
            self.conn, self._pool(), ["lp"], 3.0, ttl=600, interval=0
        )
        rows = db.watch_due(self.conn, limit=5)
        self.assertEqual([r["mint"] for r in rows], ["MintWatch"])
        db.watch_touch(self.conn, "MintWatch", ["lp"], 4.0)
        self.assertEqual(db.watch_due(self.conn, limit=5), [])

    def test_watch_expires(self):
        db.watch_upsert(
            self.conn, self._pool(), ["top10"], 2.0, ttl=600, interval=0
        )
        self.assertEqual(db.watch_expire(self.conn, int(time.time()) + 700), 1)
        self.assertEqual(db.watch_due(self.conn, int(time.time()) + 700), [])
        self.assertEqual(db.watch_stats(self.conn), {"expired": 1})

    def test_watch_not_reopened_after_alert(self):
        db.watch_upsert(
            self.conn, self._pool(), ["lp"], 3.0, ttl=600, interval=0
        )
        db.watch_finish(self.conn, "MintWatch", "alerted")
        db.watch_upsert(
            self.conn, self._pool(), ["lp"], 5.0, ttl=600, interval=0
        )
        self.assertEqual(db.watch_due(self.conn, limit=5), [])


class ScoringTests(unittest.TestCase):
    def _sec(self, top10):
        return {"top10_pct": top10, "details": {"lp_status": "burned"}}

    def test_better_metrics_get_higher_score(self):
        good = filters.alert_score(
            {"mc_usd": 50_000, "liq_usd": 25_000, "age_sec": 60},
            self._sec(18.0), {"buyers": 120},
        )
        weak = filters.alert_score(
            {"mc_usd": 50_000, "liq_usd": 8_000, "age_sec": 500},
            self._sec(34.0), {"buyers": 16},
        )
        self.assertGreater(good, weak)

    def test_near_miss_only_for_recoverable_reasons(self):
        base = {"reasons": ["lp"], "lp_status": "unlocked", "lp_pct": 95.0}
        self.assertTrue(filters.near_miss(base))
        self.assertFalse(filters.near_miss(dict(base, lp_pct=50.0)))
        self.assertFalse(filters.near_miss({"reasons": ["check_authorities"]}))
        self.assertFalse(filters.near_miss({"reasons": ["pool_dead"]}))
        self.assertTrue(
            filters.near_miss(
                {"reasons": ["top10"], "top10_pct": config.TOP10_MAX_PCT + 2}
            )
        )
        self.assertFalse(
            filters.near_miss(
                {"reasons": ["top10"], "top10_pct": config.TOP10_MAX_PCT + 20}
            )
        )
        self.assertTrue(
            filters.near_miss(
                {"reasons": ["buyers"], "buyers2": config.MIN_UNIQUE_BUYERS - 2}
            )
        )
        self.assertFalse(
            filters.near_miss(
                {"reasons": ["buyers"], "buyers2": config.MIN_UNIQUE_BUYERS - 10}
            )
        )
        self.assertTrue(filters.near_miss({"reasons": ["trades_unavailable"]}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
