"""Unit tests for skill_evidence (Phase 1)."""

from __future__ import annotations

from app.services import skill_evidence


def _snap(actionable=None, alerts=None, summary=None, status="ok"):
    return {
        "actionable": actionable or [],
        "alerts": alerts or [],
        "summary": summary or {},
        "status": status,
    }


def test_empty_inputs_zero_consensus_zero_confidence():
    out = skill_evidence.build({}, ["AAPL"])
    assert "AAPL" in out
    row = out["AAPL"]
    assert row["skill_consensus"] == 0
    assert row["skill_confidence"] == 0.0


def test_single_skill_bullish_vote():
    snaps = {
        "stock-analysis": _snap(
            actionable=[
                {"symbol": "AAPL", "action": "BUY"},
                {"symbol": "TSLA", "action": "HOLD"},
            ]
        ),
    }
    out = skill_evidence.build(snaps, ["AAPL", "TSLA"])
    assert out["AAPL"]["stock_analysis"] == 1
    assert out["AAPL"]["skill_consensus"] == 1
    assert out["TSLA"]["stock_analysis"] == 0
    assert out["TSLA"]["skill_consensus"] == 0
    # Both symbols see same skill_confidence (1 of 8 skills ran)
    assert out["AAPL"]["skill_confidence"] == round(1 / 8, 2)
    assert out["TSLA"]["skill_confidence"] == round(1 / 8, 2)


def test_bearish_votes_aggregate():
    snaps = {
        "stock-analysis": _snap(
            actionable=[
                {"symbol": "MSFT", "action": "SELL"},
            ]
        ),
        "tax-loss-review": _snap(
            actionable=[
                {"symbol": "MSFT", "unrealized_loss_pct": -12},
            ]
        ),
        "portfolio-health": _snap(
            actionable=[
                {"symbol": "MSFT", "issues": ["drawdown"]},
            ]
        ),
    }
    out = skill_evidence.build(snaps, ["MSFT"])
    assert out["MSFT"]["stock_analysis"] == -1
    assert out["MSFT"]["tax_loss"] == -1
    assert out["MSFT"]["portfolio_health"] == -1
    assert out["MSFT"]["skill_consensus"] == -3
    assert out["MSFT"]["skill_confidence"] == round(3 / 8, 2)


def test_missing_skill_does_not_count_as_negative():
    # Symbol has no opinion from any skill but cash-deployment ran successfully
    snaps = {
        "cash-deployment": _snap(
            actionable=[
                {"symbol": "NVDA", "allocation_cad": 1000},
            ]
        ),
    }
    out = skill_evidence.build(snaps, ["NVDA", "XEQT.TO"])
    # NVDA bullish
    assert out["NVDA"]["cash_deploy"] == 1
    assert out["NVDA"]["skill_consensus"] == 1
    # XEQT.TO had no opinion from the ran skill → 0, not -1
    assert out["XEQT.TO"]["cash_deploy"] == 0
    assert out["XEQT.TO"]["skill_consensus"] == 0
    # Same confidence (1 of 8 ran)
    assert out["NVDA"]["skill_confidence"] == out["XEQT.TO"]["skill_confidence"]


def test_error_status_skill_skipped():
    snaps = {
        "stock-analysis": _snap(status="error"),
        "cash-deployment": _snap(actionable=[{"symbol": "AAPL"}]),
    }
    out = skill_evidence.build(snaps, ["AAPL"])
    # stock-analysis errored → no contribution, confidence excludes it
    assert out["AAPL"]["cash_deploy"] == 1
    assert out["AAPL"]["stock_analysis"] == 0
    assert out["AAPL"]["skill_consensus"] == 1
    # Only 1 of 8 skills ran successfully
    assert out["AAPL"]["skill_confidence"] == round(1 / 8, 2)


def test_dividend_unsustainable_is_bearish():
    snaps = {
        "dividend-strategy": _snap(
            actionable=[
                {"symbol": "T", "unsustainable": True},
                {"symbol": "JNJ"},
            ]
        ),
    }
    out = skill_evidence.build(snaps, ["T", "JNJ"])
    assert out["T"]["dividend"] == -1
    assert out["JNJ"]["dividend"] == 1


def test_consensus_range_bounded():
    """Max +8 / min -8 with all 8 skills bullish / bearish."""
    snaps_bull = {
        "stock-analysis": _snap(actionable=[{"symbol": "X", "action": "BUY"}]),
        "cash-deployment": _snap(actionable=[{"symbol": "X"}]),
        "dividend-strategy": _snap(actionable=[{"symbol": "X"}]),
        "macro-impact": _snap(actionable=[{"symbol": "X", "action": "OVERWEIGHT"}]),
        # 4 others have no symbol opinion → 0
        "portfolio-health": _snap(),
        "risk-assessment": _snap(),
        "earnings-analyzer": _snap(),
        "tax-loss-review": _snap(),
    }
    out = skill_evidence.build(snaps_bull, ["X"])
    assert out["X"]["skill_consensus"] == 4  # 4 bullish, 4 neutral
    assert out["X"]["skill_confidence"] == 1.0  # all 8 ran
