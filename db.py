#!/usr/bin/env python3
"""SQLite: kandidat yang pernah dilihat, alert yang sudah dikirim, cache Rugcheck."""

import json
import sqlite3
import time
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
    mint TEXT PRIMARY KEY,
    pool TEXT,
    name TEXT,
    symbol TEXT,
    dex TEXT,
    first_seen INTEGER,
    last_seen INTEGER,
    age_sec INTEGER,
    liq_usd REAL,
    mc_usd REAL,
    buyers5 INTEGER,
    top10_pct REAL,
    lp_status TEXT,
    lp_pct REAL,
    buyers2 INTEGER,
    buys2 INTEGER,
    sells2 INTEGER,
    passed INTEGER,
    reasons TEXT,
    alerted_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_eval_last ON evaluations(last_seen);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL UNIQUE,
    pool TEXT,
    name TEXT,
    symbol TEXT,
    created_at INTEGER,
    age_sec INTEGER,
    liq_usd REAL,
    mc_usd REAL,
    buyers2 INTEGER,
    top10_pct REAL,
    lp_status TEXT,
    price_usd REAL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    fetched INTEGER,
    candidates INTEGER,
    evaluated INTEGER,
    passed INTEGER,
    alerted INTEGER,
    failed INTEGER,
    errors INTEGER,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scans_ts ON scans(ts);

