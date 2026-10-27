#!/usr/bin/env python3
"""
Pelacak hasil alert.

Untuk setiap alert, catat harga pada +5m, +30m, +1j, dan +6j memakai
OHLCV GeckoTerminal. MFE (max favorable excursion) dan MAE (max adverse
excursion) dihitung dari high/low lilin, jadi bukan sekadar harga akhir.

    python3 track.py              # proses yang sudah jatuh tempo
    python3 track.py --backfill   # siapkan baris outcome untuk alert lama
    python3 track.py --status     # ringkasan antrean
    python3 track.py --dry-run    # tidak menulis apa pun
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import config
import db
import sources

MAX_AGE_SEC = 16 * 3600  # 1000 lilin 1 menit ≈ 16,6 jam


def log(message):
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, message), flush=True)


def _entry_price(alert, candles, alert_ts):
    price = alert["price_usd"]
    if price and float(price) > 0:
        return float(price)
    for candle in candles:
        if candle["ts"] >= alert_ts - 60 and candle["open"] > 0:
            return candle["open"]
    for candle in candles:
        if candle["close"] > 0:
            return candle["close"]
    return None


def _measure(alert, horizon, candles, alert_ts):
    entry = _entry_price(alert, candles, alert_ts)
    if not entry:
        return None, "harga masuk tidak tersedia"

    end = alert_ts + horizon
    window = [c for c in candles if alert_ts - 60 <= c["ts"] <= end + 60]
    if not window:
        return None, "tidak ada lilin pada jendela %ds" % horizon

    highs = [c["high"] for c in window if c["high"] > 0]
    lows = [c["low"] for c in window if c["low"] > 0]
    if not highs or not lows:
        return None, "lilin tanpa harga"

    price = window[-1]["close"]
    mfe = max(highs) / entry - 1.0
    mae = min(lows) / entry - 1.0
    # Lilin 1 menit hanya ada kalau ada trade. Kalau lilin terakhir jauh
    # sebelum horizon, pool kemungkinan sudah berhenti — harga itu basi.
    tol = max(120, min(300, horizon // 10))
    stale = end - window[-1]["ts"]
    if stale > tol:
        return None, "lilin terakhir %ds sebelum horizon (pool berhenti?)" % stale
    return {
        "entry": entry,
        "price": price,
        "mfe": mfe,
        "mae": mae,
        "candles": len(window),
    }, None


def _group_by_alert(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["alert_id"], []).append(row)
    return grouped


def retire_stale(conn, max_age_sec=None):
    """
    Tandai outcome alert yang sudah terlalu tua sebagai no_data.

    OHLCV 1 menit dibatasi 1000 lilin (~16,6 jam), jadi alert yang lebih tua
    tidak akan pernah bisa diukur. Menandainya sekarang mencegah antrean
    penuh percobaan yang pasti gagal.
    """
    max_age_sec = MAX_AGE_SEC if max_age_sec is None else max_age_sec
    now = int(time.time())
    cur = conn.execute(
        """
        UPDATE outcomes
        SET checked_at = ?, status = 'no_data',
            last_error = 'alert lebih tua dari jendela OHLCV 1000 lilin'
        WHERE checked_at IS NULL
          AND alert_id IN (SELECT id FROM alerts WHERE created_at < ?)
        """,
        (now, now - max_age_sec),
    )
    conn.commit()
    return cur.rowcount


def run(dry_run=False, limit=None):
    config.ensure_dirs()
    conn = db.connect()
    db.init(conn)

    created = db.ensure_outcomes(conn)
    if created:
        log("antrean outcome baru: %d" % created)

    now = int(time.time())
    rows = db.due_outcomes(conn, now=now, limit=limit)
    if not rows:
        log("tidak ada outcome yang jatuh tempo")
        conn.close()
        return 0

    groups = _group_by_alert(rows)
    log("memproses %d alert (%d horizon)" % (len(groups), len(rows)))

    ok_count = fail_count = 0
    for alert_id, items in groups.items():
        alert = items[0]
        alert_ts = int(alert["created_at"])
        age = now - alert_ts

        if age > MAX_AGE_SEC:
            for item in items:
                if not dry_run:
                    db.fail_outcome(
                        conn, alert_id, item["horizon_sec"],
                        "alert terlalu lama untuk OHLCV 1000 lilin", final=True,
                    )
            log("  lewati %-12s umur %.1fj" % ((alert["symbol"] or "?")[:12], age / 3600.0))
            fail_count += len(items)
            continue

        try:
            candles = sources.fetch_ohlcv(alert["pool"])
        except sources.net.SourceError as exc:
            for item in items:
                final = (item["attempts"] + 1) >= config.TRACK_MAX_ATTEMPTS
                if not dry_run:
                    db.fail_outcome(conn, alert_id, item["horizon_sec"], exc, final=final)
            log("  gagal  %-12s %s" % ((alert["symbol"] or "?")[:12], exc))
            fail_count += len(items)
            continue

        if not candles:
            for item in items:
                final = (item["attempts"] + 1) >= config.TRACK_MAX_ATTEMPTS
                if not dry_run:
                    db.fail_outcome(
                        conn, alert_id, item["horizon_sec"],
                        "OHLCV kosong", final=final,
                    )
            log("  kosong %-12s" % (alert["symbol"] or "?"))
            fail_count += len(items)
            continue

        for item in items:
            result, error = _measure(alert, item["horizon_sec"], candles, alert_ts)
            if error:
                final = (item["attempts"] + 1) >= config.TRACK_MAX_ATTEMPTS
                if not dry_run:
                    db.fail_outcome(conn, alert_id, item["horizon_sec"], error, final=final)
                fail_count += 1
                continue
            if not dry_run:
                db.save_outcome(
                    conn, alert_id, item["horizon_sec"],
                    result["entry"], result["price"], result["mfe"],
                    result["mae"], result["candles"],
                )
            ok_count += 1
            log(
                "  %-12s %-6s MFE %+6.1f%%  MAE %+6.1f%%  (%d lilin)"
                % (
                    (alert["symbol"] or "?")[:12],
                    _label(item["horizon_sec"]),
                    result["mfe"] * 100.0,
                    result["mae"] * 100.0,
                    result["candles"],
                )
            )

    log("selesai: %d terukur, %d gagal" % (ok_count, fail_count))
    conn.close()
    return 0


def _label(seconds):
    if seconds % 3600 == 0:
        return "%dj" % (seconds // 3600)
    return "%dm" % (seconds // 60)


def show_status():
    conn = db.connect()
    db.init(conn)
    stats = db.stats(conn)
    print("alert            : %d (%d/24 jam)" % (stats["alerts"], stats["alerts_24h"]))
    print("scan             : %d/24 jam" % stats["scans_24h"])
    print("outcome pending  : %d" % stats["pending_outcomes"])
    for status, count in sorted(db.outcome_status(conn).items()):
        print("  %-14s %d" % (status, count))
    conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pelacak hasil alert screener2")
    parser.add_argument("--backfill", action="store_true", help="siapkan outcome untuk alert lama")
    parser.add_argument("--status", action="store_true", help="ringkasan antrean")
    parser.add_argument("--dry-run", action="store_true", help="jangan tulis ke database")
    parser.add_argument("--limit", type=int, help="maksimum horizon per putaran")
    args = parser.parse_args(argv)

    if args.status:
        show_status()
        return 0
    if args.backfill:
        conn = db.connect()
        db.init(conn)
        created = db.ensure_outcomes(conn)
        retired = retire_stale(conn)
        print("baris outcome dibuat: %d" % created)
        print("baris outcome no_data (alert terlalu tua): %d" % retired)
        conn.close()
        return 0
    return run(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
