#!/usr/bin/env python3
"""Kirim alert ke Telegram, dengan batas laju dan state harian."""

import html
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import config
import filters

STATE_PATH = config.DATA_DIR / "telegram_state.json"


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sent": []}


def _save_state(state):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _rate_ok(state):
    now = time.time()
    sent = [float(x) for x in state.get("sent", []) if now - float(x) < 86400]
    state["sent"] = sent
    hour = [x for x in sent if now - x < 3600]
    if len(hour) >= config.TELEGRAM_MAX_PER_HOUR:
        return False, "batas %d pesan/jam tercapai" % config.TELEGRAM_MAX_PER_HOUR
    if len(sent) >= config.TELEGRAM_MAX_PER_DAY:
        return False, "batas %d pesan/hari tercapai" % config.TELEGRAM_MAX_PER_DAY
    if sent and now - sent[-1] < config.TELEGRAM_MIN_INTERVAL_SEC:
        return False, "jeda minimum %.0fs" % config.TELEGRAM_MIN_INTERVAL_SEC
    return True, ""


def send(text, dry_run=None, disable_preview=True, bypass_limits=False):
    dry_run = config.DRY_RUN if dry_run is None else dry_run
    if not config.TELEGRAM_ENABLED:
        return False, "Telegram dimatikan"
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi"
    if dry_run:
        return False, "dry-run"

    state = _load_state()
    if not bypass_limits:
        ok, reason = _rate_ok(state)
        if not ok:
            return False, reason

    url = "https://api.telegram.org/bot%s/sendMessage" % config.TELEGRAM_BOT_TOKEN
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true" if disable_preview else "false",
    }
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, str(exc)
    if not body.get("ok"):
        return False, str(body.get("description") or body)[:200]

    state = _load_state()
    state.setdefault("sent", []).append(time.time())
    state["sent"] = state["sent"][-500:]
    _save_state(state)
    return True, "terkirim"


def _e(value):
    return html.escape("" if value is None else str(value), quote=True)


def format_alert(pool, sec, buyers, now=None):
    now = now or datetime.now(timezone.utc)
    age = pool.get("age_sec") or 0
    age_text = "%dm%02ds" % (age // 60, age % 60)
    lp_status = (sec.get("details") or {}).get("lp_status") or "unknown"
    lp_pct = (sec.get("details") or {}).get("lp_pct")
    lp_label = {"burned": "burned", "locked": "locked", "unlocked": "terbuka", "unknown": "tidak diketahui"}.get(lp_status, lp_status)
    if lp_pct is not None:
        lp_text = "%s %.1f%%" % (lp_label, float(lp_pct))
    else:
        lp_text = lp_label

    mc = pool.get("mc_usd")
    liq = pool.get("liq_usd")
    buyers2 = buyers or {}
    top10 = sec.get("top10_pct")

    lines = [
        "<b>$%s</b>" % _e(pool.get("symbol") or "?"),
        "%s · %s · umur %s" % (_e(pool.get("name") or "?"), _e(pool.get("dex") or "?"), age_text),
        "",
        "MC <b>$%s</b> · Liq <b>$%s</b>" % (filters.fmt_usd(mc), filters.fmt_usd(liq)),
        "Buyer unik %ds: <b>%d</b> (%d beli / %d jual)" % (
            buyers2.get("window_sec", config.BUYER_WINDOW_SEC),
            buyers2.get("buyers", 0), buyers2.get("buys", 0), buyers2.get("sells", 0),
        ),
        "Top 10: <b>%s</b> (di luar pool/burn)" % (
            "%.1f%%" % top10 if top10 is not None else "?"
        ),
        "Mint: revoked ✅ · Freeze: revoked ✅",
        "LP: <b>%s</b>" % _e(lp_text),
        "",
        "Harga: $%s" % _e(("%.10g" % pool["price_usd"]) if pool.get("price_usd") else "?"),
        "Mint: <code>%s</code>" % _e(pool.get("mint")),
        "Pool: <code>%s</code>" % _e(pool.get("pool")),
        "",
        '<a href="https://dexscreener.com/solana/%s">DexScreener</a> · '
        '<a href="https://rugcheck.xyz/tokens/%s">Rugcheck</a> · '
        '<a href="https://solscan.io/token/%s">Solscan</a>' % (
            urllib.parse.quote(pool.get("pool") or ""),
            urllib.parse.quote(pool.get("mint") or ""),
            urllib.parse.quote(pool.get("mint") or ""),
        ),
    ]
    return "\n".join(lines)


def send_test():
    text = (
        "✅ <b>screener2 siap</b>\n"
        "Filter: mint/freeze revoked · LP burned/locked · top10 &lt; %.0f%% · "
        "MC $%s–$%s · liq ≥ $%s · umur &lt; %dm · ≥ %d buyer unik/%ds."
        % (
            config.TOP10_MAX_PCT,
            filters.fmt_usd(config.MIN_MC_USD), filters.fmt_usd(config.MAX_MC_USD),
            filters.fmt_usd(config.MIN_LIQ_USD),
            config.MAX_AGE_SEC // 60, config.MIN_UNIQUE_BUYERS,
            config.BUYER_WINDOW_SEC,
        )
    )
    return send(text)
