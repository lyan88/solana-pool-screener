#!/usr/bin/env python3
"""
Screener Solana — pool baru, filter ketat, alert Telegram.

Satu putaran:
    python3 scanner.py --once

Terus-menerus (untuk uji lokal):
    python3 scanner.py --loop 120

Dry-run (tidak kirim Telegram, tetap cetak alert):
    python3 scanner.py --once --dry-run

Cron di VPS: lihat deploy/screener2.cron
"""

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone

import config
import db
import filters
import sources
import telegram


def log(message):
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, message), flush=True)


def _pick_pools(pools, mint_only=None):
    """Satu mint bisa punya beberapa pool; pilih likuiditas terbesar."""
    best = {}
    for pool in pools:
        if mint_only and pool.get("mint") != mint_only:
            continue
        mint = pool.get("mint")
        if not mint:
            continue
        current = best.get(mint)
        if current is None or (pool.get("liq_usd") or 0) > (current.get("liq_usd") or 0):
            best[mint] = pool
    return list(best.values())


def scan_once(pages=None, dry_run=False, mint_only=None, verbose=False):
    started = time.time()
    config.ensure_dirs()
    conn = db.connect()
    db.init(conn)

    now = datetime.now(timezone.utc)
    try:
        pools, fetch_errors = sources.fetch_new_pools(pages=pages)
    except sources.net.SourceError as exc:
        log("GAGAL ambil pool baru: %s" % exc)
        return 1

    candidates = _pick_pools(pools, mint_only=mint_only)
    for pool in candidates:
        created = pool.get("created_at")
        if created:
            pool["age_sec"] = max(0, int((now - created).total_seconds()))

    cheap = []
    for pool in candidates:
        ok, _check, detail = filters.evaluate_cheap(pool)
        if ok:
            cheap.append(pool)
        elif verbose:
            log("  skip %-18s %s" % ((pool.get("symbol") or "?")[:18], detail))
    cheap.sort(key=lambda p: p.get("age_sec") or 0)

    evaluated = passed = alerted = failed = 0
    for pool in cheap:
        mint = pool["mint"]
        if db.has_alerted(conn, mint):
            continue

        meteora = None
        if (pool.get("dex") or "").lower() in ("meteora-damm-v2", "meteora_damm_v2"):
            meteora = sources.fetch_meteora_pool(pool["pool"])

        if meteora is not None and filters.meteora_dead(meteora):
            # Pool mati/blacklist: tidak perlu panggil Rugcheck, hemat kuota.
            report = None
            sec = filters.evaluate_security(pool, None, meteora=meteora)
            if verbose:
                log(
                    "  pool mati %-12s %s"
                    % ((pool.get("symbol") or "?")[:12], sec["details"]["lp_detail"])
                )
        else:
            report = db.get_security(conn, mint)
            if report is None:
                try:
                    report = sources.fetch_rugcheck(mint)
                except sources.net.SourceError as exc:
                    log("  rugcheck gagal %s: %s" % (mint[:8], exc))
                    report = None
                if report:
                    db.put_security(conn, mint, report)
            sec = filters.evaluate_security(pool, report, meteora=meteora)
        evaluated += 1
        reasons = list(sec["reasons"])
        buyer_stats = None
        is_pass = bool(sec["passed"])

        if is_pass:
            try:
                trades = sources.fetch_trades(pool["pool"])
            except sources.net.SourceError as exc:
                log("  trades gagal %s: %s" % (mint[:8], exc))
                trades = None
                is_pass = False
                reasons.append("trades_unavailable")
            if trades is not None:
                ok, detail, buyer_stats = filters.check_buyers_window(
                    trades, now, created_at=pool.get("created_at")
                )
                if not ok:
                    is_pass = False
                    reasons.append("buyers")
                    if verbose:
                        log("  %-18s %s" % ((pool.get("symbol") or "?")[:18], detail))

        row = {
            "mint": mint,
            "pool": pool.get("pool"),
            "name": pool.get("name"),
            "symbol": pool.get("symbol"),
            "dex": pool.get("dex"),
            "age_sec": pool.get("age_sec"),
            "liq_usd": pool.get("liq_usd"),
            "mc_usd": pool.get("mc_usd"),
            "buyers5": pool.get("buyers5"),
            "top10_pct": sec.get("top10_pct"),
            "lp_status": sec["details"].get("lp_status"),
            "lp_pct": sec["details"].get("lp_pct"),
            "price_usd": pool.get("price_usd"),
            "buyers2": (buyer_stats or {}).get("buyers"),
            "buys2": (buyer_stats or {}).get("buys"),
            "sells2": (buyer_stats or {}).get("sells"),
            "passed": is_pass,
            "reasons": reasons,
        }

        if is_pass:
            passed += 1
            msg = telegram.format_alert(pool, sec, buyer_stats, now=now)
            if dry_run:
                log("  LOLOS %s — dry-run, tidak dikirim" % (pool.get("symbol") or "?"))
                print(msg)
                print()
            else:
                ok, reason = telegram.send(msg)
                if ok:
                    row["alerted_at"] = int(time.time())
                    db.record_alert(conn, dict(row, message=msg))
                    alerted += 1
                    log("  ALERT %s (%s)" % (pool.get("symbol") or "?", reason))
                else:
                    failed += 1
                    log("  LOLOS tapi Telegram gagal: %s" % reason)
        db.upsert_evaluation(conn, row)

        if verbose and not is_pass:
            log(
                "  tolak %-18s top10=%s lp=%s (%s)"
                % (
                    (pool.get("symbol") or "?")[:18],
                    "%.1f%%" % sec["top10_pct"] if sec.get("top10_pct") is not None else "?",
                    sec["details"].get("lp_status"),
                    ", ".join(reasons),
                )
            )

    if evaluated or cheap:
        db.prune(conn)
    db.record_scan(
        conn,
        {
            "ts": int(started),
            "fetched": len(pools),
            "candidates": len(cheap),
            "evaluated": evaluated,
            "passed": passed,
            "alerted": alerted,
            "failed": failed,
            "errors": len(fetch_errors),
            "duration_ms": int((time.time() - started) * 1000),
        },
    )
    log(
        "putaran selesai: fetched=%d kandidat=%d dievaluasi=%d lolos=%d alert=%d gagal=%d errors=%d"
        % (len(pools), len(cheap), evaluated, passed, alerted, failed, len(fetch_errors))
    )
    for err in fetch_errors:
        log("  sumber: %s" % err)
    conn.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Screener Solana pool baru -> Telegram")
    parser.add_argument("--once", action="store_true", help="satu putaran (default)")
    parser.add_argument("--loop", type=int, metavar="DETIK", help="ulangi tiap N detik")
    parser.add_argument("--pages", type=int, default=None, help="halaman GeckoTerminal")
    parser.add_argument("--dry-run", action="store_true", help="jangan kirim Telegram")
    parser.add_argument("--mint", help="hanya evaluasi mint tertentu (debug)")
    parser.add_argument("--verbose", action="store_true", help="cetak alasan penolakan")
    parser.add_argument("--test-telegram", action="store_true", help="kirim pesan uji lalu keluar")
    args = parser.parse_args(argv)

    if args.test_telegram:
        ok, reason = telegram.send_test()
        print("Telegram: %s" % ("OK — " + reason if ok else "GAGAL — " + reason))
        return 0 if ok else 1

    config.ensure_dirs()
    if args.loop:
        interval = max(30, int(args.loop))
        log("loop tiap %ds (Ctrl-C untuk berhenti)" % interval)
        while True:
            try:
                scan_once(args.pages, args.dry_run, args.mint, args.verbose)
            except KeyboardInterrupt:
                return 0
            except Exception:
                traceback.print_exc()
                log("putaran gagal, coba lagi")
            time.sleep(interval)

    return scan_once(args.pages, args.dry_run, args.mint, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
