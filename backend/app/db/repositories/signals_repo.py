"""signal_history repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.db.pool import get_pool

_HORIZONS: tuple[int, ...] = (1, 3, 5, 10, 21, 42, 63)
_REALIZED_COLS: frozenset[str] = frozenset(f"realized_return_{h}d" for h in _HORIZONS)


async def insert_signal(
    tenant_hash: str,
    symbol: str,
    ts: datetime,
    action: str,
    score: float,
    conviction: Optional[str] = None,
    entry_price: Optional[float] = None,
    tech_score: Optional[float] = None,
    fund_score: Optional[float] = None,
    macro_score: Optional[float] = None,
    sentiment_score: Optional[float] = None,
    skill_consensus: Optional[int] = None,
    skill_confidence: Optional[float] = None,
    skill_evidence: Optional[dict] = None,
    features: Optional[dict] = None,
    weights_version: Optional[int] = None,
) -> None:
    pool = get_pool()
    if pool is None:
        return
    f = features or {}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO signal_history (
              tenant_hash, symbol, ts, action, conviction, score,
              tech_score, fund_score, macro_score, sentiment_score,
              skill_consensus, skill_confidence, skill_evidence,
              rsi, stage, market_regime, analyst_upside_pct, weight,
              signal_quality, risk_reward, kelly_pct, win_prob, earnings_risk,
              weights_version, entry_price
            ) VALUES (
              $1, $2, $3, $4, $5, $6,
              $7, $8, $9, $10,
              $11, $12, $13,
              $14, $15, $16, $17, $18,
              $19, $20, $21, $22, $23,
              $24, $25
            )
            ON CONFLICT (tenant_hash, symbol, ts) DO NOTHING
            """,
            tenant_hash,
            symbol,
            ts,
            action,
            conviction,
            score,
            tech_score,
            fund_score,
            macro_score,
            sentiment_score,
            skill_consensus,
            skill_confidence,
            json.dumps(skill_evidence) if skill_evidence is not None else None,
            f.get("rsi"),
            f.get("stage"),
            f.get("market_regime"),
            f.get("analyst_upside_pct"),
            f.get("weight"),
            f.get("signal_quality"),
            f.get("risk_reward"),
            f.get("kelly_pct"),
            f.get("win_prob"),
            f.get("earnings_risk"),
            weights_version,
            entry_price,
        )


async def latest_for_tenant(tenant_hash: str) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (symbol) *
            FROM signal_history
            WHERE tenant_hash = $1
            ORDER BY symbol, ts DESC
            """,
            tenant_hash,
        )
    return [dict(r) for r in rows]


async def history_for_symbol(tenant_hash: str, symbol: str, days: int = 30) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM signal_history
            WHERE tenant_hash = $1
              AND symbol = $2
              AND ts > now() - ($3::TEXT || ' days')::INTERVAL
            ORDER BY ts ASC
            """,
            tenant_hash,
            symbol,
            str(days),
        )
    return [dict(r) for r in rows]


async def backfill_realized_returns(horizon_days: int, *, batch_limit: int = 500) -> int:
    """Fills realized_return_{horizon}d on rows whose horizon window has closed.

    Returns number of rows updated.
    """
    pool = get_pool()
    if pool is None:
        return 0
    col = f"realized_return_{horizon_days}d"
    if col not in _REALIZED_COLS:
        raise ValueError(f"unsupported horizon {horizon_days}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT tenant_hash, symbol, ts
            FROM signal_history
            WHERE {col} IS NULL
              AND ts < now() - ($1::TEXT || ' days')::INTERVAL
            LIMIT $2
            """,
            str(horizon_days),
            batch_limit,
        )
    # Caller fills realized prices via market_data; this repo only returns candidates.
    return len(rows)


_DIRECTIONAL_SQL = "('BUY','ADD','SELL','TRIM')"


async def rows_needing_backfill(horizon_days: int, *, batch_limit: int = 500) -> list[dict[str, Any]]:
    """Directional rows whose H-day window has closed but realized_return is null.

    Returns the keys + entry_price + action needed to compute the realized
    return externally (bars via data_router) and write it back. entry_price may
    be NULL for legacy rows; the caller derives entry from the signal-date close.
    """
    pool = get_pool()
    if pool is None:
        return []
    col = f"realized_return_{horizon_days}d"
    if col not in _REALIZED_COLS:
        raise ValueError(f"unsupported horizon {horizon_days}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT tenant_hash, symbol, ts, entry_price, action
            FROM signal_history
            WHERE {col} IS NULL
              AND action IN {_DIRECTIONAL_SQL}
              AND ts < now() - ($1::TEXT || ' days')::INTERVAL
            ORDER BY ts ASC
            LIMIT $2
            """,
            str(horizon_days),
            batch_limit,
        )
    return [dict(r) for r in rows]


