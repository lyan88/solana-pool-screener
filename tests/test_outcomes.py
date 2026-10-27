#!/usr/bin/env python3
"""Uji antrean outcome dan migrasi database tanpa menyentuh DB produksi."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402
import track  # noqa: E402


class MeasureTests(unittest.TestCase):
    BASE = 1_800_000_000

    def _candles(self, count, high=1.1, low=0.9):
        return [
            {
                "ts": self.BASE + i * 60, "open": 1.0, "high": high,
                "low": low, "close": 1.0, "volume": 1.0,
            }
            for i in range(count)
        ]

    def test_fresh_candles_are_measured(self):
        result, error = track._measure({"price_usd": 1.0}, 300, self._candles(7), self.BASE)
        self.assertIsNone(error)
        self.assertAlmostEqual(result["mfe"], 0.1)
        self.assertAlmostEqual(result["mae"], -0.1)

    def test_stale_candles_are_rejected(self):
        # Hanya 3 lilin, sedangkan horizon 5 menit: pool berhenti trading.
        result, error = track._measure({"price_usd": 1.0}, 300, self._candles(3), self.BASE)
        self.assertIsNone(result)
        self.assertIn("berhenti", error)


class OutcomeQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "test.db")
        self.conn = db.connect(self.path)
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _alert(self, mint="Mint1", price=1.0, created_at=None):
        self.conn.execute(
            """
            INSERT INTO alerts(mint, pool, name, symbol, created_at, age_sec,
                               liq_usd, mc_usd, buyers2, top10_pct, lp_status,
                               price_usd, message)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mint, "Pool1", "Test / SOL", "TEST",
                int(created_at or time.time()), 120, 20000, 50000, 30, 20,
                "locked", price, "x",
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT id FROM alerts WHERE mint=?", (mint,)).fetchone()["id"]

    def test_record_alert_stores_price(self):
        row = {
            "mint": "MintPrice", "pool": "Pool1", "name": "Test / SOL",
            "symbol": "TEST", "age_sec": 120, "liq_usd": 20000,
            "mc_usd": 50000, "buyers2": 30, "top10_pct": 20,
            "lp_status": "locked", "price_usd": 0.000123, "message": "x",
        }
        self.assertTrue(db.record_alert(self.conn, row))
        saved = self.conn.execute(
            "SELECT price_usd FROM alerts WHERE mint=?", ("MintPrice",)
        ).fetchone()
        self.assertAlmostEqual(saved["price_usd"], 0.000123)

    def test_ensure_outcomes_creates_one_row_per_horizon(self):
        self._alert()
        created = db.ensure_outcomes(self.conn)
        self.assertEqual(created, len(config.OUTCOME_HORIZONS))
        self.assertEqual(db.ensure_outcomes(self.conn), 0)  # idempotent

    def test_due_outcomes_waits_until_horizon(self):
        alert_id = self._alert(created_at=time.time())
        db.ensure_outcomes(self.conn)
        self.assertEqual(len(db.due_outcomes(self.conn)), 0)

        rows = db.due_outcomes(self.conn, now=int(time.time()) + max(config.OUTCOME_HORIZONS) + 1)
        self.assertEqual(len(rows), len(config.OUTCOME_HORIZONS))
        self.assertTrue(all(r["alert_id"] == alert_id for r in rows))

    def test_save_outcome_removes_from_queue(self):
        self._alert()
        db.ensure_outcomes(self.conn)
        rows = db.due_outcomes(self.conn, now=int(time.time()) + max(config.OUTCOME_HORIZONS) + 1)
        row = rows[0]
        db.save_outcome(
            self.conn, row["alert_id"], row["horizon_sec"], 1.0, 1.4, 0.4, -0.1, 5
        )
        remaining = db.due_outcomes(
            self.conn, now=int(time.time()) + max(config.OUTCOME_HORIZONS) + 1
        )
        self.assertEqual(len(remaining), len(config.OUTCOME_HORIZONS) - 1)
        saved = self.conn.execute(
            "SELECT status, mfe FROM outcomes WHERE alert_id=? AND horizon_sec=?",
            (row["alert_id"], row["horizon_sec"]),
        ).fetchone()
        self.assertEqual(saved["status"], "ok")
        self.assertAlmostEqual(saved["mfe"], 0.4)

    def test_fail_outcome_retries_then_gives_up(self):
        self._alert()
        db.ensure_outcomes(self.conn)
        rows = db.due_outcomes(self.conn, now=int(time.time()) + max(config.OUTCOME_HORIZONS) + 1)
        row = rows[0]
        db.fail_outcome(self.conn, row["alert_id"], row["horizon_sec"], "kosong")
        pending = db.due_outcomes(
            self.conn, now=int(time.time()) + max(config.OUTCOME_HORIZONS) + 1
        )
        self.assertEqual(len(pending), len(config.OUTCOME_HORIZONS))  # masih dicoba

        db.fail_outcome(self.conn, row["alert_id"], row["horizon_sec"], "kosong", final=True)
        pending = db.due_outcomes(
            self.conn, now=int(time.time()) + max(config.OUTCOME_HORIZONS) + 1
        )
        self.assertEqual(len(pending), len(config.OUTCOME_HORIZONS) - 1)

    def test_retire_stale_marks_only_old_alerts(self):
        old_id = self._alert(mint="Old", created_at=time.time() - track.MAX_AGE_SEC - 60)
        new_id = self._alert(mint="New", created_at=time.time())
        db.ensure_outcomes(self.conn)
        retired = track.retire_stale(self.conn)
        self.assertEqual(retired, len(config.OUTCOME_HORIZONS))
        old = self.conn.execute(
            "SELECT status FROM outcomes WHERE alert_id=?", (old_id,)
        ).fetchall()
        new = self.conn.execute(
            "SELECT status FROM outcomes WHERE alert_id=?", (new_id,)
        ).fetchall()
        self.assertEqual({row["status"] for row in old}, {"no_data"})
        self.assertEqual({row["status"] for row in new}, {None})

    def test_migration_adds_price_column_to_old_alerts(self):
        old_path = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(old_path)
        conn.execute(
            """
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL UNIQUE,
                pool TEXT, name TEXT, symbol TEXT, created_at INTEGER,
                age_sec INTEGER, liq_usd REAL, mc_usd REAL, buyers2 INTEGER,
                top10_pct REAL, lp_status TEXT, message TEXT
            )
            """
        )
        conn.commit()
        conn.close()

        conn = db.connect(old_path)
        db.init(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
        self.assertIn("price_usd", columns)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
