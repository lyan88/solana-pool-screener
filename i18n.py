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
