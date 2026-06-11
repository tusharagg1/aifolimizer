"""Locks backtest_core metric formulas (extracted from backtest + skill_backtest).

If these change, a published Sharpe/Sortino/drawdown/CAGR in track records or
trust reports changes too — so the formulas are pinned here.
"""

import math

import pandas as pd
import pytest

from app.services import backtest_core as bc


def test_annualize_factor():
    assert bc.annualize_factor() == math.sqrt(252)
    assert bc.annualize_factor(52) == math.sqrt(52)


def test_sharpe_matches_formula():
    r = pd.Series([0.01, 0.02, -0.01, 0.03])
    expected = r.mean() / r.std() * math.sqrt(252)
    assert bc.sharpe(r) == pytest.approx(expected)


def test_sharpe_edge_cases():
    assert bc.sharpe(pd.Series([], dtype=float)) == 0.0
    assert bc.sharpe(pd.Series([0.01, 0.01, 0.01])) == 0.0  # std == 0


def test_sortino_matches_formula():
    r = pd.Series([0.01, 0.02, -0.01, 0.03, -0.02])
    downside = r[r < 0]
    expected = r.mean() / downside.std() * math.sqrt(252)
    assert bc.sortino(r) == pytest.approx(expected)


def test_sortino_no_downside_is_zero():
    assert bc.sortino(pd.Series([0.01, 0.02, 0.03])) == 0.0
    assert bc.sortino(pd.Series([], dtype=float)) == 0.0


def test_max_drawdown_known_value():
    equity = pd.Series([100.0, 110.0, 105.0, 120.0, 90.0])
    # peak=120 at idx3, trough 90 -> (90-120)/120 = -25%
    assert bc.max_drawdown(equity) == pytest.approx(-25.0)


def test_max_drawdown_empty():
    assert bc.max_drawdown(pd.Series([], dtype=float)) == 0.0


def test_cagr_known_value():
    # 100 -> 121 over ~2 years (730 days)
    expected = ((121 / 100) ** (1 / (730 / 365.25)) - 1) * 100
    assert bc.cagr(100.0, 121.0, 730) == pytest.approx(expected)


def test_cagr_guards():
    assert bc.cagr(0.0, 100.0, 365) == 0.0
    assert bc.cagr(100.0, 121.0, 0) == 0.0
