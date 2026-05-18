"""SQLite-backed disk cache for quotes, history, fundamentals.

Why disk: in-memory dict caches die on backend restart. EOD bars
never change after close, so re-fetching wastes free-tier quota.

Storage: backend/.cache/data.sqlite (gitignored).

Schema:
  quotes(symbol, source, payload_json, as_of) PK (symbol, source)
  history(symbol, source, period, interval, payload_json, as_of)
    PK (symbol, source, period, interval)
  fundamentals(symbol, source, payload_json, as_of) PK (symbol, source)
  source_stats(source, ts, ok, latency_ms, error) — track-record evidence

TTLs are caller-decided. We just store + return. Router enforces freshness.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
_DB_PATH = _CACHE_DIR / "data.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    as_of REAL NOT NULL,
    PRIMARY KEY (symbol, source)
);
CREATE TABLE IF NOT EXISTS history (
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    period TEXT NOT NULL,
    interval TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    as_of REAL NOT NULL,
    PRIMARY KEY (symbol, source, period, interval)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    as_of REAL NOT NULL,
    PRIMARY KEY (symbol, source)
);
CREATE TABLE IF NOT EXISTS source_stats (
    source TEXT NOT NULL,
    ts REAL NOT NULL,
    ok INTEGER NOT NULL,
    latency_ms REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_stats_source_ts
    ON source_stats(source, ts);
"""

_conn: sqlite3.Connection | None = None


def _conn_get() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=5.0)
    c.executescript(_SCHEMA)
    c.commit()
    _conn = c
    return c


def _row_to_dict(row, payload_col: int = 0, asof_col: int = 1) -> dict | None:
    if row is None:
        return None
    payload = json.loads(row[payload_col])
    payload["_as_of"] = float(row[asof_col])
    return payload


def get_quote(symbol: str, source: str, max_age_s: float) -> dict | None:
    with _LOCK:
        c = _conn_get()
        row = c.execute(
            "SELECT payload_json, as_of FROM quotes WHERE symbol=? AND source=?",
            (symbol, source),
        ).fetchone()
    if not row:
        return None
    if (time.time() - float(row[1])) > max_age_s:
        return None
    return _row_to_dict(row)


def put_quote(symbol: str, source: str, payload: dict) -> None:
    with _LOCK:
        c = _conn_get()
        c.execute(
            "INSERT OR REPLACE INTO quotes(symbol, source, payload_json, as_of) "
            "VALUES (?, ?, ?, ?)",
            (symbol, source, json.dumps(payload), time.time()),
        )
        c.commit()


def get_history(
    symbol: str, source: str, period: str, interval: str, max_age_s: float
) -> list[dict] | None:
    with _LOCK:
        c = _conn_get()
        row = c.execute(
            "SELECT payload_json, as_of FROM history "
            "WHERE symbol=? AND source=? AND period=? AND interval=?",
            (symbol, source, period, interval),
        ).fetchone()
    if not row:
        return None
    if (time.time() - float(row[1])) > max_age_s:
        return None
    return json.loads(row[0])


def put_history(
    symbol: str, source: str, period: str, interval: str, bars: list[dict]
) -> None:
    with _LOCK:
        c = _conn_get()
        c.execute(
            "INSERT OR REPLACE INTO history "
            "(symbol, source, period, interval, payload_json, as_of) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, source, period, interval, json.dumps(bars), time.time()),
        )
        c.commit()


def get_fundamentals(symbol: str, source: str, max_age_s: float) -> dict | None:
    with _LOCK:
        c = _conn_get()
        row = c.execute(
            "SELECT payload_json, as_of FROM fundamentals WHERE symbol=? AND source=?",
            (symbol, source),
        ).fetchone()
    if not row:
        return None
    if (time.time() - float(row[1])) > max_age_s:
        return None
    return _row_to_dict(row)


def put_fundamentals(symbol: str, source: str, payload: dict) -> None:
    with _LOCK:
        c = _conn_get()
        c.execute(
            "INSERT OR REPLACE INTO fundamentals(symbol, source, payload_json, as_of) "
            "VALUES (?, ?, ?, ?)",
            (symbol, source, json.dumps(payload), time.time()),
        )
        c.commit()


def log_source_call(
    source: str, ok: bool, latency_ms: float | None, error: str | None = None
) -> None:
    """Append a call outcome row — used by the public track-record report."""
    with _LOCK:
        c = _conn_get()
        c.execute(
            "INSERT INTO source_stats(source, ts, ok, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, time.time(), 1 if ok else 0, latency_ms, error),
        )
        c.commit()


def source_stats_summary(since_s: float = 86400 * 7) -> list[dict]:
    """Aggregate source reliability for the last `since_s` seconds."""
    cutoff = time.time() - since_s
    with _LOCK:
        c = _conn_get()
        rows = c.execute(
            "SELECT source, "
            "       SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS ok_count, "
            "       SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS fail_count, "
            "       AVG(latency_ms) AS avg_latency_ms "
            "FROM source_stats WHERE ts >= ? GROUP BY source",
            (cutoff,),
        ).fetchall()
    out: list[dict] = []
    for source, ok_count, fail_count, avg_latency in rows:
        total = (ok_count or 0) + (fail_count or 0)
        out.append({
            "source": source,
            "calls": total,
            "ok": int(ok_count or 0),
            "fail": int(fail_count or 0),
            "success_rate_pct": round(100 * (ok_count or 0) / total, 2) if total else None,
            "avg_latency_ms": round(float(avg_latency), 1) if avg_latency is not None else None,
        })
    return out


def clear_all() -> None:
    """Test helper — wipe cache. Production calls should never use this."""
    with _LOCK:
        c = _conn_get()
        c.executescript(
            "DELETE FROM quotes; DELETE FROM history; "
            "DELETE FROM fundamentals; DELETE FROM source_stats;"
        )
        c.commit()
