"""Phase 2 - verify skill evidence flows into score.

Tests are pure (no DB, no network). They monkey-patch recommendations'
weights loader so we don't depend on Postgres being available.
"""

from __future__ import annotations

import pytest

from app.services import recommendations as rec_svc


@pytest.fixture(autouse=True)
def _force_weights(monkeypatch):
    """Pin weights so tests stay deterministic regardless of PG state."""
    monkeypatch.setattr(
        rec_svc,
        "_load_weights",
        lambda: {
            "w_tech": 1.0,
            "w_fund": 1.0,
            "w_macro": 1.0,
            "w_sentiment": 1.0,
            "w_skill": 0.5,
        },
    )
    yield


def _stub_tech(stage: int = 2, rsi: float = 55, score: float = 0.0) -> dict:
    return {
        "stage": stage,
        "minervini_score": 4,
        "rsi": rsi,
        "current_price": 100.0,
        "sma_50": 95.0,
        "trend": "uptrend",
        "rsi_signal": "neutral",
    }


def _stub_fund() -> dict:
    return {
        "pe_ratio": 18.0,
        "analyst_target_price": 115.0,
        "analyst_recommendation": "buy",
        "short_pct_float": 2.0,
        "eps_ttm": 6.0,
    }


def _stub_position(symbol: str = "AAPL", weight: float = 3.0) -> dict:
    return {
        "symbol": symbol,
        "weight": weight,
        "market_value_cad": 1500.0,
        "total_return_pct": 5.0,
        "currency": "USD",
        "asset_class": "stock",
    }


def test_skill_evidence_bullish_lifts_score():
    """Same fundamentals; bullish skill evidence raises score."""
    base = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.2,
        skill_evidence=None,
    )
    bull = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.2,
        skill_evidence={"skill_consensus": 4, "skill_confidence": 1.0},
    )
    assert bull["score"] > base["score"], (
        f"bullish skill evidence should lift score: base={base['score']} bull={bull['score']}"
    )


def test_skill_evidence_bearish_drops_score():
    base = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.2,
        skill_evidence=None,
    )
    bear = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.2,
        skill_evidence={"skill_consensus": -4, "skill_confidence": 1.0},
    )
    assert bear["score"] < base["score"]


def test_low_confidence_skill_does_not_vote():
    """skill_confidence < 0.5 should leave score effectively unchanged."""
    base = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.2,
        skill_evidence=None,
    )
    low_conf = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.2,
        skill_evidence={"skill_consensus": 4, "skill_confidence": 0.3},
    )
    # skill_score still contributes to raw_score (always added), but the
    # contribution at low consensus × w_skill is small. Convergence gate
    # should NOT count the skill vote - so confidence label must not switch
    # from medium to high purely due to skill.
    assert base["confidence"] == low_conf["confidence"], (
        f"low-confidence skill should not change confidence label: "
        f"base={base['confidence']} low={low_conf['confidence']}"
    )


def test_skill_score_clamped_to_two():
    """Even an extreme +8 consensus × 0.5 weight should clamp at +2."""
    rec = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.0,
        skill_evidence={"skill_consensus": 8, "skill_confidence": 1.0},
    )
    # Contribution = clamp(8/4 * 0.5, -2, +2) = 1.0; well within +2 cap.
    # Re-run with absurd consensus to validate clamp ceiling.
    rec_huge = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.0,
        skill_evidence={"skill_consensus": 100, "skill_confidence": 1.0},
    )
    # The 100-consensus case should not produce a score above the 8-consensus
    # case by more than 1.0 point (clamp at +2 - prior contribution).
    assert rec_huge["score"] - rec["score"] <= 1.1


def test_reasons_includes_skill_line_when_voting():
    rec = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.0,
        skill_evidence={"skill_consensus": 3, "skill_confidence": 0.75},
    )
    joined = " | ".join(rec.get("reasons") or [])
    assert "Skills consensus" in joined


def test_reasons_omits_skill_line_when_low_confidence():
    rec = rec_svc._score_position(
        "AAPL",
        _stub_position(),
        _stub_tech(stage=2),
        _stub_fund(),
        {},
        0.0,
        skill_evidence={"skill_consensus": 3, "skill_confidence": 0.3},
    )
    joined = " | ".join(rec.get("reasons") or [])
    assert "Skills consensus" not in joined
