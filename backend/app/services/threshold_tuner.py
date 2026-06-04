"""Nightly threshold tuner.

`signal_history.calibrate_signal_thresholds` exists but is a manual-only MCP
tool. This module runs the same grid-search nightly, persists the best
(buy_thr, sell_below_thr) pair to `.cache/signal_thresholds.json`, and the
recommendation engine reads from there.

Safety:
  * Skip the update entirely when n_scored < 50 (too noisy).
  * Skip when the best expectancy is no better than current by ≥0.5pp.
  * Cap each tick's movement to ±0.5 thresholds — no big jumps.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.threshold_tuner")

_OUT_FILE = Path(__file__).resolve().parents[2] / ".cache" / "signal_thresholds.json"

DEFAULT_BUY_THR = 7.5
DEFAULT_SELL_BELOW = 3.5
MIN_SAMPLES = 50
MIN_IMPROVEMENT_PCT = 0.5
MAX_STEP = 0.5


def _load_current() -> tuple[float, float]:
    try:
        if _OUT_FILE.is_file():
            payload = json.loads(_OUT_FILE.read_text(encoding="utf-8"))
            return (
                float(payload.get("buy_thr", DEFAULT_BUY_THR)),
                float(payload.get("sell_below_thr", DEFAULT_SELL_BELOW)),
            )
    except Exception:
        _LOG.debug("suppressed exception", exc_info=True)
    return DEFAULT_BUY_THR, DEFAULT_SELL_BELOW


def _persist(buy: float, sell: float, payload_extra: dict | None = None) -> None:
    _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "buy_thr": round(buy, 2),
        "sell_below_thr": round(sell, 2),
    }
    if payload_extra:
        payload.update(payload_extra)
    _OUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_active_thresholds() -> dict[str, float]:
    """Read by `recommendations._load_thresholds`. Always returns floats."""
    buy, sell = _load_current()
    return {"buy_thr": buy, "sell_below_thr": sell}


def recalibrate(*, horizon: int = 21, min_count: int = 10) -> dict[str, Any]:
    try:
        from app.services import signal_history
    except Exception as e:
        return {"status": "error", "error": str(e)}

    try:
        report = signal_history.calibrate_signal_thresholds(horizon=horizon, min_count=min_count)
    except Exception as e:
        return {"status": "error", "error": f"calibrate failed: {e}"}

    n_scored = int((report or {}).get("n_scored") or 0)
    if n_scored < MIN_SAMPLES:
        return {
            "status": "skip",
            "reason": f"n_scored={n_scored} < {MIN_SAMPLES}",
            "report": report,
        }

    best = (report or {}).get("best") or {}
    current_exp = float((report.get("current") or {}).get("expectancy") or 0.0)
    best_exp = float(best.get("expectancy") or 0.0)

    if (best_exp - current_exp) < (MIN_IMPROVEMENT_PCT / 100.0):
        return {
            "status": "noop",
            "reason": f"best improvement {best_exp - current_exp:.4f} below floor",
            "current_expectancy": current_exp,
            "best_expectancy": best_exp,
        }

    buy_now, sell_now = _load_current()
    buy_target = float(best.get("buy_thr") or buy_now)
    sell_target = float(best.get("sell_below_thr") or sell_now)

    # Cap per-night movement so a single noisy run doesn't yank thresholds.
    buy_new = max(buy_now - MAX_STEP, min(buy_now + MAX_STEP, buy_target))
    sell_new = max(sell_now - MAX_STEP, min(sell_now + MAX_STEP, sell_target))

    _persist(
        buy_new,
        sell_new,
        {
            "n_scored": n_scored,
            "from": {"buy_thr": buy_now, "sell_below_thr": sell_now},
            "target": {"buy_thr": buy_target, "sell_below_thr": sell_target},
            "expectancy_gain_pct": round((best_exp - current_exp) * 100, 3),
        },
    )

    _LOG.info(
        "threshold_tuner: %.2f→%.2f buy / %.2f→%.2f sell (n=%d, gain=%.2f%%)",
        buy_now,
        buy_new,
        sell_now,
        sell_new,
        n_scored,
        (best_exp - current_exp) * 100,
    )
    return {
        "status": "ok",
        "buy_thr": buy_new,
        "sell_below_thr": sell_new,
        "n_scored": n_scored,
    }
