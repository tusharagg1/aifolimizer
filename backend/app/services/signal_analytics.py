"""PG-backed signal analytics.

Reads realized outcomes from the Postgres signal_history table (the single
source of truth once the JSONL scorer was ported) and reuses the exact pure
math in signal_history by feeding it rows in the legacy JSONL shape. Only the
data source changes — accuracy / decay / attribution / calibration logic is
identical.

Each function returns the same dict shape as its signal_history counterpart.
Callers should fall back to the JSONL functions when get_pool() is None.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db.repositories import signals_repo
from app.services import signal_history as _sh


def _f(v: Any) -> Optional[float]:
    return float(v) if v is not None else None


def _to_legacy(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a flat PG signal_history row into the nested JSONL shape the
    signal_history pure helpers expect."""
    outcomes: dict[str, dict] = {}
    for h in signals_repo._HORIZONS:
        val = row.get(f"realized_return_{h}d")
        if val is not None:
            ret = float(val)
            outcomes[f"h{h}"] = {"ret_pct": ret, "win": ret > 0}
    return {
        "action": row.get("action"),
        "score": _f(row.get("score")),
        "confidence": row.get("conviction"),
        "entry_price": _f(row.get("entry_price")),
        "features": {
            "tech_score": _f(row.get("tech_score")),
            "fund_score": _f(row.get("fund_score")),
            "macro_score": _f(row.get("macro_score")),
            "sentiment": _f(row.get("sentiment_score")),
            "rsi": _f(row.get("rsi")),
            "stage": row.get("stage"),
            "market_regime": row.get("market_regime"),
        },
        "outcomes": outcomes,
    }


async def _legacy_rows(lookback_days: int = 365) -> list[dict[str, Any]]:
    rows = await signals_repo.fetch_scored(lookback_days=lookback_days)
    return [_to_legacy(r) for r in rows]


async def accuracy_report(horizon: int = 21, *, min_count: int = 5) -> dict:
    return _sh.accuracy_report(horizon, min_count=min_count, rows=await _legacy_rows())


async def signal_decay_curve(
    horizons: tuple[int, ...] = _sh._DEFAULT_HORIZONS,
    *,
    action_filter: str | None = None,
    min_count: int = 5,
) -> dict:
    return _sh.signal_decay_curve(
        horizons, action_filter=action_filter, min_count=min_count, rows=await _legacy_rows()
    )


async def per_signal_source_attribution(horizon: int = 21, *, min_count: int = 5) -> dict:
    return _sh.per_signal_source_attribution(horizon, min_count=min_count, rows=await _legacy_rows())


async def calibrate_confidence(horizon: int = 21, *, min_count_per_bucket: int = 5) -> dict:
    return _sh.calibrate_confidence(
        horizon, min_count_per_bucket=min_count_per_bucket, rows=await _legacy_rows()
    )


async def calibrate_thresholds(horizon: int = 21, *, min_count: int = 10) -> dict:
    return _sh.calibrate_thresholds(horizon, min_count=min_count, rows=await _legacy_rows())
