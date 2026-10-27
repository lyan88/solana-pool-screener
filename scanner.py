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

Alur alert:
  lolos filter -> masuk antrean (skor) -> dikirim sesuai kuota
  Kandidat near-miss -> watchlist -> dievaluasi ulang beberapa menit
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


def _evaluate_pool(conn, pool, now, verbose=False):
    """
    Evaluasi satu pool. Kembalikan (row, sec, buyer_stats, is_pass).

    Semua kegagalan API yang wajar ditangani di sini; exception tak terduga
    dibiarkan naik supaya pemanggil bisa mengisolasi kandidat tersebut.
    """
    mint = pool["mint"]

    meteora = None
    if (pool.get("dex") or "").lower() in ("meteora-damm-v2", "meteora_damm_v2"):
        meteora = sources.fetch_meteora_pool(pool["pool"])

    if meteora is not None and filters.meteora_dead(meteora):
        # Pool mati/blacklist: tidak perlu panggil Rugcheck, hemat kuota.
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
    return row, sec, buyer_stats, is_pass


def _queue_or_print(conn, pool, row, sec, buyer_stats, dry_run, stats):
    message = telegram.format_alert(pool, sec, buyer_stats)
    if dry_run:
        log("  LOLOS %s — dry-run, tidak dikirim" % (pool.get("symbol") or "?"))
        print(message)
        print()
        return

    score = filters.alert_score(pool, sec, buyer_stats)
    ttl = min(config.ALERT_QUEUE_TTL_SEC, max(120, config.MAX_AGE_SEC - (row.get("age_sec") or 0)))
    if db.queue_alert(conn, row, score, message, ttl=ttl):
        stats["queued"] += 1
        db.watch_finish(conn, row["mint"], "queued")
        log("  antre %s (skor %.1f)" % ((pool.get("symbol") or "?")[:18], score))


def _maybe_watch(conn, pool, row, sec, stats):
    """Simpan kandidat near-miss supaya dievaluasi ulang sebentar lagi."""
    if not filters.near_miss(row):
        return
    remaining = config.MAX_AGE_SEC - (row.get("age_sec") or 0)
    if remaining < 60:
        return
    ttl = min(config.WATCH_TTL_SEC, remaining)
    score = filters.alert_score(
        pool, sec, {"buyers": row.get("buyers2") or pool.get("buyers5") or 0}
    )
    db.watch_upsert(
        conn, pool, row.get("reasons"), score, ttl=ttl,
        interval=config.WATCH_INTERVAL_SEC,
    )
    stats["watched"] += 1


def _process_pool(conn, pool, now, dry_run, verbose, stats):
    """Evaluasi + catat + antrekan satu pool. Return True kalau diproses."""
    mint = pool["mint"]
    if db.has_alerted(conn, mint):
        db.watch_finish(conn, mint, "alerted")
        return False
    if db.has_pending_alert(conn, mint):
        return False

    row, sec, buyer_stats, is_pass = _evaluate_pool(conn, pool, now, verbose=verbose)
    stats["evaluated"] += 1
    db.upsert_evaluation(conn, row)

    if is_pass:
        stats["passed"] += 1
        _queue_or_print(conn, pool, row, sec, buyer_stats, dry_run, stats)
    else:
        _maybe_watch(conn, pool, row, sec, stats)
        if verbose:
            log(
                "  tolak %-18s top10=%s lp=%s (%s)"
                % (
                    (pool.get("symbol") or "?")[:18],
                    "%.1f%%" % sec["top10_pct"] if sec.get("top10_pct") is not None else "?",
                    sec["details"].get("lp_status"),
                    ", ".join(row["reasons"]),
                )
            )
    return True


