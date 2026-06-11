"""Unit tests for signal_analytics (PG-backed). DB-free - fetch_scored is
monkeypatched. Verifies the flat-PG-row -> legacy-shape conversion feeds the
shared signal_history math correctly."""

from __future__ import annotations

import asyncio

from app.services import signal_analytics as sa
from app.db.repositories import signals_repo


def test_to_legacy_maps_flat_columns_to_nested_shape():
    pg_row = {
        "action": "BUY",
        "score": 8.0,
        "conviction": "high",
        "entry_price": 100.0,
        "tech_score": 2.0,
        "fund_score": 1.0,
        "macro_score": 0.0,
        "sentiment_score": 0.5,
        "rsi": 55.0,
        "stage": 2,
        "market_regime": "bull",
        "realized_return_5d": 4.0,
        "realized_return_21d": None,
    }
    legacy = sa._to_legacy(pg_row)
    assert legacy["action"] == "BUY"
    assert legacy["score"] == 8.0
    assert legacy["confidence"] == "high"
    assert legacy["features"]["sentiment"] == 0.5
    assert legacy["features"]["tech_score"] == 2.0
    # only non-null horizons become outcomes
    assert legacy["outcomes"]["h5"] == {"ret_pct": 4.0, "win": True}
    assert "h21" not in legacy["outcomes"]


def test_accuracy_report_pg_path(monkeypatch):
    rows = [
        {"action": "BUY", "score": 8.0, "conviction": "high", "realized_return_5d": 5.0},
        {"action": "BUY", "score": 8.0, "conviction": "high", "realized_return_5d": -2.0},
    ]

    async def fake_fetch(*, lookback_days=365, tenant_hash=None):
        return rows

    monkeypatch.setattr(signals_repo, "fetch_scored", fake_fetch)

    result = asyncio.run(sa.accuracy_report(horizon=5, min_count=1))
    assert result["n"] == 2
    assert "BUY" in result["by_action"]
    assert result["overall"]["n"] == 2
    # one win one loss -> 50%
    assert result["overall"]["win_rate_pct"] == 50.0


def test_decay_curve_pg_path(monkeypatch):
    rows = [
        {"action": "BUY", "score": 8.0, "realized_return_5d": 3.0, "realized_return_21d": 1.0},
        {"action": "BUY", "score": 7.0, "realized_return_5d": 1.0, "realized_return_21d": -1.0},
    ]

    async def fake_fetch(*, lookback_days=365, tenant_hash=None):
        return rows

    monkeypatch.setattr(signals_repo, "fetch_scored", fake_fetch)

    result = asyncio.run(sa.signal_decay_curve(horizons=(5, 21), min_count=1))
    assert result["curve"]["h5"]["n"] == 2
    assert result["curve"]["h5"]["avg_ret_pct"] == 2.0
    assert result["peak_horizon"] == "h5"
