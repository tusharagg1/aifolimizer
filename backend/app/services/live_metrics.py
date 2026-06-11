"""Live KPIs (Phase 10).

Replaces accuracy-only paper-trade reporting with the EV + risk-adjusted
KPI set per the goal restated in plan v4:

  expectancy_per_trade_pct  - direct EV proxy
  profit_factor             - Σwins / Σlosses (PF)
  sharpe_30d                - daily-return based Sharpe ratio
  sortino_30d               - downside-only Sharpe
  max_drawdown_pct          - equity-curve peak-to-trough
  hit_rate                  - sanity, not goal
  avg_win_pct, avg_loss_pct
  n_trades
  after_cost_drag_bps       - implicit tx cost net
  regime_breakdown          - {composite: {pf, expectancy}}

Reads from `recommendations` table (status != 'open') for closed-trade
metrics + `portfolio_equity` for drawdown. Regime-breakdown joins each
closed rec back to the regime that was active at its entry timestamp
(best-effort by matching the nearest regime_history row).

Persists snapshots into `live_kpi_snapshots` hypertable so trend over
time is queryable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_TX_COST_BPS = 5.0  # mirror skill_backtest default


# ── pure helpers ───────────────────────────────────────────────────────────


@dataclass
class KPIs:
    expectancy_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    hit_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    n_trades: int = 0
    after_cost_drag_bps: float = 0.0
    regime_breakdown: dict[str, dict[str, float]] = field(
        default_factory=dict,
    )
    window_days: int = 30
    ts: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        out = self.__dict__.copy()
        out["ts"] = self.ts.isoformat()
        return out


def _profit_factor(returns: list[float]) -> float:
    wins = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return round(wins / losses, 2)


def _sharpe(returns: list[float]) -> float:
    """Sample-std Sharpe. Annualized assuming daily returns; if returns
    span varies, this is a rough indicator only."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    # Annualize ~252 trading days
    return round((mean / std) * math.sqrt(252), 2)


