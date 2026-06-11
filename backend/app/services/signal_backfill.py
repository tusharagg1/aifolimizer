"""Fill PG signal_history.realized_return_{H}d columns.

Ports the realized-return compute that previously lived only in the JSONL
`signal_history.score_horizons` into a Postgres backfill, so PG becomes the
single source of truth for signal outcomes.

For each directional signal whose H-day window has closed and whose
realized_return_{H}d is still null:
  - fetch daily bars (data_router, cached),
  - find the close H trading days after the signal date,
  - compute return vs stored entry_price, sign-flipped for SELL/TRIM,
  - UPDATE the column.

get_history is synchronous (network); it is run in a thread so the async
scheduler loop is not blocked.
"""

from __future__ import annotations

import asyncio
import logging

from app.services import data_router
from app.services.signal_history import _close_at_offset
from app.db.repositories import signals_repo

_LOG = logging.getLogger(__name__)

_DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5, 10, 21, 42, 63)
_LONG_ACTIONS = frozenset({"BUY", "ADD"})


def compute_return(entry: float, exit_close: float, action: str) -> float | None:
    """Realized % return for a directional signal; sign-flipped for shorts.

    BUY/ADD: long-side return. SELL/TRIM: inverted (a correct sell is a fall).
    Returns None when entry is non-positive.
    """
    if entry is None or exit_close is None or entry <= 0:
        return None
    raw = (exit_close - entry) / entry * 100
    return raw if (action or "").upper() in _LONG_ACTIONS else -raw


async def _bars(symbol: str, cache: dict[str, list[dict]]) -> list[dict]:
    if symbol not in cache:
        try:
            bars = await asyncio.to_thread(
                data_router.get_history, symbol, period="1y", interval="1d"
            )
            cache[symbol] = bars or []
        except Exception:
            cache[symbol] = []
    return cache[symbol]


async def run(
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    *,
    batch_limit: int = 500,
) -> dict:
    """Backfill realized returns across horizons. Returns a per-horizon summary."""
    bars_cache: dict[str, list[dict]] = {}
    written = 0
    skipped_window = 0
    skipped_data = 0
    per_horizon: dict[str, int] = {}

    for h in horizons:
        candidates = await signals_repo.rows_needing_backfill(h, batch_limit=batch_limit)
        h_written = 0
        for row in candidates:
            symbol = row.get("symbol") or ""
            ts = row.get("ts")
            entry = row.get("entry_price")
            action = row.get("action") or ""
            if not symbol or ts is None:
                skipped_data += 1
                continue

            bars = await _bars(symbol, bars_cache)
            if not bars:
                skipped_data += 1
                continue

            # Legacy rows may lack entry_price: derive it from the close on the
            # signal date (offset 0) and persist so the column stops being NULL.
            if entry is None:
                derived = _close_at_offset(bars, ts.date(), 0)
                if derived is None:
                    skipped_data += 1
                    continue
                entry = derived
                await signals_repo.set_entry_price(row["tenant_hash"], symbol, ts, round(float(entry), 4))

            exit_close = _close_at_offset(bars, ts.date(), h)
            if exit_close is None:
                skipped_window += 1  # window not yet present in available bars
                continue

            ret_pct = compute_return(float(entry), exit_close, action)
            if ret_pct is None:
                skipped_data += 1
                continue

            await signals_repo.set_realized_return(
                row["tenant_hash"], symbol, ts, h, round(ret_pct, 3)
            )
            h_written += 1
            written += 1
        per_horizon[f"h{h}"] = h_written

    return {
        "written": written,
        "skipped_window": skipped_window,
        "skipped_data": skipped_data,
        "per_horizon": per_horizon,
    }
