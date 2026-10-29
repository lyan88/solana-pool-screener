#!/usr/bin/env python3
"""Uji watchdog tanpa jaringan dan tanpa mengirim Telegram."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import watchdog  # noqa: E402


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = watchdog.STATE_PATH
        watchdog.STATE_PATH = Path(self.tmp.name) / "watchdog_state.json"
        self.old_lang = config.ALERT_LANG
        config.ALERT_LANG = "en"
        self.sent = []

        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, ts INTEGER)")

    def tearDown(self):
        self.conn.close()
        watchdog.STATE_PATH = self.old_state
        config.ALERT_LANG = self.old_lang
        self.tmp.cleanup()

    def _sender(self, text):
        self.sent.append(text)
        return True, "terkirim"

    def _scan(self, ts):
        self.conn.execute("INSERT INTO scans(ts) VALUES(?)", (ts,))
        self.conn.commit()

    def test_empty_database_is_quiet(self):
        action, age, _ = watchdog.check(self.conn, now=1000, sender=self._sender)
        self.assertEqual(action, "empty")
        self.assertIsNone(age)
        self.assertEqual(self.sent, [])

    def test_fresh_scan_is_ok(self):
        self._scan(940)
        action, age, _ = watchdog.check(self.conn, now=1000, sender=self._sender)
        self.assertEqual(action, "ok")
        self.assertEqual(age, 60)
        self.assertEqual(self.sent, [])

    def test_stale_scan_alerts_once_then_waits(self):
        self._scan(200)
        action, age, _ = watchdog.check(self.conn, now=1000, sender=self._sender)
        self.assertEqual(action, "stale")
        self.assertEqual(age, 800)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("No scan since", self.sent[0])
        self.assertIn("13 min ago", self.sent[0])

        action, _, _ = watchdog.check(self.conn, now=1300, sender=self._sender)
        self.assertEqual(action, "cooldown")
        self.assertEqual(len(self.sent), 1)

        action, _, _ = watchdog.check(
            self.conn, now=1000 + config.WATCHDOG_REPEAT_SEC, sender=self._sender
        )
        self.assertEqual(action, "stale")
        self.assertEqual(len(self.sent), 2)

    def test_recovery_message_is_sent_once(self):
        self._scan(200)
        watchdog.check(self.conn, now=1000, sender=self._sender)
        self._scan(1500)

        action, _, _ = watchdog.check(self.conn, now=1520, sender=self._sender)
        self.assertEqual(action, "recovered")
        self.assertEqual(len(self.sent), 2)
        self.assertIn("is back", self.sent[1])

        action, _, _ = watchdog.check(self.conn, now=1560, sender=self._sender)
        self.assertEqual(action, "ok")
        self.assertEqual(len(self.sent), 2)

    def test_failed_alert_is_retried_next_run(self):
        self._scan(200)

        def failing(_text):
            return False, "jaringan mati"

        action, _, reason = watchdog.check(
            self.conn, now=1000, sender=failing
        )
        self.assertEqual(action, "stale_failed")
        self.assertEqual(reason, "jaringan mati")

        action, _, _ = watchdog.check(self.conn, now=1260, sender=self._sender)
        self.assertEqual(action, "stale")
        self.assertEqual(len(self.sent), 1)

    def test_state_is_not_written_in_dry_run(self):
        self._scan(200)
        watchdog.check(
            self.conn, now=1000, sender=self._sender, persist=False
        )
        self.assertFalse(watchdog.STATE_PATH.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
