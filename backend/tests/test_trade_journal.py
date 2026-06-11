"""Trade-journal: qualitative entry/exit capture + felt-state insights.

Captures psychological state (emotion, conviction source, confidence) that
decision_memory (facts) and shadow_account (price/date biases) cannot derive.
Storage: parallel ~/.aifolimizer/journal.jsonl, joined to decisions by ticker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.services.decision_memory as dm
import app.services.trade_journal as tj


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tj, "_JOURNAL_FILE", tmp_path / "journal.jsonl")
    monkeypatch.setattr(dm, "_DECISIONS_FILE", tmp_path / "decisions.jsonl")


def test_log_entry_persists_felt_state() -> None:
    out = tj.log_entry(
        ticker="aaa",
        emotion="fomo",
        conviction_source="social",
        confidence_1to5=4,
        plan_intended="enter on pullback, stop 5%",
        felt_note="saw it ripping on twitter",
        pre_trade_check_passed=False,
    )
    assert out["logged"] is True

    entries = tj._load_all()
    assert len(entries) == 1
    rec = entries[0]
    assert rec["ticker"] == "AAA"  # upper-cased
    assert rec["phase"] == "entry"
    assert rec["emotion"] == "fomo"
    assert rec["conviction_source"] == "social"
    assert rec["confidence_1to5"] == 4
    assert rec["pre_trade_check_passed"] is False
    assert rec["created_utc"]  # timestamp set
    assert rec["plan_followed"] is None  # exit fields start empty
    assert rec["lesson"] is None


def test_log_entry_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValueError):
        tj.log_entry(
            ticker="AAA",
            emotion="calm",
            conviction_source="thesis",
            confidence_1to5=7,
            plan_intended="",
            felt_note="",
            pre_trade_check_passed=True,
        )


def test_log_entry_rejects_unknown_emotion() -> None:
    with pytest.raises(ValueError):
        tj.log_entry(
            ticker="AAA",
            emotion="ecstatic",
            conviction_source="thesis",
            confidence_1to5=3,
            plan_intended="",
            felt_note="",
            pre_trade_check_passed=True,
        )


def test_log_exit_reconciles_newest_open_entry() -> None:
    tj.log_entry(
        ticker="AAA",
        emotion="conviction",
        conviction_source="thesis",
        confidence_1to5=5,
        plan_intended="hold to target",
        felt_note="",
        pre_trade_check_passed=True,
    )
    out = tj.log_exit(
        ticker="aaa",
        plan_followed=True,
        exit_emotion="calm",
        outcome_surprise="expected",
        lesson="thesis played out, sizing was right",
    )
    assert out["reconciled"] is True

    rec = tj._load_all()[0]
    assert rec["phase"] == "exit"
    assert rec["plan_followed"] is True
    assert rec["exit_emotion"] == "calm"
    assert rec["outcome_surprise"] == "expected"
    assert rec["lesson"] == "thesis played out, sizing was right"


def test_log_exit_noop_when_no_open_entry() -> None:
    out = tj.log_exit(
        ticker="ZZZ",
        plan_followed=False,
        exit_emotion="fear",
        outcome_surprise="shock",
        lesson="n/a",
    )
    assert out["reconciled"] is False


def test_insights_win_rate_by_emotion_joins_decision_outcomes() -> None:
    # Two trades: FOMO entry that stopped out, conviction entry that hit target.
    for tk, emo, conv_src, conf in [
        ("FOMOX", "fomo", "social", 4),
        ("WINX", "conviction", "thesis", 5),
    ]:
        tj.log_entry(
            ticker=tk,
            emotion=emo,
            conviction_source=conv_src,
            confidence_1to5=conf,
            plan_intended="",
            felt_note="",
            pre_trade_check_passed=True,
        )
        dm.log_decision(
            ticker=tk,
            action="BUY",
            conviction="Buy",
            entry_price=100.0,
            target_price=110.0,
            stop_price=95.0,
            thesis_summary="t",
            skill_used="trade-journal",
        )
    # FOMOX stops out, WINX hits target.
    dm.resolve_outcomes({"FOMOX": 94.0, "WINX": 111.0})

    ins = tj.get_insights()

    by_emo = ins["win_rate_by_emotion"]
    assert by_emo["fomo"]["win_rate_pct"] == 0.0
    assert by_emo["conviction"]["win_rate_pct"] == 100.0

    by_src = ins["win_rate_by_conviction_source"]
    assert by_src["social"]["win_rate_pct"] == 0.0
    assert by_src["thesis"]["win_rate_pct"] == 100.0

    # Confidence calibration: winners' avg confidence should exceed losers'.
    cal = ins["confidence_calibration"]
    assert cal["avg_confidence_wins"] == 5.0
    assert cal["avg_confidence_losses"] == 4.0


def test_insights_empty_when_no_entries() -> None:
    ins = tj.get_insights()
    assert ins["total_entries"] == 0
    assert ins["win_rate_by_emotion"] == {}
