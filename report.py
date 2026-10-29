#!/usr/bin/env python3
"""
Laporan harian Solana Pool Screener ke Telegram.

Isinya: aktivitas scan, jumlah alert, dan performa tiap horizon
(win rate, median MFE/MAE, alert terbaik/terburuk).

    python3 report.py --dry-run
    python3 report.py --telegram
"""

import argparse
import html
import sys
import time
from datetime import datetime, timezone

import config
import db
import i18n
import telegram


def _e(value):
    return html.escape("" if value is None else str(value), quote=True)


def _pct(value, digits=1):
    if value is None:
        return "?"
    return "%+.*f%%" % (digits, value * 100.0)


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    if n % 2:
        return values[n // 2]
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def _label(seconds, lang="en"):
    if seconds >= 3600:
        return "+%d%s" % (seconds // 3600, "j" if lang == "id" else "h")
    return "+%dm" % (seconds // 60)


def _scan_totals(conn, since):
    row = conn.execute(
        """
        SELECT COUNT(*) AS rounds, COALESCE(SUM(fetched),0) AS fetched,
               COALESCE(SUM(candidates),0) AS candidates,
               COALESCE(SUM(evaluated),0) AS evaluated,
               COALESCE(SUM(alerted),0) AS alerted,
               COALESCE(SUM(failed),0) AS failed,
               COALESCE(SUM(errors),0) AS errors
        FROM scans WHERE ts >= ?
        """,
        (since,),
    ).fetchone()
    return row


def _outcomes(conn, since):
    return conn.execute(
        """
        SELECT o.horizon_sec, o.mfe, o.mae, o.price, o.entry_price,
               a.symbol, a.mint
        FROM outcomes o
        JOIN alerts a ON a.id = o.alert_id
        WHERE a.created_at >= ? AND o.checked_at IS NOT NULL AND o.status = 'ok'
        """,
        (since,),
    ).fetchall()


def _pending(conn, since):
    return conn.execute(
        """
        SELECT COUNT(*) FROM outcomes o
        JOIN alerts a ON a.id = o.alert_id
        WHERE a.created_at >= ? AND o.checked_at IS NULL
        """,
        (since,),
    ).fetchone()[0]


def _approx_entries(conn, since):
    """Alert lama tanpa price_usd memakai harga lilin sebagai entry."""
    return conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) FROM outcomes o
        JOIN alerts a ON a.id = o.alert_id
        WHERE a.created_at >= ? AND o.status = 'ok' AND a.price_usd IS NULL
        """,
        (since,),
    ).fetchone()[0]


def build(hours=None, lang=None):
    lang = i18n.normalize(lang or config.ALERT_LANG)
    hours = config.REPORT_HOURS if hours is None else hours
    now = int(time.time())
    since = now - hours * 3600

    conn = db.connect()
    db.init(conn)

    scans = _scan_totals(conn, since)
    alert_row = conn.execute(
        """
        SELECT COUNT(*) AS n, COUNT(DISTINCT mint) AS mints
        FROM alerts WHERE created_at >= ?
        """,
        (since,),
    ).fetchone()
    outcomes = _outcomes(conn, since)
    pending = _pending(conn, since)
    approx = _approx_entries(conn, since)
    conn.close()

    by_horizon = {}
    for row in outcomes:
        by_horizon.setdefault(row["horizon_sec"], []).append(row)

    end = datetime.fromtimestamp(now, timezone.utc)
    start = datetime.fromtimestamp(since, timezone.utc)
    lines = [
        i18n.text(lang, "report_title"),
        "%s–%s UTC" % (start.strftime("%d %b %H:%M"), end.strftime("%d %b %H:%M")),
        "",
        i18n.text(lang, "report_scan"),
        i18n.text(lang, "report_scans")
        % (
            scans["rounds"], scans["fetched"], scans["candidates"],
            scans["alerted"], scans["errors"] + scans["failed"],
        ),
        i18n.text(lang, "report_alerts") % (alert_row["n"], alert_row["mints"]),
        "",
        i18n.text(lang, "report_outcomes"),
    ]

    if not outcomes:
        lines.append(i18n.text(lang, "report_no_outcomes"))
    else:
        for horizon in config.OUTCOME_HORIZONS:
            rows = by_horizon.get(horizon) or []
            if not rows:
                lines.append(
                    i18n.text(lang, "report_horizon_nodata") % _label(horizon, lang)
                )
                continue
            mfes = [r["mfe"] for r in rows if r["mfe"] is not None]
            maes = [r["mae"] for r in rows if r["mae"] is not None]
            win = sum(1 for m in mfes if m >= config.WIN_MFE_MIN - 1.0)
            win_pct = (100.0 * win / len(mfes)) if mfes else 0.0
            lines.append(
                i18n.text(lang, "report_horizon")
                % (
                    _label(horizon, lang), len(rows), win_pct,
                    _pct(_median(mfes)), _pct(_median(maes)),
                )
            )

        # Pilih horizon terpanjang yang punya data untuk juara/pecundang.
        ranked_horizon = None
        for horizon in reversed(config.OUTCOME_HORIZONS):
            if by_horizon.get(horizon):
                ranked_horizon = horizon
                break
        rows = by_horizon.get(ranked_horizon) or []
        ranked = [r for r in rows if r["mfe"] is not None]
        if ranked:
            lines.append("")
            lines.append(
                i18n.text(lang, "report_best") % _label(ranked_horizon, lang)
            )
            for row in sorted(ranked, key=lambda r: r["mfe"], reverse=True)[:3]:
                lines.append(
                    "  $%s %s (MAE %s)"
                    % (_e(row["symbol"] or "?"), _pct(row["mfe"]), _pct(row["mae"]))
                )
            drawdown = [r for r in ranked if r["mae"] is not None]
            lines.append(
                i18n.text(lang, "report_worst") % _label(ranked_horizon, lang)
            )
            for row in sorted(drawdown, key=lambda r: r["mae"])[:3]:
                lines.append(
                    "  $%s %s (MFE %s)"
                    % (_e(row["symbol"] or "?"), _pct(row["mae"]), _pct(row["mfe"]))
                )

    lines += [
        "",
        i18n.text(lang, "report_pending") % pending,
    ]
    if approx:
        lines.append(i18n.text(lang, "report_approx") % approx)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Laporan harian Solana Pool Screener")
    parser.add_argument("--telegram", action="store_true", help="kirim ke Telegram")
    parser.add_argument("--dry-run", action="store_true", help="cetak saja")
    parser.add_argument("--hours", type=int, default=None, help="periode laporan")
    args = parser.parse_args(argv)

    text = build(hours=args.hours)
    print(text)
    if args.telegram and not args.dry_run:
        ok, reason = telegram.send(text, bypass_limits=True)
        print("\nTelegram: %s — %s" % ("OK" if ok else "GAGAL", reason))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
