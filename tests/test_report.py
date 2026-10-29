#!/usr/bin/env python3
"""Uji teks laporan harian (Inggris & Indonesia)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import i18n  # noqa: E402
import report  # noqa: E402


class ReportLanguageTests(unittest.TestCase):
    def test_horizon_labels(self):
        self.assertEqual(report._label(300, "en"), "+5m")
        self.assertEqual(report._label(3600, "en"), "+1h")
        self.assertEqual(report._label(21600, "id"), "+6j")

    def test_scan_line_is_translated(self):
        en = i18n.text("en", "report_scans") % (10, 400, 20, 2, 0)
        self.assertIn("rounds", en)
        self.assertIn("candidates", en)

        id_text = i18n.text("id", "report_scans") % (10, 400, 20, 2, 0)
        self.assertIn("putaran", id_text)
        self.assertIn("kandidat", id_text)

    def test_default_language_is_english(self):
        self.assertEqual(i18n.normalize(None), "en")
        self.assertIn(
            "Solana Pool Screener report", i18n.text(None, "report_title")
        )
        self.assertIn("Not measured yet", i18n.text(None, "report_pending"))

    def test_every_report_key_exists_in_both_languages(self):
        keys = [k for k in i18n.STRINGS["en"] if k.startswith("report_")]
        self.assertTrue(keys)
        for key in keys:
            self.assertIn(key, i18n.STRINGS["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
