"""Unit tests for calibration (Phase 9). Pure `compute` only."""

from __future__ import annotations

import random

from app.services import calibration as cal


def test_empty_input_returns_no_data_verdict():
    r = cal.compute([])
    assert r.verdict == "no_data"
    assert r.n_samples == 0
    assert r.brier_score == 0.0
    assert r.bins == []


def test_perfect_calibration_low_brier():
    """50%-prob bets that win 50% of the time → low Brier, well_calibrated."""
    random.seed(42)
    pairs = []
    for _ in range(200):
        outcome = random.choice([0, 1])
        pairs.append((0.5, outcome))
    r = cal.compute(pairs)
    assert r.brier_score < 0.30  # 0.5^2 = 0.25 expected
    assert r.verdict in {"well_calibrated", "noisy"}


def test_overconfident_detected():
    """Predicts 90% but actually wins ~50% → ECE big, overconfident."""
    pairs = [(0.9, 1 if i % 2 == 0 else 0) for i in range(200)]
    r = cal.compute(pairs)
    assert r.ece > 0.15
    assert r.verdict == "overconfident"


def test_underconfident_detected():
    """Predicts 30% but actually wins ~80% → underconfident."""
    pairs = []
    for i in range(200):
        outcome = 0 if i % 5 == 0 else 1
        pairs.append((0.3, outcome))
    r = cal.compute(pairs)
    assert r.ece > 0.15
    assert r.verdict == "underconfident"


def test_brier_correctness():
    """Single (0.7, 1) pair → Brier = (0.7-1)^2 = 0.09."""
    r = cal.compute([(0.7, 1)])
    assert abs(r.brier_score - 0.09) < 1e-4


def test_bins_aggregate_correct_counts():
    pairs = [
        (0.05, 1),
        (0.05, 0),
        (0.05, 0),  # bin 0
        (0.55, 1),
        (0.55, 1),  # bin 5
        (0.95, 0),  # bin 9
    ]
    r = cal.compute(pairs)
    assert sum(b.count for b in r.bins) == 6
    bin_centers = {b.bin_center for b in r.bins}
    assert 0.05 in bin_centers
    assert 0.55 in bin_centers
    assert 0.95 in bin_centers


def test_bin_actual_rate_correct():
    pairs = [(0.55, 1), (0.55, 1), (0.55, 0), (0.55, 0)]
    r = cal.compute(pairs)
    bin5 = next(b for b in r.bins if b.bin_center == 0.55)
    assert bin5.actual_rate == 0.5
    assert bin5.predicted_avg == 0.55


def test_extreme_prob_one_falls_in_last_bin():
    r = cal.compute([(1.0, 1), (1.0, 1)])
    # bin index 9 (last) for p=1.0
    bin_last = next(b for b in r.bins if b.bin_center == 0.95)
    assert bin_last.count == 2
    assert bin_last.actual_rate == 1.0


def test_n_samples_field_set():
    r = cal.compute([(0.5, 1)] * 17)
    assert r.n_samples == 17


def test_horizon_passed_through():
    r = cal.compute([(0.5, 1)] * 5, horizon_days=63)
    assert r.horizon_days == 63
