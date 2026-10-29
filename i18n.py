#!/usr/bin/env python3
"""Teks yang dilihat pengguna (alert Telegram), dalam bahasa Inggris/Indonesia."""

DEFAULT_LANG = "en"
SUPPORTED = ("en", "id")

STRINGS = {
    "en": {
        "line_meta": "%s · %s · age %s",
        "buyers": (
            "Unique buyers %(window)ds: <b>%(buyers)d</b> "
            "(%(buys)d buys / %(sells)d sells)"
        ),
        "top10": "Top 10: <b>%s</b> (excl. pool/burn)",
        "auth": "Mint auth: %s · Freeze auth: %s",
        "lp": "LP: <b>%s</b>",
        "price": "Price: $%s",
        "revoked": "revoked ✅",
        "active": "ACTIVE ⚠️",
        "unknown": "unknown ❔",
        "lp_burned": "burned",
        "lp_locked": "locked",
        "lp_unlocked": "open",
        "lp_dead": "dead",
        "lp_unknown": "unknown",
        "delay_banner": (
            "⏳ <i>Delayed %s from detection — age & price below are as of "
            "detection.</i>"
        ),
        "test_ready": "✅ <b>Solana Pool Screener is ready</b>",
        "test_filter": (
            "Filters: mint/freeze revoked · LP burned/locked · top10 &lt; %.0f%% · "
            "MC $%s–$%s · liq ≥ $%s · age &lt; %dm · ≥ %d unique buyers/%ds."
        ),
        "report_title": "📊 <b>Solana Pool Screener report</b>",
        "report_scan": "<b>Scan</b>",
        "report_scans": "%d rounds · %d pools · %d candidates · %d alerts · %d errors",
        "report_alerts": "Alerts: <b>%d</b> (%d unique mints)",
        "report_outcomes": "<b>Alert outcomes</b>",
        "report_no_outcomes": "No measured outcomes in this period yet.",
        "report_horizon_nodata": "%s  no data yet",
        "report_horizon": "%s  n=%d  win %.0f%%  MFE %s  MAE %s",
        "report_best": "<b>Best</b> (%s, MFE)",
        "report_worst": "<b>Worst</b> (%s, MAE)",
        "report_pending": "Not measured yet: %d",
        "report_approx": (
            "Note: %d old alerts use the OHLCV candle price "
            "(the alert-time price was not stored)."
        ),
    },
    "id": {
        "line_meta": "%s · %s · umur %s",
        "buyers": (
            "Buyer unik %(window)ds: <b>%(buyers)d</b> "
            "(%(buys)d beli / %(sells)d jual)"
        ),
        "top10": "Top 10: <b>%s</b> (di luar pool/burn)",
        "auth": "Mint auth: %s · Freeze auth: %s",
        "lp": "LP: <b>%s</b>",
        "price": "Harga: $%s",
        "revoked": "revoked ✅",
        "active": "AKTIF ⚠️",
        "unknown": "tidak diketahui ❔",
        "lp_burned": "burned",
        "lp_locked": "locked",
        "lp_unlocked": "terbuka",
        "lp_dead": "mati",
        "lp_unknown": "tidak diketahui",
        "delay_banner": (
            "⏳ <i>Tertunda %s dari deteksi — umur & harga di bawah adalah saat "
            "deteksi.</i>"
        ),
        "test_ready": "✅ <b>Solana Pool Screener siap</b>",
        "test_filter": (
            "Filter: mint/freeze revoked · LP burned/locked · top10 &lt; %.0f%% · "
            "MC $%s–$%s · liq ≥ $%s · umur &lt; %dm · ≥ %d buyer unik/%ds."
        ),
        "report_title": "📊 <b>Laporan Solana Pool Screener</b>",
        "report_scan": "<b>Scan</b>",
        "report_scans": "%d putaran · %d pool · %d kandidat · %d alert · %d error",
        "report_alerts": "Alert: <b>%d</b> (%d mint unik)",
        "report_outcomes": "<b>Hasil alert</b>",
        "report_no_outcomes": "Belum ada outcome terukur pada periode ini.",
        "report_horizon_nodata": "%s  belum ada data",
        "report_horizon": "%s  n=%d  win %.0f%%  MFE %s  MAE %s",
        "report_best": "<b>Terbaik</b> (%s, MFE)",
        "report_worst": "<b>Terburuk</b> (%s, MAE)",
        "report_pending": "Belum terukur: %d",
        "report_approx": (
            "Catatan: %d alert lama memakai harga lilin OHLCV "
            "(harga saat alert tidak tersimpan)."
        ),
    },
}


def normalize(lang):
    lang = (lang or DEFAULT_LANG).strip().lower()
    return lang if lang in SUPPORTED else DEFAULT_LANG


def text(lang, key):
    return STRINGS[normalize(lang)][key]


def status_label(lang, status):
    key = "lp_%s" % (status or "unknown")
    return STRINGS[normalize(lang)].get(key, status or "unknown")