CREATE TABLE IF NOT EXISTS outcomes (
    alert_id INTEGER NOT NULL,
    horizon_sec INTEGER NOT NULL,
    due_at INTEGER NOT NULL,
    checked_at INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    entry_price REAL,
    price REAL,
    mfe REAL,
    mae REAL,
    candles INTEGER,
    status TEXT,
    last_error TEXT,
    PRIMARY KEY (alert_id, horizon_sec)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_due ON outcomes(checked_at, due_at);

CREATE TABLE IF NOT EXISTS security_cache (
    mint TEXT PRIMARY KEY,
    fetched_at INTEGER,
    report TEXT
);
"""


def connect(path=None):
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    # Migrasi ringan untuk database yang dibuat sebelum kolom price_usd ada.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    if "price_usd" not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN price_usd REAL")
    conn.commit()


def get_security(conn, mint, ttl=None):
    ttl = config.SECURITY_CACHE_SEC if ttl is None else ttl
    row = conn.execute(
        "SELECT fetched_at, report FROM security_cache WHERE mint = ?", (mint,)
    ).fetchone()
    if not row:
        return None
    if time.time() - row["fetched_at"] > ttl:
        return None
    try:
        return json.loads(row["report"])
    except (TypeError, ValueError):
        return None


def put_security(conn, mint, report):
    if not report:
        return
    conn.execute(
        "INSERT INTO security_cache(mint, fetched_at, report) VALUES(?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET fetched_at=excluded.fetched_at, report=excluded.report",
        (mint, int(time.time()), json.dumps(report, separators=(",", ":"))),
    )
    conn.commit()


def has_alerted(conn, mint):
    row = conn.execute("SELECT 1 FROM alerts WHERE mint = ?", (mint,)).fetchone()
    return row is not None


def upsert_evaluation(conn, row):
    now = int(time.time())
    existing = conn.execute(
        "SELECT first_seen, alerted_at FROM evaluations WHERE mint = ?", (row["mint"],)
    ).fetchone()
    first_seen = existing["first_seen"] if existing else now
    alerted_at = existing["alerted_at"] if existing else row.get("alerted_at")
    if row.get("alerted_at"):
        alerted_at = row["alerted_at"]
    conn.execute(
        """
        INSERT INTO evaluations(
            mint, pool, name, symbol, dex, first_seen, last_seen, age_sec,
            liq_usd, mc_usd, buyers5, top10_pct, lp_status, lp_pct,
            buyers2, buys2, sells2, passed, reasons, alerted_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mint) DO UPDATE SET
            pool=excluded.pool, name=excluded.name, symbol=excluded.symbol,
            dex=excluded.dex, last_seen=excluded.last_seen, age_sec=excluded.age_sec,
            liq_usd=excluded.liq_usd, mc_usd=excluded.mc_usd,
            buyers5=excluded.buyers5, top10_pct=excluded.top10_pct,
            lp_status=excluded.lp_status, lp_pct=excluded.lp_pct,
            buyers2=excluded.buyers2, buys2=excluded.buys2, sells2=excluded.sells2,
            passed=excluded.passed, reasons=excluded.reasons,
            alerted_at=COALESCE(excluded.alerted_at, evaluations.alerted_at)
        """,
        (
            row["mint"], row.get("pool"), row.get("name"), row.get("symbol"),
            row.get("dex"), first_seen, now, row.get("age_sec"),
            row.get("liq_usd"), row.get("mc_usd"), row.get("buyers5"),
            row.get("top10_pct"), row.get("lp_status"), row.get("lp_pct"),
            row.get("buyers2"), row.get("buys2"), row.get("sells2"),
            1 if row.get("passed") else 0, json.dumps(row.get("reasons") or []),
            alerted_at,
        ),
    )
    conn.commit()


def record_alert(conn, row):
    try:
        conn.execute(
            """
            INSERT INTO alerts(mint, pool, name, symbol, created_at, age_sec,
                               liq_usd, mc_usd, buyers2, top10_pct, lp_status,
                               price_usd, message)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["mint"], row.get("pool"), row.get("name"), row.get("symbol"),
                int(time.time()), row.get("age_sec"), row.get("liq_usd"),
                row.get("mc_usd"), row.get("buyers2"), row.get("top10_pct"),
                row.get("lp_status"), row.get("price_usd"), row.get("message"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def record_scan(conn, row):
    conn.execute(
        """
        INSERT INTO scans(ts, fetched, candidates, evaluated, passed, alerted,
                          failed, errors, duration_ms)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            int(row.get("ts") or time.time()), row.get("fetched"),
            row.get("candidates"), row.get("evaluated"), row.get("passed"),
            row.get("alerted"), row.get("failed"), row.get("errors"),
            row.get("duration_ms"),
        ),
    )
    conn.commit()


def ensure_outcomes(conn, now=None, lookback_days=None):
    """Buat baris outcome yang belum ada untuk alert dalam periode lookback."""
    now = int(time.time()) if now is None else int(now)
    days = config.TRACK_LOOKBACK_DAYS if lookback_days is None else lookback_days
    cutoff = now - days * 86400
    inserted = 0
    alerts = conn.execute(
        "SELECT id, created_at FROM alerts WHERE created_at >= ?", (cutoff,)
    ).fetchall()
    for alert in alerts:
        for horizon in config.OUTCOME_HORIZONS:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO outcomes(alert_id, horizon_sec, due_at, attempts)
                VALUES(?,?,?,0)
                """,
                (alert["id"], horizon, int(alert["created_at"]) + horizon),
            )
            inserted += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    return inserted


def due_outcomes(conn, now=None, limit=None):
    """Outcome yang sudah jatuh tempo dan belum berhasil diukur."""
    now = int(time.time()) if now is None else int(now)
    limit = config.TRACK_MAX_ALERTS if limit is None else limit
    return conn.execute(
        """
        SELECT o.alert_id, o.horizon_sec, o.due_at, o.attempts,
               a.pool, a.mint, a.symbol, a.created_at, a.price_usd
        FROM outcomes o
        JOIN alerts a ON a.id = o.alert_id
        WHERE o.checked_at IS NULL
          AND o.due_at <= ?
          AND o.attempts < ?
        ORDER BY o.due_at ASC
        LIMIT ?
        """,
        (now, config.TRACK_MAX_ATTEMPTS, limit),
    ).fetchall()


def save_outcome(conn, alert_id, horizon, entry_price, price, mfe, mae, candles):
    conn.execute(
        """
        UPDATE outcomes
        SET checked_at = ?, entry_price = ?, price = ?, mfe = ?, mae = ?,
            candles = ?, status = 'ok', last_error = NULL
        WHERE alert_id = ? AND horizon_sec = ?
        """,
        (
            int(time.time()), entry_price, price, mfe, mae, candles,
            alert_id, horizon,
        ),
    )
    conn.commit()


def fail_outcome(conn, alert_id, horizon, error, final=False):
    """Catat kegagalan. Set checked_at hanya kalau sudah menyerah."""
    conn.execute(
        """
        UPDATE outcomes
        SET attempts = attempts + 1,
            checked_at = CASE WHEN ? THEN ? ELSE checked_at END,
            status = CASE WHEN ? THEN 'no_data' ELSE 'retry' END,
            last_error = ?
        WHERE alert_id = ? AND horizon_sec = ?
        """,
        (
            1 if final else 0, int(time.time()),
            1 if final else 0, str(error)[:300],
            alert_id, horizon,
        ),
    )
    conn.commit()


def outcome_status(conn):
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM outcomes GROUP BY status"
    ).fetchall()
    return {row["status"] or "pending": row["n"] for row in rows}


def prune(conn, keep_days=None):
    keep_days = config.LOG_KEEP_DAYS if keep_days is None else keep_days
    cutoff = int(time.time()) - keep_days * 86400
    conn.execute("DELETE FROM evaluations WHERE last_seen < ?", (cutoff,))
    conn.execute("DELETE FROM security_cache WHERE fetched_at < ?", (cutoff,))
    conn.execute("DELETE FROM scans WHERE ts < ?", (cutoff,))
    conn.commit()


def stats(conn):
    today = int(time.time()) - 86400
    return {
        "evaluations": conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0],
        "alerts": conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
        "alerts_24h": conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE created_at >= ?", (today,)
        ).fetchone()[0],
        "scans_24h": conn.execute(
            "SELECT COUNT(*) FROM scans WHERE ts >= ?", (today,)
        ).fetchone()[0],
        "pending_outcomes": conn.execute(
            "SELECT COUNT(*) FROM outcomes WHERE checked_at IS NULL"
        ).fetchone()[0],
    }
