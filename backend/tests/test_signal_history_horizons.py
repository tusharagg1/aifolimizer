"""`_classify_subset` no longer leaks redundant precision/recall/f1."""

from __future__ import annotations

from app.services.signal_history import _classify_subset


def test_subset_drops_precision_recall_f1() -> None:
    rows = [
        {"outcomes": {"h21": {"ret_pct": 5.0}}},
        {"outcomes": {"h21": {"ret_pct": -2.0}}},
        {"outcomes": {"h21": {"ret_pct": 3.0}}},
    ]
    out = _classify_subset(rows, horizon=21)
    assert out["n"] == 3
    assert "win_rate_pct" in out
    assert "expectancy_pct" in out
    # The three columns that collapsed to win_rate are gone.
    assert "precision" not in out
    assert "recall" not in out
    assert "f1" not in out


def test_empty_subset_short_circuits() -> None:
    out = _classify_subset([], horizon=21)
    assert out == {"n": 0}
