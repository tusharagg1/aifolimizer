"""Unit tests for risk_gate (Phase 12). Pure `evaluate` only."""

from __future__ import annotations

from app.services import risk_gate as rg


def test_trade_when_all_clear():
    s = rg.evaluate(
        max_drawdown_pct=-5.0,
        vix=18.0,
        loss_streak_count=2,
        calibration_ece=0.10,
    )
    assert s.status == "trade"
    assert s.size_multiplier == 1.0
    assert s.reasons == []


def test_reduce_size_on_moderate_drawdown():
    s = rg.evaluate(
        max_drawdown_pct=-18.0,
        vix=20.0,
        loss_streak_count=0,
        calibration_ece=None,
    )
    assert s.status == "reduce_size"
    assert s.size_multiplier == 0.5
    assert "max DD" in " ".join(s.reasons)


def test_halt_on_severe_drawdown():
    s = rg.evaluate(
        max_drawdown_pct=-28.0,
        vix=20.0,
        loss_streak_count=0,
        calibration_ece=None,
    )
    assert s.status == "halt"
    assert s.size_multiplier == 0.0


def test_reduce_size_on_high_vix():
    s = rg.evaluate(
        max_drawdown_pct=-5.0,
        vix=40.0,
        loss_streak_count=0,
        calibration_ece=None,
    )
    assert s.status == "reduce_size"
    assert "VIX" in " ".join(s.reasons)


def test_reduce_size_on_loss_streak():
    s = rg.evaluate(
        max_drawdown_pct=-5.0,
        vix=18.0,
        loss_streak_count=6,
        calibration_ece=None,
    )
    assert s.status == "reduce_size"
    assert "consecutive losses" in " ".join(s.reasons)


def test_reduce_size_on_overconfident_calibration():
    s = rg.evaluate(
        max_drawdown_pct=-5.0,
        vix=18.0,
        loss_streak_count=0,
        calibration_ece=0.40,
    )
    assert s.status == "reduce_size"
    assert "calibration" in " ".join(s.reasons).lower()


def test_halt_escalates_over_reduce():
    s = rg.evaluate(
        max_drawdown_pct=-28.0,
        vix=40.0,
        loss_streak_count=10,
        calibration_ece=0.5,
    )
    assert s.status == "halt"
    assert s.size_multiplier == 0.0


def test_loss_streak_below_threshold_not_triggered():
    s = rg.evaluate(
        max_drawdown_pct=-5.0,
        vix=18.0,
        loss_streak_count=4,
        calibration_ece=None,
    )
    assert s.status == "trade"


def test_missing_inputs_do_not_fire():
    s = rg.evaluate(
        max_drawdown_pct=None,
        vix=None,
        loss_streak_count=0,
        calibration_ece=None,
    )
    assert s.status == "trade"


def test_state_serializable_to_dict():
    s = rg.evaluate(
        max_drawdown_pct=-18.0,
        vix=40.0,
        loss_streak_count=0,
        calibration_ece=None,
    )
    d = s.to_dict()
    assert d["status"] == "reduce_size"
    assert d["size_multiplier"] == 0.5
    assert isinstance(d["reasons"], list)
    assert "max_drawdown_pct" in d["triggers"]
    assert "triggered_at" in d
    assert "valid_until" in d
