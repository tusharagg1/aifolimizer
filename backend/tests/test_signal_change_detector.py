"""Unit tests for signal_change_detector (Phase 4)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import signal_change_detector as scd


_TS = datetime.now(tz=timezone.utc)


def _sig(symbol, action, score, conviction="medium"):
    return {
        "symbol": symbol,
        "action": action,
        "score": score,
        "conviction": conviction,
    }


def test_first_strong_action_is_material():
    out = scd.detect_changes({}, [_sig("AAPL", "BUY", 8.0)], _TS)
    assert len(out) == 1
    assert out[0].new_action == "BUY"
    assert out[0].prev_action is None


def test_first_hold_is_not_material():
    out = scd.detect_changes({}, [_sig("AAPL", "HOLD", 5.0)], _TS)
    assert out == []


def test_action_flip_detected():
    prev = {"AAPL": {"action": "HOLD", "score": 5.0, "conviction": "medium"}}
    out = scd.detect_changes(prev, [_sig("AAPL", "BUY", 7.0)], _TS)
    assert len(out) == 1
    assert "action HOLD→BUY" in " ".join(out[0].reasons)


def test_hold_to_watch_is_noise():
    prev = {"X": {"action": "HOLD", "score": 5.0, "conviction": "medium"}}
    out = scd.detect_changes(prev, [_sig("X", "WATCH", 5.2)], _TS)
    assert out == []


def test_conviction_step_up_detected():
    prev = {"X": {"action": "BUY", "score": 7.0, "conviction": "medium"}}
    out = scd.detect_changes(prev, [_sig("X", "BUY", 7.1, "high")], _TS)
    assert len(out) == 1
    assert "conviction" in " ".join(out[0].reasons)


def test_conviction_step_down_not_detected():
    prev = {"X": {"action": "BUY", "score": 7.0, "conviction": "high"}}
    out = scd.detect_changes(prev, [_sig("X", "BUY", 7.1, "medium")], _TS)
    assert out == []  # downstep ignored to avoid spam


def test_large_score_move_detected():
    prev = {"X": {"action": "HOLD", "score": 5.0, "conviction": "medium"}}
    # score swings 5 → 7.5 (+2.5pt), action stays HOLD
    out = scd.detect_changes(prev, [_sig("X", "HOLD", 7.5)], _TS)
    assert len(out) == 1
    assert "score" in " ".join(out[0].reasons)


def test_small_score_move_ignored():
    prev = {"X": {"action": "HOLD", "score": 5.0, "conviction": "medium"}}
    out = scd.detect_changes(prev, [_sig("X", "HOLD", 6.5)], _TS)
    assert out == []


def test_dedup_key_format():
    chg_list = scd.detect_changes(
        {"X": {"action": "HOLD", "score": 5.0, "conviction": "medium"}},
        [_sig("X", "BUY", 8.0, "high")],
        _TS,
    )
    assert len(chg_list) == 1
    key = chg_list[0].dedup_key()
    assert "X:BUY:" in key  # symbol:action:date


def test_alert_priority_strong_buy_is_high():
    out = scd.detect_changes(
        {"X": {"action": "HOLD", "score": 5.0, "conviction": "medium"}},
        [_sig("X", "BUY", 8.5, "high")],
        _TS,
    )
    assert out[0].alert_priority() == "high"


def test_alert_priority_sell_is_high():
    out = scd.detect_changes(
        {"X": {"action": "BUY", "score": 7.0, "conviction": "high"}},
        [_sig("X", "SELL", 3.0, "high")],
        _TS,
    )
    assert out[0].alert_priority() == "high"


def test_alert_body_includes_score():
    out = scd.detect_changes(
        {"X": {"action": "HOLD", "score": 4.5, "conviction": "medium"}},
        [_sig("X", "BUY", 8.1, "high")],
        _TS,
    )
    body = out[0].alert_body()
    assert "4.5" in body
    assert "8.1" in body


def test_multiple_symbols_handled():
    prev = {
        "A": {"action": "HOLD", "score": 5.0, "conviction": "medium"},
        "B": {"action": "BUY", "score": 8.0, "conviction": "high"},
    }
    out = scd.detect_changes(
        prev,
        [
            _sig("A", "BUY", 8.0, "high"),   # material flip
            _sig("B", "BUY", 8.1, "high"),   # no change
            _sig("C", "SELL", 2.0, "high"),  # new strong-action symbol
        ],
        _TS,
    )
    syms = {c.symbol for c in out}
    assert syms == {"A", "C"}
