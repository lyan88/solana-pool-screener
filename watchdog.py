#!/usr/bin/env python3
"""
Watchdog: kirim peringatan kalau scanner berhenti menulis scan baru.

Cron menjalankan skrip ini tiap 5 menit. Kalau `scans` terakhir lebih tua
dari WATCHDOG_STALE_SEC, satu pesan dikirim ke Telegram. Selama masalah
berlanjut, peringatan diulang tiap WATCHDOG_REPEAT_SEC, dan pesan pemulihan
dikirim begitu scan kembali normal.

    python3 watchdog.py
    python3 watchdog.py --dry-run
"""

import argparse
import json
import time
from datetime import datetime, timezone

import config
import db
import i18n
import telegram

STATE_PATH = config.DATA_DIR / "watchdog_state.json"


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _message(key, last_ts, age_sec):
    stamp = datetime.fromtimestamp(int(last_ts), timezone.utc).strftime(
        "%d %b %H:%M"
    )
    return i18n.text(config.ALERT_LANG, key) % (stamp, max(0, int(age_sec) // 60))


def _send(text):
    # Peringatan operasional tidak boleh tertahan kuota alert biasa.
    return telegram.send(text, bypass_limits=True)


def check(conn, now=None, sender=None, persist=True):
    """
    Periksa scan terakhir.

    Return (action, age_sec, reason), action salah satu:
    empty / ok / cooldown / stale / stale_failed / recovered / recovered_failed.
    """
    now = int(time.time()) if now is None else int(now)
    sender = sender or _send
    row = conn.execute("SELECT MAX(ts) FROM scans").fetchone()
    last = row[0] if row is not None and row[0] is not None else None
    if last is None:
        return "empty", None, ""

    age = max(0, now - int(last))
    state = _load_state()
    was_stale = state.get("state") == "stale"

    if age > config.WATCHDOG_STALE_SEC:
        last_alert = int(state.get("last_alert_at") or 0)
        if was_stale and last_alert and now - last_alert < config.WATCHDOG_REPEAT_SEC:
            return "cooldown", age, ""

        ok, reason = sender(_message("watchdog_stale", last, age))
        state["state"] = "stale"
        if ok:
            state["last_alert_at"] = now
        if persist:
            _save_state(state)
        return ("stale" if ok else "stale_failed"), age, reason

    if was_stale:
        ok, reason = sender(_message("watchdog_recovered", last, age))
        if ok:
            state["state"] = "ok"
            state.pop("last_alert_at", None)
            if persist:
                _save_state(state)
        return ("recovered" if ok else "recovered_failed"), age, reason

    if state.get("state") != "ok":
        state["state"] = "ok"
        if persist:
            _save_state(state)
    return "ok", age, ""


def _age_text(age_sec):
    if age_sec is None:
        return "?"
    if age_sec < 120:
        return "%ds" % age_sec
    return "%dm" % (age_sec // 60)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Watchdog Solana Pool Screener")
    parser.add_argument("--dry-run", action="store_true", help="cetak, jangan kirim")
    args = parser.parse_args(argv)

    if not config.WATCHDOG_ENABLED:
        print("[%s] watchdog dimatikan" % time.strftime("%H:%M:%S"))
        return 0

    if args.dry_run:
        def sender(text):
            print(text)
            return False, "dry-run"
    else:
        sender = _send

    conn = db.connect()
    db.init(conn)
    action, age, reason = check(
        conn, sender=sender, persist=not args.dry_run
    )
    conn.close()

    stamp = time.strftime("%H:%M:%S")
    if action == "empty":
        print("[%s] belum ada scan" % stamp)
    elif action == "ok":
        print("[%s] ok, scan terakhir %s lalu" % (stamp, _age_text(age)))
    elif action == "cooldown":
        print("[%s] masih stale, peringatan ditahan" % stamp)
    elif action in ("stale", "recovered"):
        print(
            "[%s] %s, scan terakhir %s lalu"
            % (stamp, action, _age_text(age))
        )
    else:
        print("[%s] %s: %s" % (stamp, action, reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
