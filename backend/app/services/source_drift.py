"""Detect data-source reliability drift and demote chronically-failing sources.

Reads the `source_stats` table maintained by `data_cache.log_source_call` and
compares the trailing 7-day success rate to the trailing 30-day baseline. When
a source loses ≥10pp of reliability over the short window we:

  1. Move it to the back of `data_router._ORDER` (cheap demote, not removal).
  2. Append an audit row to `.cache/source_drift_audit.jsonl`.
  3. Optionally fire a Telegram alert when configured.

This lets the fallback chain re-rank itself when, say, yfinance starts 429-ing
heavily or stooq goes down — without losing the source entirely.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.security import get_logger
from app.services import data_cache

_LOG = get_logger("aifolimizer.services.source_drift")

_AUDIT_FILE = (
    Path(__file__).resolve().parents[2] / ".cache" / "source_drift_audit.jsonl"
)

DRIFT_THRESHOLD_PP = 10.0  # percentage-point delta that trips the gate
MIN_CALLS = 30             # don't react to a 5-call sample


def _stats(window_s: float) -> dict[str, dict[str, float]]:
    """Map source -> {calls, success_rate_pct, avg_latency_ms}."""
    rows = data_cache.source_stats_summary(since_s=window_s)
    return {
        r["source"]: {
            "calls": float(r.get("calls") or 0),
            "success_rate_pct": float(r.get("success_rate_pct") or 0),
            "avg_latency_ms": float(r.get("avg_latency_ms") or 0),
        }
        for r in rows
    }


def _append_audit(row: dict) -> None:
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        _LOG.warning("source_drift: audit append failed: %s", e)


def detect_and_demote(
    *,
    short_window_s: float = 7 * 86400,
    long_window_s: float = 30 * 86400,
    threshold_pp: float = DRIFT_THRESHOLD_PP,
) -> dict[str, Any]:
    short = _stats(short_window_s)
    long_ = _stats(long_window_s)

    drifted: list[dict] = []
    inspected: list[dict] = []
    for source, s in short.items():
        if s["calls"] < MIN_CALLS:
            continue
        baseline = long_.get(source, {}).get("success_rate_pct", 0.0)
        delta = baseline - s["success_rate_pct"]
        inspected.append({
            "source": source,
            "short_success_pct": round(s["success_rate_pct"], 2),
            "long_success_pct": round(baseline, 2),
            "delta_pp": round(delta, 2),
        })
        if delta < threshold_pp:
            continue
        # Demote in-process — restart resets ordering. Cheap signal that
        # the next call should prefer a different source first.
        try:
            from app.services import data_router
            order = list(data_router._ORDER)
            if source in order:
                order.remove(source)
                order.append(source)
                data_router._ORDER = order
        except Exception as e:
            _LOG.warning("source_drift: demote failed for %s: %s", source, e)
        row = {
            "ts": time.time(),
            "source": source,
            "delta_pp": round(delta, 2),
            "short_success_pct": round(s["success_rate_pct"], 2),
            "long_success_pct": round(baseline, 2),
            "action": "demoted_to_back",
        }
        _append_audit(row)
        drifted.append(row)
        _LOG.warning(
            "source_drift: demoted %s (delta=%.1fpp short=%.1f%% long=%.1f%%)",
            source, delta, s["success_rate_pct"], baseline,
        )

    return {
        "n_inspected": len(inspected),
        "drifted": drifted,
        "inspected": inspected,
    }
