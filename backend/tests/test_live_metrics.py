"""Unit tests for live_metrics (Phase 10). Pure helpers only."""

from __future__ import annotations

from app.services import live_metrics as lm


# ── profit factor ───────────────────────────────────────────────────────────


def test_pf_basic():
    assert lm._profit_factor([2, 4, -1, -1]) == 3.0  # 6 / 2


def test_pf_only_wins_infinite():
    assert lm._profit_factor([1, 2, 3]) == float("inf")


def test_pf_only_losses_zero():
    assert lm._profit_factor([-1, -2, -3]) == 0.0


def test_pf_empty_zero():
    assert lm._profit_factor([]) == 0.0


# ── sharpe / sortino ────────────────────────────────────────────────────────


def test_sharpe_constant_returns_zero():
    # Zero std → sharpe undefined → 0.
    assert lm._sharpe([0.01, 0.01, 0.01]) == 0.0


def test_sharpe_positive_returns_positive():
    assert lm._sharpe([0.02, 0.01, 0.015, 0.005]) > 0


def test_sortino_positive_for_low_downside():
    # All up except one small drawdown.
    s = lm._sortino([0.02, 0.03, 0.01, -0.001, 0.02])
    assert s > 0


def test_sortino_single_return_zero():
    assert lm._sortino([0.5]) == 0.0


# ── max drawdown ────────────────────────────────────────────────────────────


def test_max_drawdown_no_drop():
    assert lm._max_drawdown([100, 105, 110, 115]) == 0.0


def test_max_drawdown_with_drop():
    # 100 → 120 → 90 → 110 → drawdown from 120 to 90 = -25%
    assert lm._max_drawdown([100, 120, 90, 110]) == -25.0


def test_max_drawdown_empty():
    assert lm._max_drawdown([]) == 0.0


# ── compute_from_closed_recs ────────────────────────────────────────────────


def _rec(return_pct, regime="trend_up_low_vol"):
    return {
        "return_pct": return_pct,
        "status": "closed_target",
        "regime_composite": regime,
    }


def test_compute_basic_kpis():
    recs = [
        _rec(0.05),
        _rec(0.03),
        _rec(-0.02),
        _rec(0.01),
    ]
    k = lm.compute_from_closed_recs(recs, equity_curve=[100, 105, 103, 107])
    assert k.n_trades == 4
    assert k.hit_rate == 0.75
    assert k.expectancy_pct > 0
    assert k.profit_factor > 1.0
    assert k.avg_win_pct > 0
    assert k.avg_loss_pct < 0


def test_compute_no_closed_recs_returns_zero_block():
    recs = [{"return_pct": None, "status": "open"}]
    k = lm.compute_from_closed_recs(recs)
    assert k.n_trades == 0
    assert k.profit_factor == 0.0


def test_regime_breakdown_only_includes_buckets_with_three():
    recs = (
        [_rec(0.02, "trend_up_low_vol")] * 5 + [_rec(-0.01, "sideways_high_vol")] * 2  # n=2 → excluded
    )
    k = lm.compute_from_closed_recs(recs)
    assert "trend_up_low_vol" in k.regime_breakdown
    assert "sideways_high_vol" not in k.regime_breakdown


def test_regime_breakdown_pf_correct():
    recs = [_rec(0.04), _rec(0.04), _rec(-0.02)]
    k = lm.compute_from_closed_recs(recs)
    bucket = k.regime_breakdown["trend_up_low_vol"]
    # wins 0.08, losses 0.02 → PF 4.0
    assert bucket["pf"] == 4.0
    assert bucket["n"] == 3
