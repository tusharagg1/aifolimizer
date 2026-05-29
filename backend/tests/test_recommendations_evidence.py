"""Offline tests for recommendation evidence tiering (pure function).

No network, no DB. Verifies the proven_edge / reasonable_thesis /
experimental / no_edge gating that keeps unproven signals from being
presented as realized edge.
"""

from app.services import recommendations as rec


def test_no_edge_action():
    tier, _ = rec._evidence_tier("NO_EDGE", "low", 500, True, 100.0)
    assert tier == "no_edge"


def test_experimental_when_low_forward_sample():
    tier, note = rec._evidence_tier("BUY", "high", 2, False, 50.0)
    assert tier == "experimental"
    assert "2 closed" in note


def test_reasonable_thesis_with_enough_forward_but_uncalibrated():
    tier, _ = rec._evidence_tier("BUY", "high", 60, False, 50.0)
    assert tier == "reasonable_thesis"
    tier2, _ = rec._evidence_tier("ADD", "medium", 40, False, None)
    assert tier2 == "reasonable_thesis"


def test_proven_edge_requires_all_gates():
    assert rec._evidence_tier("BUY", "high", 150, True, 25.0)[0] == "proven_edge"
    # calibrated + high sample but negative EV → not proven
    assert rec._evidence_tier("BUY", "high", 150, True, -5.0)[0] == "reasonable_thesis"
    # high sample + high conf but uncalibrated → not proven
    assert rec._evidence_tier("BUY", "high", 150, False, 25.0)[0] == "reasonable_thesis"
    # medium confidence never proven even if calibrated + sample
    assert rec._evidence_tier("BUY", "medium", 150, True, 25.0)[0] == "reasonable_thesis"


def test_low_confidence_below_thesis_threshold_is_experimental():
    assert rec._evidence_tier("WATCH", "low", 60, False, None)[0] == "experimental"
