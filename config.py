#!/usr/bin/env python3
"""
Konfigurasi Solana Pool Screener. Semua ambang batas bisa diubah lewat .env.

Filosofi: tolak cepat dengan data murah (umur, likuiditas, MC, buyers m5),
baru panggil API mahal (Rugcheck, trades) untuk kandidat yang tersisa.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SCREENER_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("SCREENER_DB", DATA_DIR / "screener2.db"))


def _load_env(path):
    """Pembaca .env sederhana, tanpa dependensi luar."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env(BASE_DIR / ".env")


def _s(key, default=""):
    return os.environ.get(key, default).strip()


def _f(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _b(key, default):
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "ya")


def _ints(key, default):
    raw = _s(key, default)
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(sorted(set(out))) or (300, 1800, 3600, 21600)


# ---------------------------------------------------------------- filter utama
MAX_AGE_SEC = _i("MAX_AGE_SEC", 600)          # < 10 menit
MIN_AGE_SEC = _i("MIN_AGE_SEC", 0)
MIN_LIQ_USD = _f("MIN_LIQ_USD", 5_000)        # likuiditas minimum
MIN_MC_USD = _f("MIN_MC_USD", 8_000)          # market cap minimum
MAX_MC_USD = _f("MAX_MC_USD", 100_000)        # market cap maksimum
TOP10_MAX_PCT = _f("TOP10_MAX_PCT", 35.0)     # top 10 < 35%
MIN_TOP10_HOLDERS = _i("MIN_TOP10_HOLDERS", 10)  # minimal holder yang dihitung
MIN_TOP10_TOTAL_PCT = _f("MIN_TOP10_TOTAL_PCT", 5.0)  # float minimum agar top10 berarti
MIN_UNIQUE_BUYERS = _i("MIN_UNIQUE_BUYERS", 15)
BUYER_WINDOW_SEC = _i("BUYER_WINDOW_SEC", 120)  # unique buyer dalam 2 menit

REQUIRE_MINT_REVOKED = _b("REQUIRE_MINT_REVOKED", True)
REQUIRE_FREEZE_REVOKED = _b("REQUIRE_FREEZE_REVOKED", True)
REJECT_RUGGED = _b("REJECT_RUGGED", True)

# LP: "burned" = token LP dikirim ke incinerator / supply nol.
# "locked" = LP dipegang locker dengan persentase tetap.
# Banyak pool pump.fun tidak punya token LP (dianggap burned oleh Rugcheck).
LP_MIN_LOCKED_PCT = _f("LP_MIN_LOCKED_PCT", 99.0)
LP_ACCEPT_LOCKED = _b("LP_ACCEPT_LOCKED", True)

# ---------------------------------------------------------------- sumber data
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"
GECKO_NETWORK = "solana"
SCAN_PAGES = _i("SCAN_PAGES", 2)
GECKO_MIN_INTERVAL = _f("GECKO_MIN_INTERVAL", 6.0)
RUGCHECK_MIN_INTERVAL = _f("RUGCHECK_MIN_INTERVAL", 2.0)
HTTP_TIMEOUT = _f("HTTP_TIMEOUT", 25.0)
HTTP_RETRIES = _i("HTTP_RETRIES", 3)
USER_AGENT = _s("USER_AGENT", "solana-pool-screener/1.0 (+https://github.com/lyan88/solana-pool-screener)")

# Keamanan cache: top holder & LP bisa berubah, tapi tidak perlu tiap detik.
SECURITY_CACHE_SEC = _i("SECURITY_CACHE_SEC", 120)

# Meteora DAMM v2: pool mati/blacklist sering muncul di GeckoTerminal dengan
# reserve_in_usd yang menyesatkan. API resmi ini gratis (10 RPS).
METEORA_BASE = "https://damm-v2.datapi.meteora.ag"
METEORA_MIN_INTERVAL = _f("METEORA_MIN_INTERVAL", 0.2)
METEORA_MIN_TVL_USD = _f("METEORA_MIN_TVL_USD", 1000)