def _process_watchlist(conn, now, dry_run, verbose, stats, already_seen):
    """Evaluasi ulang kandidat near-miss yang sudah jatuh tempo."""
    now_ts = int(now.timestamp())
    db.watch_expire(conn, now_ts)
    rows = db.watch_due(conn, now_ts, limit=config.WATCH_MAX_PER_ROUND)
    for watch in rows:
        mint = watch["mint"]
        if mint in already_seen:
            # Sudah dievaluasi dari new_pools pada putaran ini.
            continue
        if db.has_alerted(conn, mint):
            db.watch_finish(conn, mint, "alerted")
            continue
        if db.has_pending_alert(conn, mint):
            db.watch_finish(conn, mint, "queued")
            continue

        try:
            pool = sources.fetch_pool(watch["pool"])
        except sources.net.SourceError as exc:
            log("  watch gagal %s: %s" % ((watch["symbol"] or "?")[:12], exc))
            _watch_next(conn, watch, watch["reasons"], watch["best_score"])
            continue

        if not pool:
            db.watch_finish(conn, mint, "dropped")
            continue

        created = pool.get("created_at")
        if created:
            pool["age_sec"] = max(0, int((now - created).total_seconds()))

        ok, _check, detail = filters.evaluate_cheap(pool)
        if not ok:
            if verbose:
                log("  watch buang %-12s %s" % ((watch["symbol"] or "?")[:12], detail))
            db.watch_finish(conn, mint, "dropped")
            continue

        row, sec, buyer_stats, is_pass = _evaluate_pool(conn, pool, now, verbose=verbose)
        stats["evaluated"] += 1
        db.upsert_evaluation(conn, row)

        if is_pass:
            stats["passed"] += 1
            _queue_or_print(conn, pool, row, sec, buyer_stats, dry_run, stats)
            db.watch_finish(conn, mint, "queued")
            continue

        if filters.near_miss(row):
            _watch_next(
                conn, watch, row.get("reasons"),
                filters.alert_score(
                    pool, sec,
                    {"buyers": row.get("buyers2") or pool.get("buyers5") or 0},
                ),
            )
        else:
            db.watch_finish(conn, mint, "dropped")


def _watch_next(conn, watch, reasons, score):
    checks = int(watch["checks"] or 0) + 1
    if checks >= config.WATCH_MAX_CHECKS:
        db.watch_finish(conn, watch["mint"], "dropped")
        return
    db.watch_touch(conn, watch["mint"], reasons, score)


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
        # Antrean tetap dicoba walau sumber sedang bermasalah.
        if not dry_run:
            result = telegram.drain_queue(conn)
            if result["sent"] or result["failed"]:
                log("antrean: %d terkirim, %d gagal" % (result["sent"], result["failed"]))
        conn.close()
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

    stats = {
        "evaluated": 0, "passed": 0, "queued": 0,
        "watched": 0, "errors": 0, "alerted": 0, "failed": 0,
    }
    seen = set()

    # 1) Kandidat baru dari halaman new_pools.
    for pool in cheap:
        seen.add(pool["mint"])
        try:
            _process_pool(conn, pool, now, dry_run, verbose, stats)
        except Exception as exc:  # noqa: BLE001 - satu kandidat tidak boleh mematikan putaran
            stats["errors"] += 1
            log(
                "  ERROR %-18s %s"
                % ((pool.get("symbol") or pool["mint"][:8])[:18], exc)
            )
            traceback.print_exc()

    # 2) Near-miss yang sudah keluar dari new_pools.
    try:
        _process_watchlist(conn, now, dry_run, verbose, stats, seen)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] += 1
        log("  ERROR watchlist: %s" % exc)
        traceback.print_exc()

    # 3) Kirim antrean (prioritas skor tertinggi).
    if not dry_run:
        result = telegram.drain_queue(conn)
        stats["alerted"] = result["sent"]
        stats["failed"] = result["failed"]
        if result["sent"] or result["failed"]:
            log(
                "antrean: %d terkirim, %d gagal"
                % (result["sent"], result["failed"])
            )

    if stats["evaluated"] or cheap:
        db.prune(conn)
    db.record_scan(
        conn,
        {
            "ts": int(started),
            "fetched": len(pools),
            "candidates": len(cheap),
            "evaluated": stats["evaluated"],
            "passed": stats["passed"],
            "alerted": stats["alerted"],
            "failed": stats["failed"],
            "errors": len(fetch_errors) + stats["errors"],
            "duration_ms": int((time.time() - started) * 1000),
        },
    )
    log(
        "putaran selesai: fetched=%d kandidat=%d dievaluasi=%d lolos=%d "
        "alert=%d gagal=%d antre=%d watch=%d errors=%d"
        % (
            len(pools), len(cheap), stats["evaluated"], stats["passed"],
            stats["alerted"], stats["failed"], stats["queued"], stats["watched"],
            len(fetch_errors) + stats["errors"],
        )
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