async def set_entry_price(tenant_hash: str, symbol: str, ts: datetime, entry_price: float) -> None:
    """Backfill a derived entry_price on a legacy row that lacked one."""
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE signal_history
            SET entry_price = $4
            WHERE tenant_hash = $1 AND symbol = $2 AND ts = $3
              AND entry_price IS NULL
            """,
            tenant_hash,
            symbol,
            ts,
            entry_price,
        )


async def set_realized_return(
    tenant_hash: str,
    symbol: str,
    ts: datetime,
    horizon_days: int,
    ret_pct: float,
) -> None:
    pool = get_pool()
    if pool is None:
        return
    col = f"realized_return_{horizon_days}d"
    if col not in _REALIZED_COLS:
        raise ValueError(f"unsupported horizon {horizon_days}")
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            UPDATE signal_history
            SET {col} = $4
            WHERE tenant_hash = $1 AND symbol = $2 AND ts = $3
            """,
            tenant_hash,
            symbol,
            ts,
            ret_pct,
        )


async def fetch_scored(
    *,
    lookback_days: int = 365,
    tenant_hash: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Directional rows for analytics (accuracy/decay/attribution/calibration).

    Returns action, score, conviction, sub-scores, and every realized_return_*d
    column. tenant_hash=None aggregates across tenants (single-user default,
    matches the legacy JSONL semantics which carried no tenant).
    """
    pool = get_pool()
    if pool is None:
        return []
    realized_cols = ", ".join(f"realized_return_{h}d" for h in _HORIZONS)
    where = [
        f"action IN {_DIRECTIONAL_SQL}",
        "ts > now() - ($1::TEXT || ' days')::INTERVAL",
    ]
    params: list[Any] = [str(lookback_days)]
    if tenant_hash is not None:
        params.append(tenant_hash)
        where.append(f"tenant_hash = ${len(params)}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT action, score, conviction, entry_price,
                   tech_score, fund_score, macro_score, sentiment_score,
                   rsi, stage, market_regime, {realized_cols}
            FROM signal_history
            WHERE {" AND ".join(where)}
            ORDER BY ts ASC
            """,
            *params,
        )
    return [dict(r) for r in rows]


async def attribution_by_source(horizon_days: int = 21, lookback_days: int = 90) -> dict[str, dict[str, float]]:
    """Per-sub-signal accuracy + EV (used by weights_tuner Phase 5/11).

    Returns {source: {win_rate, avg_return, n, after_cost_expectancy_pct, profit_factor}}.
    """
    pool = get_pool()
    if pool is None:
        return {}
    col = f"realized_return_{horizon_days}d"
    if col not in _REALIZED_COLS:
        raise ValueError(f"unsupported horizon {horizon_days}")
    result: dict[str, dict[str, float]] = {}
    async with pool.acquire() as conn:
        for src in ("tech_score", "fund_score", "macro_score", "sentiment_score", "skill_consensus"):
            row = await conn.fetchrow(
                f"""
                SELECT
                  AVG(CASE WHEN {col} > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                  AVG({col}) AS avg_return,
                  COUNT(*) AS n,
                  COALESCE(SUM(CASE WHEN {col} > 0 THEN {col} END), 0)
                    / NULLIF(COALESCE(-SUM(CASE WHEN {col} < 0 THEN {col} END), 0), 0)
                    AS profit_factor
                FROM signal_history
                WHERE {src} > 0
                  AND {col} IS NOT NULL
                  AND ts > now() - ($1::TEXT || ' days')::INTERVAL
                """,
                str(lookback_days),
            )
            if row and row["n"]:
                src_key = src.replace("_score", "").replace("skill_consensus", "skill")
                result[src_key] = {
                    "win_rate": float(row["win_rate"] or 0),
                    "avg_return": float(row["avg_return"] or 0),
                    "n": int(row["n"]),
                    "profit_factor": float(row["profit_factor"] or 0),
                    "after_cost_expectancy_pct": float(row["avg_return"] or 0) - 0.0010,
                }
    return result