# ---------------------------------------------------------------- outcome
# Harga dicatat pada jarak ini setelah alert, untuk mengukur kualitas sinyal.
OUTCOME_HORIZONS = _ints("OUTCOME_HORIZONS", "300,1800,3600,21600")
WIN_MFE_MIN = _f("WIN_MFE_MIN", 1.5)      # dianggap "menang" kalau MFE >= 1.5x
TRACK_MAX_ALERTS = _i("TRACK_MAX_ALERTS", 25)  # alert per putaran tracker
TRACK_LOOKBACK_DAYS = _i("TRACK_LOOKBACK_DAYS", 7)
TRACK_MAX_ATTEMPTS = _i("TRACK_MAX_ATTEMPTS", 3)
REPORT_HOURS = _i("REPORT_HOURS", 24)

# ---------------------------------------------------------------- Telegram
TELEGRAM_BOT_TOKEN = _s("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _s("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = _b("TELEGRAM_ENABLED", True)
# Bahasa pesan yang dilihat pengguna: "en" atau "id".
ALERT_LANG = _s("ALERT_LANG", "en").strip().lower()
if ALERT_LANG not in ("en", "id"):
    ALERT_LANG = "en"
TELEGRAM_MIN_INTERVAL_SEC = _f("TELEGRAM_MIN_INTERVAL_SEC", 3.0)
# Batas lama (20/jam, 100/hari) membuat alert hilang saat jam sibuk.
# Antrean di bawah tetap mencegah spam, jadi batas bisa lebih longgar.
TELEGRAM_MAX_PER_HOUR = _i("TELEGRAM_MAX_PER_HOUR", 40)
TELEGRAM_MAX_PER_DAY = _i("TELEGRAM_MAX_PER_DAY", 400)
DRY_RUN = _b("DRY_RUN", False)

# ---------------------------------------------------------------- antrean alert
# Alert yang lolos tapi belum bisa dikirim (kuota/jeda) disimpan dulu, lalu
# dikirim berdasarkan skor tertinggi. TTL mencegah alert basi ikut terkirim.
ALERT_QUEUE_TTL_SEC = _i("ALERT_QUEUE_TTL_SEC", 900)
ALERT_QUEUE_MAX_ATTEMPTS = _i("ALERT_QUEUE_MAX_ATTEMPTS", 3)
ALERT_DRAIN_MAX = _i("ALERT_DRAIN_MAX", 6)
# Kalau alert tertunda lebih lama dari ini, harga entry tidak dipakai lagi;
# tracker memakai harga lilin saat alert benar-benar terkirim.
ALERT_QUEUE_MAX_PRICE_AGE_SEC = _i("ALERT_QUEUE_MAX_PRICE_AGE_SEC", 90)

# ---------------------------------------------------------------- watchlist
# Kandidat yang hampir lolos dipantau ulang sebentar, supaya tidak hilang
# begitu saja saat keluar dari halaman new_pools GeckoTerminal.
WATCH_TTL_SEC = _i("WATCH_TTL_SEC", 600)
WATCH_INTERVAL_SEC = _i("WATCH_INTERVAL_SEC", 120)
WATCH_MAX_PER_ROUND = _i("WATCH_MAX_PER_ROUND", 4)
WATCH_MAX_CHECKS = _i("WATCH_MAX_CHECKS", 5)
WATCH_TOP10_MARGIN_PCT = _f("WATCH_TOP10_MARGIN_PCT", 5.0)
WATCH_LP_MIN_PCT = _f("WATCH_LP_MIN_PCT", 90.0)
WATCH_BUYERS_RATIO = _f("WATCH_BUYERS_RATIO", 0.6)

# ---------------------------------------------------------------- operasional
SCAN_INTERVAL_SEC = _i("SCAN_INTERVAL_SEC", 120)
LOG_KEEP_DAYS = _i("LOG_KEEP_DAYS", 14)


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def summary():
    return {
        "umur_maks": MAX_AGE_SEC,
        "likuiditas_min": MIN_LIQ_USD,
        "mc": (MIN_MC_USD, MAX_MC_USD),
        "top10_maks": TOP10_MAX_PCT,
        "top10_holder_min": MIN_TOP10_HOLDERS,
        "top10_total_min": MIN_TOP10_TOTAL_PCT,
        "buyer_unik": MIN_UNIQUE_BUYERS,
        "jendela_buyer": BUYER_WINDOW_SEC,
        "lp_locked_min": LP_MIN_LOCKED_PCT,
        "halaman_gecko": SCAN_PAGES,
        "horizon_outcome": OUTCOME_HORIZONS,
        "win_mfe": WIN_MFE_MIN,
        "meteora_tvl_min": METEORA_MIN_TVL_USD,
    }
