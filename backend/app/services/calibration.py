"""Calibration metric (Phase 9).

Reads predicted-probability + realized-outcome pairs from signal_history
and computes:
  - Brier score (mean squared error between prob and binary outcome)
  - Reliability bins (predicted bin center vs actual win rate per bin)
  - Expected Calibration Error (ECE — magnitude of miscalibration)
  - Verdict: well_calibrated / overconfident / underconfident

A "well-calibrated" model says 70% and is right 70% of the time. If it
says 70% but is right 50% → overconfident. If it says 70% but is right
85% → underconfident.

This module persists nightly results to calibration_reports table.
Phase 10 surfaces them in the dashboard.

Phase 11 weights tuner may consult `verdict` to throttle weight bumps
when the model is overconfident.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


_BRIER_OK_THRESHOLD = 0.20  # below this → well_calibrated
_ECE_OVERCONFIDENT_THR = 0.15  # ECE above + actual<predicted = overconf


@dataclass
class Bin:
    bin_center: float  # 0.05, 0.15, ... 0.95
    predicted_avg: float  # mean predicted prob in this bin
    actual_rate: float  # share of wins in this bin
    count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CalibrationReport:
    horizon_days: int
    n_samples: int
    brier_score: float
    ece: float
    verdict: str
    bins: list[Bin]
    ts: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        d["bins"] = [b.to_dict() for b in self.bins]
        return d


def _bin_index(p: float, n_bins: int) -> int:
    """Map prob in [0,1] to bin index in [0, n_bins-1]."""
    if p < 0:
        p = 0.0
    if p >= 1:
        return n_bins - 1
    return int(p * n_bins)


def compute(
    pairs: list[tuple[float, int]],
    *,
    horizon_days: int = 21,
    n_bins: int = 10,
) -> CalibrationReport:
    """Pure: compute report from (predicted_prob, binary_outcome) pairs.

    Args:
      pairs: list of (predicted_prob in [0,1], outcome 0/1).
      horizon_days: stamped in report — informational.
      n_bins: number of reliability bins (default 10 = 10% slices).

    Returns:
      CalibrationReport.
    """
    n = len(pairs)
    if n == 0:
        return CalibrationReport(
            horizon_days=horizon_days,
            n_samples=0,
            brier_score=0.0,
            ece=0.0,
            verdict="no_data",
            bins=[],
            ts=datetime.now(tz=timezone.utc),
        )

    # Brier
    brier = sum((p - o) ** 2 for p, o in pairs) / n

    # Bin into n equal-width buckets
    bin_pairs: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in pairs:
        bin_pairs[_bin_index(p, n_bins)].append((p, o))

    bins: list[Bin] = []
    ece_sum = 0.0
    overconf_signal = 0
    underconf_signal = 0
    for i, group in enumerate(bin_pairs):
        if not group:
            continue
        bin_center = (i + 0.5) / n_bins
        predicted_avg = sum(p for p, _ in group) / len(group)
        actual_rate = sum(o for _, o in group) / len(group)
        bins.append(
            Bin(
                bin_center=round(bin_center, 3),
                predicted_avg=round(predicted_avg, 4),
                actual_rate=round(actual_rate, 4),
                count=len(group),
            )
        )
        gap = predicted_avg - actual_rate
        ece_sum += (len(group) / n) * abs(gap)
        if gap > 0.05:
            overconf_signal += len(group)
        elif gap < -0.05:
            underconf_signal += len(group)

    ece = round(ece_sum, 4)

    if ece <= 0.05:
        verdict = "well_calibrated"
    elif overconf_signal > underconf_signal and ece > _ECE_OVERCONFIDENT_THR:
        verdict = "overconfident"
    elif underconf_signal > overconf_signal and ece > _ECE_OVERCONFIDENT_THR:
        verdict = "underconfident"
    else:
        # Calibrated enough not to label, but not perfect.
        verdict = "well_calibrated" if brier < _BRIER_OK_THRESHOLD else "noisy"

    return CalibrationReport(
        horizon_days=horizon_days,
        n_samples=n,
        brier_score=round(brier, 4),
        ece=ece,
        verdict=verdict,
        bins=bins,
        ts=datetime.now(tz=timezone.utc),
    )


# ── Data fetch + persistence ────────────────────────────────────────────────


async def _fetch_pairs(horizon_days: int) -> list[tuple[float, int]]:
    """Read (predicted win_prob, realized binary outcome) from the signal_history JSONL.

    Source of truth is the same JSONL the horizon scorer writes
    (`signal_history.score_horizons`): each directional row carries
    `features.win_prob` (the model's predicted P(win) at decision time) and,
    once the H-day window has elapsed, `outcomes["h{H}"]["win"]` (already
    direction-corrected — SELL/TRIM returns are sign-flipped upstream).

    Pairs only exist at horizons whose window has closed for some signals;
    shorter horizons populate first. Returns [] (→ "no_data" verdict) when no
    overlapping (win_prob, outcome) rows exist yet — honest, not an error.

    NOTE: this intentionally does NOT read Postgres. The realized-return
    columns on the Postgres `signal_history` table are never populated by this
    codebase (the scorer writes JSONL), so the old Postgres path was always
    empty. The JSONL is the live source the rest of the pipeline uses.
    """
    try:
        from app.services import signal_history as sh

        key = f"h{horizon_days}"
        out: list[tuple[float, int]] = []
        for row in sh._load_history():
            if (row.get("action") or "").upper() not in sh._DIRECTIONAL_ACTIONS:
                continue
            wp = (row.get("features") or {}).get("win_prob")
            oc = (row.get("outcomes") or {}).get(key)
            if wp is None or not oc:
                continue
            win = oc.get("win")
            if win is None:
                ret = oc.get("ret_pct")
                if ret is None:
                    continue
                win = ret > 0
            out.append((float(wp), 1 if win else 0))
        return out
    except Exception as e:
        log.warning("calibration fetch failed: %s", e)
        return []


async def calibration_verdict(horizon_days: int = 21) -> dict[str, Any]:
    """Full pipeline: fetch → compute → persist → return dict."""
    pairs = await _fetch_pairs(horizon_days)
    report = compute(pairs, horizon_days=horizon_days)
    await _persist(report)
    return report.to_dict()


async def _persist(report: CalibrationReport) -> None:
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return
        bins_json = json.dumps([b.to_dict() for b in report.bins])
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO calibration_reports (
                  horizon_days, brier_score, ece, verdict, bins
                ) VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                report.horizon_days,
                report.brier_score,
                report.ece,
                report.verdict,
                bins_json,
            )
    except Exception as e:
        log.warning("calibration persist failed: %s", e)


async def latest_report(horizon_days: int = 21) -> dict[str, Any] | None:
    """Best-effort fetch latest persisted report for an MCP / API caller."""
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM calibration_reports
                WHERE horizon_days = $1
                ORDER BY ts DESC
                LIMIT 1
                """,
                horizon_days,
            )
        if not row:
            return None
        return {
            "horizon_days": row["horizon_days"],
            "brier_score": float(row["brier_score"] or 0),
            "ece": float(row["ece"] or 0),
            "verdict": row["verdict"],
            "bins": row["bins"],
            "ts": row["ts"].isoformat() if row.get("ts") else None,
        }
    except Exception as e:
        log.warning("calibration latest_report failed: %s", e)
        return None
