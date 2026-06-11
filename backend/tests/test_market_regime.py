"""Unit tests for market_regime (Phase 8). Pure classifier - no I/O."""

from __future__ import annotations

import pytest

from app.services import market_regime as mr


# ── trend ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pct,expected",
    [
        (10.0, "up"),
        (5.0, "up"),
        (2.1, "up"),
        (1.5, "sideways"),
        (-1.0, "sideways"),
        (-2.1, "down"),
        (-10.0, "down"),
        (None, "sideways"),
    ],
)
def test_trend_classifier(pct, expected):
    assert mr._classify_trend(pct) == expected


# ── volatility ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vix,expected",
    [
        (35, "high"),
        (26, "high"),
        (20, "normal"),
        (14.9, "low"),
        (10, "low"),
        (None, "normal"),
    ],
)
def test_vol_classifier(vix, expected):
    assert mr._classify_vol(vix) == expected


# ── macro ───────────────────────────────────────────────────────────────────


def test_macro_risk_off_when_inverted():
    assert mr._classify_macro(5.0, 4.5) == "risk_off"


def test_macro_risk_off_when_fed_high():
    assert mr._classify_macro(5.5, 6.0) == "risk_off"


def test_macro_risk_on_normal():
    assert mr._classify_macro(2.0, 4.0) == "risk_on"


def test_macro_default_risk_on_when_missing():
    assert mr._classify_macro(None, None) == "risk_on"


# ── composite ───────────────────────────────────────────────────────────────


def test_composite_combines_trend_and_vol():
    assert mr._composite("up", "low") == "trend_up_low_vol"
    assert mr._composite("down", "high") == "trend_down_high_vol"
    assert mr._composite("sideways", "high") == "sideways_high_vol"


# ── classify integration ────────────────────────────────────────────────────


def test_classify_bull_low_vol():
    r = mr.classify(
        vix=12.0,
        spy_vs_sma200_pct=8.0,
        ten_y_yield=4.5,
        fed_funds=4.0,
    )
    assert r.trend == "up"
    assert r.volatility == "low"
    assert r.composite == "trend_up_low_vol"
    assert r.macro == "risk_on"
    assert r.confidence == 1.0


def test_classify_bear_high_vol():
    r = mr.classify(
        vix=35.0,
        spy_vs_sma200_pct=-10.0,
        ten_y_yield=4.0,
        fed_funds=5.5,
    )
    assert r.trend == "down"
    assert r.volatility == "high"
    assert r.composite == "trend_down_high_vol"
    assert r.macro == "risk_off"


def test_classify_neutral_confidence_lower():
    r = mr.classify(
        vix=20.0,
        spy_vs_sma200_pct=1.0,
        ten_y_yield=4.5,
        fed_funds=4.0,
    )
    assert r.composite == "sideways_normal_vol"
    assert r.confidence == 0.5


# ── multipliers ─────────────────────────────────────────────────────────────


def test_momentum_skill_amplified_in_uptrend():
    m = mr.multiplier_for("stock-analysis", "trend_up_low_vol")
    assert m > 1.0


def test_momentum_skill_suppressed_in_downtrend():
    m = mr.multiplier_for("stock-analysis", "trend_down_high_vol")
    assert m < 1.0


def test_defensive_skill_amplified_in_downtrend():
    m = mr.multiplier_for("risk-assessment", "trend_down_high_vol")
    assert m > 1.0


def test_unknown_skill_returns_one():
    assert mr.multiplier_for("nonexistent-skill", "trend_up_low_vol") == 1.0


def test_unknown_composite_returns_one():
    assert mr.multiplier_for("stock-analysis", "unknown_composite") == 1.0


def test_initial_multipliers_returns_all_known_skills():
    out = mr.initial_multipliers_for("trend_up_low_vol")
    expected = {
        "cash-deployment",
        "tax-loss-review",
        "dividend-strategy",
        "stock-analysis",
        "risk-assessment",
    }
    assert expected <= set(out.keys())


# ── skill_evidence regime integration ───────────────────────────────────────


def test_skill_evidence_scales_votes_by_regime():
    from app.services import skill_evidence

    snapshots = {
        "stock-analysis": {
            "status": "ok",
            "actionable": [{"symbol": "NVDA", "action": "BUY"}],
        },
    }
    out_up = skill_evidence.build(
        snapshots,
        ["NVDA"],
        regime_composite="trend_up_low_vol",
    )
    out_down = skill_evidence.build(
        snapshots,
        ["NVDA"],
        regime_composite="trend_down_high_vol",
    )
    assert out_up["NVDA"]["stock_analysis"] > out_down["NVDA"]["stock_analysis"]
    assert out_up["NVDA"]["skill_consensus"] > out_down["NVDA"]["skill_consensus"]


def test_skill_evidence_unchanged_without_regime():
    from app.services import skill_evidence

    snapshots = {
        "stock-analysis": {
            "status": "ok",
            "actionable": [{"symbol": "AAPL", "action": "BUY"}],
        },
    }
    out = skill_evidence.build(snapshots, ["AAPL"], regime_composite=None)
    assert out["AAPL"]["stock_analysis"] == 1
    assert out["AAPL"]["skill_consensus"] == 1
