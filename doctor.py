#!/usr/bin/env python3
"""
Pemeriksaan cepat sebelum mengaktifkan cron.

    python3 doctor.py
    python3 doctor.py --telegram   # kirim satu pesan uji

Keluar dengan kode 1 kalau ada pemeriksaan yang gagal.
"""

import argparse
import sys
import time

import config
import db
import filters
import sources
import telegram

PASS = FAIL = WARN = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print("  ok    %-34s %s" % (name, detail))


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    print("  GAGAL %-34s %s" % (name, detail))


def warn(name, detail=""):
    global WARN
    WARN += 1
    print("  hati2 %-34s %s" % (name, detail))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram", action="store_true", help="kirim pesan uji")
    args = parser.parse_args()

    print("=" * 62)
    print(" DOCTOR — Solana Pool Screener")
    print("=" * 62)
    print(" waktu UTC : %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
    print(" filter    : %s" % config.summary())

    print("\n[berkas]")
    config.ensure_dirs()
    probe = config.DATA_DIR / ".write_test"
    try:
        probe.write_text("x")
        probe.unlink()
        ok("data dir bisa ditulis", str(config.DATA_DIR))
    except OSError as exc:
        bad("data dir", str(exc))

    print("\n[database]")
    try:
        conn = db.connect()
        db.init(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        ok("integritas", integrity)
        stats = db.stats(conn)
        ok("isi", "%d evaluasi, %d alert (%d/24 jam)" % (
            stats["evaluations"], stats["alerts"], stats["alerts_24h"]))
    except Exception as exc:  # noqa: BLE001 - doctor harus menangkap semua
        bad("database", str(exc))
        conn = None

    print("\n[telegram]")
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        ok("kredensial", "token & chat id terisi")
    else:
        bad("kredensial", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID kosong")
    if args.telegram:
        sent, reason = telegram.send_test()
        (ok if sent else bad)("pesan uji", reason)

    print("\n[jaringan]")
    try:
        pools, errors = sources.fetch_new_pools(pages=1)
        ok("geckoterminal", "%d pool baru, %d error" % (len(pools), len(errors)))
        first = pools[0] if pools else None
    except Exception as exc:  # noqa: BLE001
        bad("geckoterminal", str(exc))
        first = None

    if first:
        report = sources.fetch_rugcheck(first["mint"])
        if not report:
            warn("rugcheck", "token pertama belum terindeks (normal untuk detik awal)")
        else:
            pct, _excluded, _detail = filters.top10(report, first["pool"])
            status, lp_pct, _detail = filters.lp_state(
                report, first["pool"], dex=first.get("dex")
            )
            ok(
                "rugcheck",
                "mintAuth=%s freeze=%s top10=%s lp=%s(%s)"
                % (
                    report.get("mintAuthority"),
                    report.get("freezeAuthority"),
                    "%.1f%%" % pct if pct is not None else "?",
                    status,
                    "%.1f%%" % lp_pct if lp_pct is not None else "?",
                ),
            )

    print("\n" + "=" * 62)
    print(" %d ok, %d peringatan, %d gagal" % (PASS, WARN, FAIL))
    print("=" * 62)
    if conn:
        conn.close()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
