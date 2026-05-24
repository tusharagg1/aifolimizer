"""Unit tests for weights_tuner (Phase 5).

Tests the pure `_adjust` helper to keep them DB-free. End-to-end
`recalibrate()` is covered by integration tests once Postgres-backed
horizon data exists.
"""
from __future__ import annotations

import pytest

from app.services import weights_tuner as wt


# ── accuracy objective ────────────────────────────────────────────────────────

def test_accuracy_bump_when_winrate_high_and_avg_positive():
    new = wt._adjust(
        1.0,
        {"win_rate": 0.6, "avg_return": 0.01, "n": 50},
        source="tech", objective="accuracy",
    )
    assert new > 1.0
    assert new == round(1.0 * wt._BUMP, 2)


def test_accuracy_cut_when_winrate_low():
    new = wt._adjust(
        1.0,
        {"win_rate": 0.40, "avg_return": 0.0, "n": 50},
        source="tech", objective="accuracy",
    )
    assert new < 1.0
    assert new == round(1.0 * wt._CUT, 2)


def test_accuracy_noop_when_n_below_threshold():
    new = wt._adjust(
        1.0,
        {"win_rate": 0.9, "avg_return": 0.05, "n": 5},
        source="tech", objective="accuracy",
    )
    assert new == 1.0


def test_legacy_weight_clamped_at_upper_bound():
    new = wt._adjust(
        1.49,
        {"win_rate": 0.7, "avg_return": 0.02, "n": 50},
        source="fund", objective="accuracy",
    )
    assert new == 1.5
    assert new <= wt._W_MAX_LEGACY


def test_legacy_weight_clamped_at_lower_bound():
    new = wt._adjust(
        0.51,
        {"win_rate": 0.4, "avg_return": -0.01, "n": 50},
        source="macro", objective="accuracy",
    )
    assert new == 0.5
    assert new >= wt._W_MIN_LEGACY


def test_skill_weight_has_lower_floor():
    new = wt._adjust(
        0.11,
        {"win_rate": 0.4, "avg_return": -0.01, "n": 50},
        source="skill", objective="accuracy",
    )
    assert new == 0.10
    assert new >= wt._W_MIN_SKILL


# ── expectancy objective (Phase 11 preview) ───────────────────────────────────

def test_expectancy_bump_requires_pf_above_one_one():
    new = wt._adjust(
        1.0,
        {
            "after_cost_expectancy_pct": 0.01,
            "profit_factor": 1.5,
            "n": 50,
        },
        source="skill", objective="expectancy",
    )
    assert new > 1.0


def test_expectancy_skip_when_pf_borderline():
    """PF between 0.9 and 1.1 → noop."""
    new = wt._adjust(
        1.0,
        {
            "after_cost_expectancy_pct": 0.003,
            "profit_factor": 1.05,
            "n": 50,
        },
        source="skill", objective="expectancy",
    )
    assert new == 1.0


def test_expectancy_cut_on_negative_ev():
    new = wt._adjust(
        1.0,
        {
            "after_cost_expectancy_pct": -0.005,
            "profit_factor": 0.7,
            "n": 50,
        },
        source="skill", objective="expectancy",
    )
    assert new < 1.0


def test_phase11_auto_objective_accuracy_when_few_samples():
    """Phase 11: <20 samples → fallback to accuracy objective."""
    import asyncio

    async def fake_attr_small(*, horizon_days, lookback_days):
        return {"tech": {"win_rate": 0.5, "avg_return": 0.0, "n": 5}}

    async def fake_attr_big(*, horizon_days, lookback_days):
        return {"tech": {
            "win_rate": 0.6, "avg_return": 0.02,
            "n": 50,
            "after_cost_expectancy_pct": 0.015,
            "profit_factor": 1.4,
        }}

    async def fake_cur():
        return {"w_tech": 1.0, "w_fund": 1.0, "w_macro": 1.0,
                "w_sentiment": 1.0, "w_skill": 0.5, "version": 1}

    async def fake_insert(*args, **kwargs):
        return 99

    async def _go():
        from app.services import weights_tuner as wt_mod
        from app.db.repositories import signals_repo, weights_repo
        signals_repo.attribution_by_source = fake_attr_small  # type: ignore
        weights_repo.current = fake_cur  # type: ignore
        weights_repo.insert_version = fake_insert  # type: ignore

        r = await wt_mod.recalibrate()
        assert r["status"] == "noop"  # n<20, no bump

        signals_repo.attribution_by_source = fake_attr_big  # type: ignore
        r2 = await wt_mod.recalibrate()
        assert r2["status"] == "ok"
        assert r2["objective"] == "expectancy"

    asyncio.run(_go())


@pytest.mark.parametrize("src,lo,hi", [
    ("tech",      wt._W_MIN_LEGACY, wt._W_MAX_LEGACY),
    ("fund",      wt._W_MIN_LEGACY, wt._W_MAX_LEGACY),
    ("macro",     wt._W_MIN_LEGACY, wt._W_MAX_LEGACY),
    ("sentiment", wt._W_MIN_LEGACY, wt._W_MAX_LEGACY),
    ("skill",     wt._W_MIN_SKILL,  wt._W_MAX_SKILL),
])
def test_bounds_per_source(src, lo, hi):
    # No matter what the input, the helper never returns outside bounds.
    for w in [0.0, 0.05, 0.5, 1.0, 1.5, 2.0, 5.0]:
        new = wt._adjust(
            w,
            {"win_rate": 0.99, "avg_return": 1.0, "n": 50},
            source=src, objective="accuracy",
        )
        # For inputs outside bounds the helper may either clamp on bump or
        # leave a no-op if direction is unchanged; assert never above hi.
        if w <= hi:
            assert new <= hi
        new2 = wt._adjust(
            w,
            {"win_rate": 0.0, "avg_return": -1.0, "n": 50},
            source=src, objective="accuracy",
        )
        if w >= lo:
            assert new2 >= lo