def _sortino(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downs = [(r - mean) ** 2 for r in returns if r < mean]
    if not downs:
        return 0.0
    dd_std = math.sqrt(sum(downs) / len(downs))
    if dd_std == 0:
        return 0.0
    return round((mean / dd_std) * math.sqrt(252), 2)


def _max_drawdown(equity_curve: list[float]) -> float:
    """Peak-to-trough drawdown in percent (negative)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return round(max_dd * 100, 2)


def compute_from_closed_recs(
    recs: list[dict],
    equity_curve: list[float] | None = None,
    *,
    window_days: int = 30,
) -> KPIs:
    """Pure: compute KPIs from list of closed recommendations."""
    closed = [r for r in recs if r.get("return_pct") is not None and r.get("status") != "open"]
    if not closed:
        return KPIs(window_days=window_days)

    returns = [float(r["return_pct"]) for r in closed]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    expectancy = sum(returns) / len(returns)
    hit = len(wins) / len(returns)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    # Regime breakdown
    regime_map: dict[str, list[float]] = {}
    for r in closed:
        regime = r.get("regime_composite") or "unknown"
        regime_map.setdefault(regime, []).append(float(r["return_pct"]))
    regime_breakdown = {
        composite: {
            "pf": _profit_factor(rets),
            "expectancy_pct": round(sum(rets) / len(rets), 3),
            "n": len(rets),
        }
        for composite, rets in regime_map.items()
        if len(rets) >= 3  # noisy below 3
    }

    return KPIs(
        expectancy_pct=round(expectancy, 3),
        profit_factor=_profit_factor(returns),
        sharpe=_sharpe(returns),
        sortino=_sortino(returns),
        max_drawdown_pct=_max_drawdown(equity_curve or []),
        hit_rate=round(hit, 3),
        avg_win_pct=round(avg_win, 3),
        avg_loss_pct=round(avg_loss, 3),
        n_trades=len(closed),
        after_cost_drag_bps=_TX_COST_BPS * 2,  # entry + exit
        regime_breakdown=regime_breakdown,
        window_days=window_days,
    )


# ── DB-backed orchestration ────────────────────────────────────────────────


async def kpis(
    tenant_hash: str,
    window_days: int = 30,
) -> dict[str, Any]:
    """Fetch closed recs + equity curve over window → compute → persist."""
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return KPIs(window_days=window_days).to_dict()

        async with pool.acquire() as conn:
            # Closed recs with regime annotation (best-effort).
            rec_rows = await conn.fetch(
                """
                SELECT r.return_pct, r.status, r.exit_date, r.ts,
                       (
                         SELECT composite FROM regime_history
                         WHERE ts <= r.ts
                         ORDER BY ts DESC LIMIT 1
                       ) AS regime_composite
                FROM recommendations r
                WHERE r.tenant_hash = $1
                  AND r.status <> 'open'
                  AND r.exit_date > current_date
                       - ($2::TEXT || ' days')::INTERVAL
                """,
                tenant_hash,
                str(window_days),
            )
            equity_rows = await conn.fetch(
                """
                SELECT total_value_cad FROM portfolio_equity
                WHERE tenant_hash = $1
                  AND date > current_date - ($2::TEXT || ' days')::INTERVAL
                ORDER BY date ASC
                """,
                tenant_hash,
                str(window_days),
            )

        recs = [dict(r) for r in rec_rows]
        equity_curve = [float(r["total_value_cad"]) for r in equity_rows]
        kpi = compute_from_closed_recs(
            recs,
            equity_curve=equity_curve,
            window_days=window_days,
        )
        await _persist(tenant_hash, kpi)
        return kpi.to_dict()
    except Exception as e:
        log.warning("live_metrics.kpis failed: %s", e)
        return KPIs(window_days=window_days).to_dict()


async def _persist(tenant_hash: str, kpi: KPIs) -> None:
    try:
        from app.db.pool import get_pool
        import json

        pool = get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO live_kpi_snapshots (
                  ts, tenant_hash, window_days,
                  expectancy_pct, profit_factor, sharpe, sortino,
                  max_drawdown_pct, hit_rate, avg_win_pct, avg_loss_pct,
                  n_trades, after_cost_drag_bps, regime_breakdown
                ) VALUES (
                  $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                  $14::jsonb
                )
                ON CONFLICT (tenant_hash, window_days, ts) DO NOTHING
                """,
                kpi.ts,
                tenant_hash,
                kpi.window_days,
                kpi.expectancy_pct,
                kpi.profit_factor,
                kpi.sharpe,
                kpi.sortino,
                kpi.max_drawdown_pct,
                kpi.hit_rate,
                kpi.avg_win_pct,
                kpi.avg_loss_pct,
                kpi.n_trades,
                kpi.after_cost_drag_bps,
                json.dumps(kpi.regime_breakdown),
            )
    except Exception as e:
        log.warning("live_metrics persist failed: %s", e)


async def latest(
    tenant_hash: str,
    window_days: int = 30,
) -> dict[str, Any] | None:
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM live_kpi_snapshots
                WHERE tenant_hash = $1 AND window_days = $2
                ORDER BY ts DESC LIMIT 1
                """,
                tenant_hash,
                window_days,
            )
        if not row:
            return None
        d = dict(row)
        d["ts"] = d["ts"].isoformat() if d.get("ts") else None
        # Coerce numerics for JSON
        for k in (
            "expectancy_pct",
            "profit_factor",
            "sharpe",
            "sortino",
            "max_drawdown_pct",
            "hit_rate",
            "avg_win_pct",
            "avg_loss_pct",
            "after_cost_drag_bps",
        ):
            if d.get(k) is not None:
                d[k] = float(d[k])
        return d
    except Exception as e:
        log.warning("live_metrics.latest failed: %s", e)
        return None
