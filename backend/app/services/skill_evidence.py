"""Skill evidence aggregator (Phase 1).

Reads the latest snapshot per skill and produces a per-symbol evidence dict:

    { "AAPL": {
        "portfolio_health":  -1 | 0 | +1,
        "risk_evidence":     -1 | 0 | +1,
        "macro_evidence":    -1 | 0 | +1,
        "cash_deploy":       -1 | 0 | +1,
        "stock_analysis":    -1 | 0 | +1,
        "earnings":          -1 | 0 | +1,
        "tax_loss":          -1 | 0 | +1,
        "dividend":          -1 | 0 | +1,
        "skill_consensus":   int,           # sum of above (-N..+N)
        "skill_confidence":  float,         # n_with_data / 8
      },
      ...
    }

Symbols missing from a snapshot get 0 (neutral, NOT negative).

Phase 1 contract: this module ONLY produces the dict + logs it to
signal_history.skill_evidence JSONB.  It does NOT yet feed into scoring
(w_skill=0.0 baseline).  Phase 2 wires the contribution.

Skill name → key mapping is fixed.  When a new skill is codified, add
a mapping function here.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

# Ordered list of skills that contribute evidence + their evidence-key name.
# Order matters for skill_confidence denominator (max 8 contributors).
_SKILL_KEYS: tuple[tuple[str, str], ...] = (
    ("portfolio-health", "portfolio_health"),
    ("risk-assessment", "risk_evidence"),
    ("macro-impact", "macro_evidence"),
    ("cash-deployment", "cash_deploy"),
    ("stock-analysis", "stock_analysis"),
    ("earnings-analyzer", "earnings"),
    ("tax-loss-review", "tax_loss"),
    ("dividend-strategy", "dividend"),
)

_MAX_SKILLS = len(_SKILL_KEYS)


# ---------------------------------------------------------------------------
# Per-skill mappers — each returns dict[symbol] -> -1/+1
# ---------------------------------------------------------------------------


def _map_portfolio_health(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """portfolio-health flags positions with issues.  -1 (need attention)."""
    out: dict[str, int] = {}
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if not sym:
            continue
        # Flagged means problem — bearish vote.
        out[sym] = -1
    return out


def _map_risk_assessment(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """risk-assessment exposes concentration warnings in alerts list."""
    out: dict[str, int] = {}
    for row in snapshot.get("alerts") or []:
        sym = row.get("symbol") or row.get("ticker")
        if sym:
            out[sym] = -1
    return out


def _map_macro_impact(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """macro-impact summary may carry per-symbol sector_bias.  Best-effort."""
    out: dict[str, int] = {}
    summary = snapshot.get("summary") or {}
    biases = summary.get("symbol_bias") or {}
    for sym, bias in biases.items():
        if isinstance(bias, (int, float)) and bias != 0:
            out[sym] = 1 if bias > 0 else -1
    # Also accept actionable list shape if present
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if not sym or sym in out:
            continue
        action = (row.get("action") or "").upper()
        if action in {"OVERWEIGHT", "ADD", "BUY"}:
            out[sym] = 1
        elif action in {"UNDERWEIGHT", "TRIM", "SELL"}:
            out[sym] = -1
    return out


def _map_cash_deployment(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """cash-deployment.actionable lists sized BUY/ADD candidates → +1."""
    out: dict[str, int] = {}
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if sym:
            out[sym] = 1
    return out


def _map_stock_analysis(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """stock-analysis.actionable lists every position with action label."""
    out: dict[str, int] = {}
    bullish = {"BUY", "ADD"}
    bearish = {"SELL", "TRIM"}
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if not sym:
            continue
        action = (row.get("action") or "").upper()
        if action in bullish:
            out[sym] = 1
        elif action in bearish:
            out[sym] = -1
        # HOLD/WATCH/NO_EDGE explicitly stay 0
    return out


def _map_earnings_analyzer(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """earnings-analyzer.actionable carries upcoming-earnings recs.

    hedge_flag / unfavorable bias → -1.
    buy-through / favorable bias  → +1.
    Default 0.
    """
    out: dict[str, int] = {}
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if not sym:
            continue
        # Tolerate either rec shape or simple flag shape
        bear_recs = {"hedge", "trim", "wait"}
        if row.get("hedge_flag") or row.get("recommendation") in bear_recs:
            out[sym] = -1
        elif row.get("recommendation") in {"buy", "add", "buy_through"}:
            out[sym] = 1
    return out


def _map_tax_loss(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """tax-loss-review.actionable lists underwater positions → bearish (-1)."""
    out: dict[str, int] = {}
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if sym:
            out[sym] = -1
    return out


def _map_dividend(snapshot: Mapping[str, Any]) -> dict[str, int]:
    """dividend-strategy.actionable lists strong income holds → +1.

    Risk flags (unsustainable payout etc.) → -1.
    """
    out: dict[str, int] = {}
    for row in snapshot.get("actionable") or []:
        sym = row.get("symbol")
        if not sym:
            continue
        if row.get("unsustainable") or row.get("risk_flag"):
            out[sym] = -1
        else:
            out[sym] = 1
    return out


_MAPPERS = {
    "portfolio-health": _map_portfolio_health,
    "risk-assessment": _map_risk_assessment,
    "macro-impact": _map_macro_impact,
    "cash-deployment": _map_cash_deployment,
    "stock-analysis": _map_stock_analysis,
    "earnings-analyzer": _map_earnings_analyzer,
    "tax-loss-review": _map_tax_loss,
    "dividend-strategy": _map_dividend,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build(
    snapshots: Mapping[str, Mapping[str, Any] | None],
    symbols: Iterable[str],
    *,
    regime_composite: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build per-symbol evidence dict.

    Args:
      snapshots: { skill_name: snapshot_dict | None } — latest per skill.
                 Missing/None skills are skipped (count 0 toward confidence).
      symbols:   iterable of every symbol you want a row for (typically every
                 holding + every discovery candidate).
      regime_composite: Phase 8 — if set, per-skill votes are scaled by
                 market_regime.multiplier_for(skill, composite) so a skill
                 that historically underperforms in this regime contributes
                 less to consensus. None = no regime gating.

    Returns:
      { symbol: {
          <each skill key>: float (signed, regime-scaled in [-1.7, +1.7]),
          "skill_consensus": float (sum of above),
          "skill_confidence": float in [0, 1],
        } }
    """
    symbols = list({s for s in symbols if s})

    # Per-skill regime multipliers (None composite → all 1.0)
    multipliers: dict[str, float] = {}
    if regime_composite:
        try:
            from app.services import market_regime

            for skill_name, _ in _SKILL_KEYS:
                multipliers[skill_name] = market_regime.multiplier_for(
                    skill_name,
                    regime_composite,
                )
        except Exception as e:
            log.warning("regime multiplier lookup failed: %s", e)

    # Pre-compute per-skill maps once.
    skill_maps: dict[str, dict[str, int]] = {}
    skill_had_data: dict[str, bool] = {}
    for skill_name, evidence_key in _SKILL_KEYS:
        snap = snapshots.get(skill_name)
        if not snap or snap.get("status") not in {None, "ok"}:
            skill_had_data[evidence_key] = False
            skill_maps[evidence_key] = {}
            continue
        mapper = _MAPPERS.get(skill_name)
        if mapper is None:
            skill_had_data[evidence_key] = False
            skill_maps[evidence_key] = {}
            continue
        try:
            skill_maps[evidence_key] = mapper(snap)
            skill_had_data[evidence_key] = True
        except Exception as e:
            log.warning(
                "skill_evidence mapper failed for %s: %s",
                skill_name,
                e,
            )
            skill_had_data[evidence_key] = False
            skill_maps[evidence_key] = {}

    # Assemble per-symbol row.
    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        row: dict[str, Any] = {}
        n_with_data = 0
        consensus = 0.0
        for skill_name, evidence_key in _SKILL_KEYS:
            raw_vote = skill_maps[evidence_key].get(sym, 0)
            mult = multipliers.get(skill_name, 1.0)
            vote = raw_vote * mult if raw_vote else 0
            # Round to 2dp so JSONB stays compact + readable.
            row[evidence_key] = round(vote, 2) if vote else 0
            if skill_had_data[evidence_key]:
                n_with_data += 1
            consensus += vote
        # Round consensus to 2dp for the same reason. Downstream code
        # accepts float (scoring already coerces).
        row["skill_consensus"] = round(consensus, 2)
        row["skill_confidence"] = round(n_with_data / _MAX_SKILLS, 2)
        out[sym] = row
    return out


def build_for_portfolio(
    snapshots: Mapping[str, Mapping[str, Any] | None],
    portfolio_symbols: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Convenience wrapper — same as build() but takes portfolio symbol list."""
    return build(snapshots, portfolio_symbols)
