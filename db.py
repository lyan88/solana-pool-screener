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

CREATE TABLE IF NOT EXISTS alert_queue (
    mint TEXT PRIMARY KEY,
    pool TEXT,
    name TEXT,
    symbol TEXT,
    age_sec INTEGER,
    liq_usd REAL,
    mc_usd REAL,
    buyers2 INTEGER,
    top10_pct REAL,
    lp_status TEXT,
    price_usd REAL,
    message TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    sent_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_queue_due
    ON alert_queue(status, expires_at, score, created_at);

CREATE TABLE IF NOT EXISTS watchlist (
    mint TEXT PRIMARY KEY,
    pool TEXT NOT NULL,
    name TEXT,
    symbol TEXT,
    dex TEXT,
    first_seen INTEGER,
    last_seen INTEGER,
    next_check INTEGER,
    expires_at INTEGER,
    checks INTEGER NOT NULL DEFAULT 0,
    best_score REAL NOT NULL DEFAULT 0,
    reasons TEXT,
    status TEXT NOT NULL DEFAULT 'watching'
);
CREATE INDEX IF NOT EXISTS idx_watch_due ON watchlist(status, next_check);
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


def has_pending_alert(conn, mint, now=None):
    """True kalau mint masih menunggu di antrean dan belum kedaluwarsa."""
    now = int(time.time()) if now is None else int(now)
    row = conn.execute(
        "SELECT 1 FROM alert_queue "
        "WHERE mint = ? AND status = 'pending' AND expires_at > ?",
        (mint, now),
    ).fetchone()
    return row is not None


def queue_alert(conn, row, score, message, ttl=None):
    """Simpan alert untuk dikirim. Tidak menimpa alert yang sudah terkirim."""
    now = int(time.time())
    ttl = config.ALERT_QUEUE_TTL_SEC if ttl is None else ttl
    expires_at = now + max(60, int(ttl))
    cur = conn.execute(
        """
        INSERT INTO alert_queue(
            mint, pool, name, symbol, age_sec, liq_usd, mc_usd, buyers2,
            top10_pct, lp_status, price_usd, message, score, created_at,
            expires_at, status
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')
        ON CONFLICT(mint) DO UPDATE SET
            pool=excluded.pool, name=excluded.name, symbol=excluded.symbol,
            age_sec=excluded.age_sec, liq_usd=excluded.liq_usd,
            mc_usd=excluded.mc_usd, buyers2=excluded.buyers2,
            top10_pct=excluded.top10_pct, lp_status=excluded.lp_status,
            price_usd=excluded.price_usd, message=excluded.message,
            score=excluded.score, expires_at=excluded.expires_at
        WHERE alert_queue.status = 'pending'
        """,
        (
            row["mint"], row.get("pool"), row.get("name"), row.get("symbol"),
            row.get("age_sec"), row.get("liq_usd"), row.get("mc_usd"),
            row.get("buyers2"), row.get("top10_pct"), row.get("lp_status"),
            row.get("price_usd"), message, float(score or 0), now, expires_at,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def pending_queue(conn, now=None, limit=None):
    """Antrean yang siap dikirim, skor tertinggi lebih dulu."""
    now = int(time.time()) if now is None else int(now)
    limit = config.ALERT_DRAIN_MAX if limit is None else int(limit)
    return conn.execute(
        """
        SELECT * FROM alert_queue
        WHERE status = 'pending' AND expires_at > ?
        ORDER BY score DESC, created_at ASC
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()


def mark_queue_sent(conn, mint):
    conn.execute(
        "UPDATE alert_queue SET status='sent', sent_at=?, attempts=attempts+1, "
        "last_error=NULL WHERE mint=?",
        (int(time.time()), mint),
    )
    conn.commit()


def mark_queue_blocked(conn, mint, error):
    """Kuota/jeda: biarkan pending tanpa menambah percobaan."""
    conn.execute(
        "UPDATE alert_queue SET last_error=? WHERE mint=? AND status='pending'",
        (str(error)[:300], mint),
    )
    conn.commit()


def mark_queue_failed(conn, mint, error, final=False):
    conn.execute(
        """
        UPDATE alert_queue
        SET attempts = attempts + 1,
            last_error = ?,
            status = CASE WHEN ? THEN 'failed' ELSE 'pending' END
        WHERE mint = ? AND status = 'pending'
        """,
        (str(error)[:300], 1 if final else 0, mint),
    )
    conn.commit()


def expire_queue(conn, now=None):
    now = int(time.time()) if now is None else int(now)
    cur = conn.execute(
        "UPDATE alert_queue SET status='expired', last_error='kedaluwarsa' "
        "WHERE status='pending' AND expires_at <= ?",
        (now,),
    )
    conn.commit()
    return cur.rowcount


def queue_stats(conn, now=None):
    now = int(time.time()) if now is None else int(now)
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM alert_queue GROUP BY status"
    ).fetchall()
    out = {row["status"]: row["n"] for row in rows}
    out["pending_live"] = conn.execute(
        "SELECT COUNT(*) FROM alert_queue WHERE status='pending' AND expires_at > ?",
        (now,),
    ).fetchone()[0]
    return out


def watch_upsert(conn, pool, reasons, score, ttl, interval=None, now=None):
    """Masukkan/segarkan kandidat near-miss ke watchlist."""
    now = int(time.time()) if now is None else int(now)
    interval = config.WATCH_INTERVAL_SEC if interval is None else int(interval)
    expires_at = now + max(60, int(ttl))
    conn.execute(
        """
        INSERT INTO watchlist(
            mint, pool, name, symbol, dex, first_seen, last_seen,
            next_check, expires_at, checks, best_score, reasons, status
        ) VALUES(?,?,?,?,?,?,?,?,?,0,?,?,'watching')
        ON CONFLICT(mint) DO UPDATE SET
            pool=excluded.pool, name=excluded.name, symbol=excluded.symbol,
            dex=excluded.dex, last_seen=excluded.last_seen,
            next_check=excluded.next_check,
            expires_at=MAX(watchlist.expires_at, excluded.expires_at),
            best_score=MAX(watchlist.best_score, excluded.best_score),
            reasons=excluded.reasons,
            status=CASE WHEN watchlist.status IN ('alerted','queued')
                        THEN watchlist.status ELSE 'watching' END
        """,
        (
            pool["mint"], pool.get("pool"), pool.get("name"), pool.get("symbol"),
            pool.get("dex"), now, now, now + interval, expires_at,
            float(score or 0), json.dumps(list(reasons or [])),
        ),
    )
    conn.commit()


def watch_due(conn, now=None, limit=None):
    now = int(time.time()) if now is None else int(now)
    limit = config.WATCH_MAX_PER_ROUND if limit is None else int(limit)
    return conn.execute(
        """
        SELECT * FROM watchlist
        WHERE status='watching' AND next_check <= ? AND expires_at > ?
        ORDER BY best_score DESC, next_check ASC
        LIMIT ?
        """,
        (now, now, limit),
    ).fetchall()


def watch_touch(conn, mint, reasons, score, next_check=None):
    now = int(time.time())
    next_check = (now + config.WATCH_INTERVAL_SEC) if next_check is None else int(next_check)
    if isinstance(reasons, str):
        payload = reasons
    else:
        payload = json.dumps(list(reasons or []))
    conn.execute(
        """
        UPDATE watchlist
        SET last_seen=?, next_check=?, checks=checks+1,
            best_score=MAX(best_score, ?), reasons=?
        WHERE mint=? AND status='watching'
        """,
        (now, next_check, float(score or 0), payload, mint),
    )
    conn.commit()


def watch_finish(conn, mint, status):
    conn.execute(
        "UPDATE watchlist SET status=?, last_seen=? WHERE mint=?",
        (status, int(time.time()), mint),
    )
    conn.commit()


def watch_expire(conn, now=None):
    now = int(time.time()) if now is None else int(now)
    cur = conn.execute(
        "UPDATE watchlist SET status='expired' "
        "WHERE status='watching' AND expires_at <= ?",
        (now,),
    )
    conn.commit()
    return cur.rowcount


def watch_stats(conn):
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM watchlist GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


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
                int(row.get("created_at") or time.time()), row.get("age_sec"),
                row.get("liq_usd"), row.get("mc_usd"), row.get("buyers2"),
                row.get("top10_pct"), row.get("lp_status"), row.get("price_usd"),
                row.get("message"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def mark_evaluation_alerted(conn, mint, ts=None):
    conn.execute(
        "UPDATE evaluations SET alerted_at=? "
        "WHERE mint=? AND alerted_at IS NULL",
        (int(ts or time.time()), mint),
    )
    conn.commit()


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
    conn.execute(
        "DELETE FROM alert_queue WHERE status != 'pending' AND created_at < ?",
        (cutoff,),
    )
    conn.execute(
        "DELETE FROM watchlist WHERE status != 'watching' AND first_seen < ?",
        (cutoff,),
    )
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
        "queue_pending": conn.execute(
            "SELECT COUNT(*) FROM alert_queue WHERE status='pending' AND expires_at > ?",
            (int(time.time()),),
        ).fetchone()[0],
        "watch_watching": conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE status='watching'"
        ).fetchone()[0],
    }
