"""`get_open_decisions` regression — added so the scheduler can mark-to-market."""
from __future__ import annotations

from pathlib import Path

import app.services.decision_memory as dm


def test_get_open_decisions_filters_resolved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dm, "_DECISIONS_FILE", tmp_path / "decisions.jsonl")

    dm.log_decision(
        ticker="AAA", action="BUY", conviction="Buy",
        entry_price=100.0, target_price=110.0, stop_price=95.0,
        thesis_summary="t", skill_used="adv",
    )
    dm.log_decision(
        ticker="BBB", action="BUY", conviction="Buy",
        entry_price=50.0, target_price=55.0, stop_price=48.0,
        thesis_summary="t", skill_used="adv",
    )
    # AAA hits target — resolved.
    dm.resolve_outcomes({"AAA": 111.0, "BBB": 51.0})

    open_now = dm.get_open_decisions()
    tickers = sorted(r["ticker"] for r in open_now)
    assert tickers == ["BBB"]
