"""KPI benchmark test suite — aifolimizer.

Categories:
  1. Quant primitives   — returns, Sharpe, Sortino, VaR, beta, correlation
  2. Backtest metrics   — _sharpe, _max_drawdown, _cagr (pandas-level)
  3. Recommendation     — score direction, convergence gate, stop/target
                          directionality, Kelly bounds, EV calculation
  4. Cache / TTL        — recommendation cache invalidation

Run from backend/:
  .venv\\Scripts\\python -m pytest tests/test_kpi_suite.py -v
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

# ── 1. Quant primitives ─────────────────────────────────────────────────────

from app.services.quant import (
    annualized_volatility,
    beta,
    correlation,
    expected_shortfall,
    historical_var,
    log_returns,
    max_drawdown,
    sharpe_ratio,
    simple_returns,
    sortino_ratio,
)


class TestSimpleReturns:
    def test_basic_growth(self):
        r = simple_returns([100.0, 110.0, 121.0])
        assert r == pytest.approx([0.10, 0.10], rel=1e-6)

    def test_decline(self):
        r = simple_returns([100.0, 90.0])
        assert r == pytest.approx([-0.10], rel=1e-6)

    def test_skip_zero_previous(self):
        r = simple_returns([0.0, 10.0, 20.0])
        assert r == pytest.approx([1.0], rel=1e-6)

    def test_empty(self):
        assert simple_returns([]) == []

    def test_single_value(self):
        assert simple_returns([100.0]) == []


class TestLogReturns:
    def test_symmetric_property(self):
        up = log_returns([100.0, 110.0])
        down = log_returns([110.0, 100.0])
        assert abs(up[0] + down[0]) < 1e-10

    def test_skip_nonpositive(self):
        r = log_returns([0.0, 10.0, 20.0])
        assert len(r) == 1
        assert r[0] == pytest.approx(math.log(2.0))


class TestAnnualizedVolatility:
    def test_constant_returns_zero_vol(self):
        assert annualized_volatility([0.01] * 100) == pytest.approx(0.0, abs=1e-10)

    def test_scales_with_sqrt_periods(self):
        daily_returns = [0.01, -0.01, 0.02, -0.02] * 25
        vol_252 = annualized_volatility(daily_returns, periods=252)
        vol_126 = annualized_volatility(daily_returns, periods=126)
        ratio = vol_252 / vol_126
        assert ratio == pytest.approx(math.sqrt(2), rel=1e-3)

    def test_insufficient_data(self):
        assert annualized_volatility([0.01]) == 0.0


class TestSharpeRatio:
    def test_positive_for_rising_series(self):
        # Alternating to avoid zero-vol; mean is positive → Sharpe > 0
        returns = [0.001 + (0.0005 if i % 2 == 0 else -0.0005) for i in range(252)]
        s = sharpe_ratio(returns)
        assert s is not None and s > 0

    def test_negative_for_declining_series(self):
        returns = [-0.001 + (0.0005 if i % 2 == 0 else -0.0005) for i in range(252)]
        s = sharpe_ratio(returns)
        assert s is not None and s < 0

    def test_zero_vol_returns_none(self):
        # Constant non-zero returns → pstdev = 0 → None
        assert sharpe_ratio([0.001] * 100) is None

    def test_insufficient_data(self):
        assert sharpe_ratio([0.01]) is None


class TestSortinoRatio:
    def test_higher_than_sharpe_for_upside_skew(self):
        # Mix returns so vol > 0; upside skew → Sortino > Sharpe
        returns = [0.01 if i % 3 == 0 else -0.001 for i in range(252)]
        sortino = sortino_ratio(returns)
        sharpe = sharpe_ratio(returns)
        assert sortino is not None and sharpe is not None
        assert sortino > sharpe

    def test_all_positive_returns_gives_high_sortino(self):
        returns = [0.005] * 252
        s = sortino_ratio(returns)
        assert s is None  # no downside returns → downside_dev = 0 → None


class TestHistoricalVaR:
    def test_basic_95_confidence(self):
        returns = sorted([-0.02, -0.015, -0.01, -0.005, 0.0, 0.005, 0.01, 0.015, 0.02, 0.025])
        var = historical_var(returns, confidence=0.95)
        assert var >= 0.0

    def test_empty_returns_zero(self):
        assert historical_var([]) == 0.0

    def test_all_positive_returns_zero_var(self):
        var = historical_var([0.01] * 100, confidence=0.95)
        assert var == 0.0


class TestExpectedShortfall:
    def test_es_ge_var(self):
        returns = [(-0.01 * (i + 1)) for i in range(100)] + [0.005] * 100
        var = historical_var(returns, 0.95)
        es = expected_shortfall(returns, 0.95)
        assert es >= var

    def test_empty_returns_zero(self):
        assert expected_shortfall([]) == 0.0


class TestMaxDrawdown:
    def test_monotone_rise_no_drawdown(self):
        prices = [100.0 + i for i in range(100)]
        assert max_drawdown(prices) == pytest.approx(0.0, abs=1e-10)

    def test_peak_then_crash(self):
        prices = [100.0, 120.0, 80.0]
        dd = max_drawdown(prices)
        assert dd == pytest.approx(-1 / 3, rel=1e-5)

    def test_always_non_positive(self):
        import random
        random.seed(42)
        prices = [abs(random.gauss(100, 10)) + 1 for _ in range(200)]
        assert max_drawdown(prices) <= 0.0


class TestBeta:
    def test_market_beta_is_one(self):
        r = [0.01, -0.01, 0.02, -0.02] * 25
        b = beta(r, r)
        assert b == pytest.approx(1.0, rel=1e-6)

    def test_inverse_beta_is_minus_one(self):
        r = [0.01, -0.01, 0.02, -0.02] * 25
        b = beta(r, [-x for x in r])
        assert b == pytest.approx(-1.0, rel=1e-6)

    def test_insufficient_data_returns_none(self):
        assert beta([0.01], [0.01]) is None

    def test_zero_variance_benchmark_returns_none(self):
        assert beta([0.01, 0.02], [0.0, 0.0]) is None


class TestCorrelation:
    def test_perfect_positive(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        assert correlation(xs, xs) == pytest.approx(1.0, rel=1e-6)

    def test_perfect_negative(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [-1.0, -2.0, -3.0, -4.0]
        assert correlation(xs, ys) == pytest.approx(-1.0, rel=1e-6)

    def test_uncorrelated_approx_zero(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [4.0, 1.0, 3.0, 2.0]
        c = correlation(xs, ys)
        assert c is not None and abs(c) < 0.5

    def test_constant_series_returns_none(self):
        assert correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


# ── 2. Backtest metrics (pandas helpers) ───────────────────────────────────

from app.services.backtest import _cagr, _max_drawdown as _bt_max_dd, _sharpe as _bt_sharpe


class TestBacktestSharpe:
    def test_zero_std_returns_zero(self):
        s = _bt_sharpe(pd.Series([0.01, 0.01, 0.01]))
        assert s == 0.0

    def test_empty_returns_zero(self):
        assert _bt_sharpe(pd.Series([], dtype=float)) == 0.0

    def test_positive_for_uniform_gains(self):
        s = _bt_sharpe(pd.Series([0.002] * 50 + [0.001] * 50))
        assert s > 0


class TestBacktestMaxDrawdown:
    def test_monotone_equity_zero_dd(self):
        equity = pd.Series([1.0, 1.1, 1.2, 1.3])
        assert _bt_max_dd(equity) == pytest.approx(0.0, abs=1e-10)

    def test_50pct_crash(self):
        equity = pd.Series([1.0, 1.5, 0.75])
        dd = _bt_max_dd(equity)
        assert dd == pytest.approx(-50.0, rel=1e-4)

    def test_empty_returns_zero(self):
        assert _bt_max_dd(pd.Series([], dtype=float)) == 0.0


class TestBacktestCAGR:
    def test_double_in_one_year(self):
        cagr = _cagr(100.0, 200.0, 365)
        assert cagr == pytest.approx(100.0, rel=1e-3)

    def test_zero_days_returns_zero(self):
        assert _cagr(100.0, 200.0, 0) == 0.0

    def test_zero_start_returns_zero(self):
        assert _cagr(0.0, 200.0, 365) == 0.0

    def test_flat_returns_zero(self):
        assert _cagr(100.0, 100.0, 365) == pytest.approx(0.0, abs=1e-6)

    def test_negative_growth(self):
        cagr = _cagr(100.0, 50.0, 365)
        assert cagr < 0


# ── 3. Recommendation engine ───────────────────────────────────────────────

from app.services.recommendations import _score_position, _direction


class TestDirectionHelper:
    def test_bullish_above_threshold(self):
        assert _direction(0.5) == 1

    def test_bearish_below_threshold(self):
        assert _direction(-0.5) == -1

    def test_neutral_inside_band(self):
        assert _direction(0.2) == 0
        assert _direction(-0.2) == 0

    def test_custom_threshold(self):
        assert _direction(0.3, threshold=0.2) == 1
        assert _direction(0.3, threshold=0.4) == 0


# Minimal position fixture — no live data needed
_BASE_POS = {
    "symbol": "TEST",
    "name": "Test Corp",
    "asset_class": "equity",
    "weight": 5.0,
    "total_return_pct": 10.0,
    "market_value_cad": 5000.0,
}

_BULL_TECH = {
    "stage": 2,
    "minervini_score": 6,
    "rsi_14": 55.0,
    "macd_hist": 0.5,
    "trend": "uptrend",
    "sma_50": 90.0,
    "current_price": 100.0,
    "pct_from_52w_high": -5.0,
}
_BEAR_TECH = {
    "stage": 4,
    "minervini_score": 1,
    "rsi_14": 78.0,
    "macd_hist": -0.5,
    "trend": "downtrend",
    "sma_50": 110.0,
    "current_price": 100.0,
    "pct_from_52w_high": -3.0,
}

_BULL_FUND = {
    "analyst_recommendation": "strong_buy",
    "analyst_target_price": 130.0,
    "eps_growth_yoy": 0.25,
    "short_interest": 0.02,
    "revenue_growth_yoy": 0.20,
}
_BEAR_FUND = {
    "analyst_recommendation": "sell",
    "analyst_target_price": 75.0,
    "eps_growth_yoy": -0.15,
    "short_interest": 0.20,
}

_BULL_MACRO = {"market_regime": "bull_low_fear", "vix": 15.0, "fear_greed_score": 50.0}
_BEAR_MACRO = {"market_regime": "bear_high_fear", "vix": 35.0, "fear_greed_score": 15.0}


class TestRecommendationScoreDirection:
    def test_all_bullish_signals_give_buy(self):
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        assert rec["action"] == "BUY"
        assert rec["score"] >= 7.5

    def test_all_bearish_signals_give_sell(self):
        rec = _score_position("TEST", _BASE_POS, _BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5)
        assert rec["action"] == "SELL"
        assert rec["score"] < 5.5

    def test_neutral_signals_give_hold_watch_or_no_edge(self):
        # No directional sub-signals → engine must refuse BUY/SELL.
        # Acceptable: HOLD (neutral), WATCH (forming), NO_EDGE (no advantage).
        neutral_tech = {"stage": None, "rsi_14": 50.0, "macd_hist": 0.0, "trend": None,
                        "sma_50": 100.0, "current_price": 100.0}
        neutral_fund = {"analyst_recommendation": "hold"}
        neutral_macro = {"market_regime": "bull_low_fear", "vix": 18.0}
        rec = _score_position("TEST", _BASE_POS, neutral_tech, neutral_fund, neutral_macro, 0.0)
        assert rec["action"] in ("HOLD", "WATCH", "NO_EDGE")
        assert rec["action"] not in ("BUY", "SELL")

    def test_conflicting_signals_capped_at_watch(self):
        # Bullish tech + bearish fundamentals + bearish macro → low confidence → WATCH cap
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5)
        assert rec["action"] != "BUY", "Conflicting signals must not produce BUY"

    def test_overweight_penalty_applied(self):
        heavy = {**_BASE_POS, "weight": 25.0}
        # Use neutral signals so score doesn't hit ceiling before penalty
        neutral_tech = {
            "stage": None, "rsi_14": 50.0, "macd_hist": 0.1,
            "trend": "uptrend", "sma_50": 95.0, "current_price": 100.0,
        }
        neutral_fund = {"analyst_recommendation": "hold", "analyst_target_price": 110.0}
        rec_normal = _score_position(
            "TEST", _BASE_POS, neutral_tech, neutral_fund, _BULL_MACRO, 0.1
        )
        rec_heavy = _score_position(
            "TEST", heavy, neutral_tech, neutral_fund, _BULL_MACRO, 0.1
        )
        assert rec_heavy["score"] < rec_normal["score"]
        assert "overweight" in rec_heavy["flags"]

    def test_deep_loss_penalty_applied(self):
        loser = {**_BASE_POS, "total_return_pct": -30.0}
        neutral_tech = {
            "stage": None, "rsi_14": 50.0, "macd_hist": 0.1,
            "trend": "uptrend", "sma_50": 95.0, "current_price": 100.0,
        }
        neutral_fund = {"analyst_recommendation": "hold", "analyst_target_price": 110.0}
        rec_normal = _score_position(
            "TEST", _BASE_POS, neutral_tech, neutral_fund, _BULL_MACRO, 0.1
        )
        rec_loser = _score_position(
            "TEST", loser, neutral_tech, neutral_fund, _BULL_MACRO, 0.1
        )
        assert rec_loser["score"] < rec_normal["score"]

    def test_score_bounded_0_to_10(self):
        for tech, fund, macro, sent in [
            (_BULL_TECH, _BULL_FUND, _BULL_MACRO, 1.0),
            (_BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -1.0),
        ]:
            rec = _score_position("TEST", _BASE_POS, tech, fund, macro, sent)
            assert 0.0 <= rec["score"] <= 10.0

    def test_etf_skips_fundamentals(self):
        etf_pos = {**_BASE_POS, "asset_class": "etf"}
        rec = _score_position("ETF", etf_pos, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.0)
        # ETF should have fund_score = 0.0
        assert rec["fund_score"] == 0.0


class TestStopLossDirectionality:
    """Critical: SELL stop must be ABOVE current price; BUY stop BELOW."""

    def test_sell_stop_above_current_price(self):
        rec = _score_position("TEST", _BASE_POS, _BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5)
        if rec["action"] == "SELL" and rec["stop_loss"] and rec["current_price"]:
            assert rec["stop_loss"] > rec["current_price"], (
                f"SELL stop {rec['stop_loss']} must be ABOVE current {rec['current_price']}"
            )

    def test_sell_target_below_current_price(self):
        rec = _score_position("TEST", _BASE_POS, _BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5)
        if rec["action"] == "SELL" and rec["take_profit"] and rec["current_price"]:
            assert rec["take_profit"] < rec["current_price"], (
                f"SELL target {rec['take_profit']} must be BELOW current {rec['current_price']}"
            )

    def test_buy_stop_below_current_price(self):
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        if rec["action"] == "BUY" and rec["stop_loss"] and rec["current_price"]:
            assert rec["stop_loss"] < rec["current_price"], (
                f"BUY stop {rec['stop_loss']} must be BELOW current {rec['current_price']}"
            )

    def test_buy_target_above_current_price(self):
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        if rec["action"] == "BUY" and rec["take_profit"] and rec["current_price"]:
            assert rec["take_profit"] > rec["current_price"], (
                f"BUY target {rec['take_profit']} must be ABOVE current {rec['current_price']}"
            )

    def test_sell_risk_reward_positive(self):
        rec = _score_position("TEST", _BASE_POS, _BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5)
        if rec["action"] == "SELL" and rec["risk_reward"] is not None:
            assert rec["risk_reward"] > 0, "R/R must be positive for valid SELL setup"

    def test_max_loss_dollars_positive_for_sell(self):
        rec = _score_position("TEST", _BASE_POS, _BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5)
        if rec["action"] == "SELL" and rec["max_loss_dollars"] is not None:
            assert rec["max_loss_dollars"] > 0, "Max-loss must be positive for SELL"


class TestKellyCriterion:
    def test_kelly_bounded_0_to_20(self):
        for tech, fund, macro, sent in [
            (_BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5),
            (_BEAR_TECH, _BEAR_FUND, _BEAR_MACRO, -0.5),
        ]:
            rec = _score_position("TEST", _BASE_POS, tech, fund, macro, sent)
            if rec["kelly_pct"] is not None:
                assert 0.0 <= rec["kelly_pct"] <= 20.0

    def test_sell_rec_uses_lower_win_prob(self):
        # SELL analyst rec → win_prob = 0.35 (hardcoded floor)
        sell_fund = {**_BEAR_FUND, "analyst_recommendation": "sell"}
        rec = _score_position(
            "TEST", _BASE_POS, _BEAR_TECH, sell_fund, _BEAR_MACRO, -0.5
        )
        assert rec["win_prob"] == pytest.approx(0.35)

    def test_hold_rec_win_prob_interpolated_from_score(self):
        # hold recommendation → win_prob derived from score, not hardcoded
        hold_fund = {**_BULL_FUND, "analyst_recommendation": "hold"}
        rec = _score_position(
            "TEST", _BASE_POS, _BULL_TECH, hold_fund, _BULL_MACRO, 0.3
        )
        expected = min(0.65, max(0.35, 0.35 + (rec["score"] / 10) * 0.30))
        assert rec["win_prob"] == pytest.approx(expected, rel=1e-3)


class TestExpectedValue:
    def test_ev_dollars_positive_for_bull_setup(self):
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        if rec["ev_dollars"] is not None:
            assert rec["ev_dollars"] > 0

    def test_ev_zero_without_position_value(self):
        no_value_pos = {**_BASE_POS, "market_value_cad": 0.0}
        rec = _score_position("TEST", no_value_pos, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        assert rec["ev_dollars"] is None

    def test_max_loss_requires_position_value(self):
        no_value_pos = {**_BASE_POS, "market_value_cad": 0.0}
        rec = _score_position("TEST", no_value_pos, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        assert rec["max_loss_dollars"] is None


class TestConfidenceGate:
    def test_high_confidence_all_agree(self):
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, _BULL_FUND, _BULL_MACRO, 0.5)
        assert rec["confidence"] in ("high", "medium")

    def test_low_confidence_mixed_signals(self):
        # Strong bull tech + strong bear fund + bear macro
        mixed_fund = {**_BEAR_FUND, "analyst_recommendation": "sell"}
        rec = _score_position("TEST", _BASE_POS, _BULL_TECH, mixed_fund, _BEAR_MACRO, 0.5)
        assert rec["confidence"] in ("low", "medium")


# ── 4. Cache TTL behavior ──────────────────────────────────────────────────

from app.services.recommendations import _REC_CACHE, get_recommendations


class TestRecommendationCache:
    def test_cache_populated_on_first_call(self, monkeypatch):
        calls = []

        def fake_tech(symbols):
            calls.append("tech")
            return {s: _BULL_TECH for s in symbols}

        def fake_fund(symbols):
            calls.append("fund")
            return {s: _BULL_FUND for s in symbols}

        def fake_macro():
            calls.append("macro")
            return _BULL_MACRO

        monkeypatch.setattr("app.services.recommendations.tech_svc.get_technicals", fake_tech)
        monkeypatch.setattr("app.services.recommendations.fund_svc.get_fundamentals", fake_fund)
        monkeypatch.setattr("app.services.recommendations.market_breadth", fake_macro)
        monkeypatch.setattr("app.services.recommendations._get_sentiment", lambda s: 0.0)
        monkeypatch.setattr("app.services.recommendations.get_earnings_expected_moves", lambda *a, **k: {})

        positions = [{**_BASE_POS, "symbol": "CACHE1"}]
        _REC_CACHE.clear()

        result1 = get_recommendations(positions)
        call_count_after_first = len(calls)
        result2 = get_recommendations(positions)

        # Second call hits cache — no additional service calls
        assert len(calls) == call_count_after_first
        assert result1 == result2

    def test_cache_misses_on_portfolio_change(self, monkeypatch):
        calls = []

        def fake_tech(symbols):
            calls.append("tech")
            return {s: _BULL_TECH for s in symbols}

        def fake_fund(symbols):
            return {s: _BULL_FUND for s in symbols}

        def fake_macro():
            return _BULL_MACRO

        monkeypatch.setattr("app.services.recommendations.tech_svc.get_technicals", fake_tech)
        monkeypatch.setattr("app.services.recommendations.fund_svc.get_fundamentals", fake_fund)
        monkeypatch.setattr("app.services.recommendations.market_breadth", fake_macro)
        monkeypatch.setattr("app.services.recommendations._get_sentiment", lambda s: 0.0)
        monkeypatch.setattr("app.services.recommendations.get_earnings_expected_moves", lambda *a, **k: {})

        _REC_CACHE.clear()
        get_recommendations([{**_BASE_POS, "symbol": "A1"}])
        count_after_a = len(calls)

        get_recommendations([{**_BASE_POS, "symbol": "B1"}])
        # Different portfolio → cache miss → new tech call
        assert len(calls) > count_after_a

    def test_empty_portfolio_returns_empty(self):
        assert get_recommendations([]) == []
