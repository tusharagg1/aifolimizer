"""Tests for personal-context store + PII filters + self-learning hash.

Run from backend/:
  .venv\\Scripts\\python -m pytest tests/test_personal_context.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.personal_context import PersonalContext
from app.services import personal_context as pc
from app.services.pii_filter import (
    filter_personal_context_external,
    filter_personal_context_full,
)


@pytest.fixture(autouse=True)
def _isolate_context_file(tmp_path, monkeypatch):
    """Redirect the on-disk context file to a tmp path per test."""
    fake = tmp_path / "personal_context.json"
    monkeypatch.setattr(pc, "_CONTEXT_FILE", fake)
    yield fake


# ─── Round-trip + persistence ───────────────────────────────────────────────


def test_load_returns_none_when_file_missing(_isolate_context_file):
    assert pc.load() is None


def test_envelope_returns_present_false_when_missing(_isolate_context_file):
    env = pc.envelope()
    assert env["present"] is False
    assert env["context_hash"] == "absent"


def test_save_then_load_round_trip(_isolate_context_file):
    ctx = PersonalContext(age=33, province="ON", gross_salary_cad=89_000)
    pc.save(ctx)
    loaded = pc.load()
    assert loaded is not None
    assert loaded.age == 33
    assert loaded.province == "ON"
    assert loaded.gross_salary_cad == 89_000
    assert loaded.updated_utc is not None


def test_update_field_merges_existing(_isolate_context_file):
    pc.save(PersonalContext(age=33, province="ON"))
    pc.update_field("gross_salary_cad", 89_000)
    loaded = pc.load()
    assert loaded.age == 33
    assert loaded.province == "ON"
    assert loaded.gross_salary_cad == 89_000


def test_update_field_rejects_unknown(_isolate_context_file):
    with pytest.raises(ValueError, match="Unknown field"):
        pc.update_field("not_a_field", 42)


def test_clear_deletes_file(_isolate_context_file):
    pc.save(PersonalContext(age=33))
    assert pc.clear() is True
    assert pc.load() is None
    assert pc.clear() is False  # idempotent


# ─── Validation ─────────────────────────────────────────────────────────────


def test_invalid_province_rejected():
    with pytest.raises(Exception):
        PersonalContext(province="XX")


def test_negative_balance_rejected():
    with pytest.raises(Exception):
        PersonalContext(room_tfsa_cad=-1)


def test_invalid_risk_tier_rejected():
    with pytest.raises(Exception):
        PersonalContext(risk_tier_per_account={"TFSA": "yolo"})


def test_age_bounds():
    with pytest.raises(Exception):
        PersonalContext(age=10)
    with pytest.raises(Exception):
        PersonalContext(age=130)


# ─── Derived helpers ────────────────────────────────────────────────────────


def test_marginal_rate_ON_mid_bracket():
    rate = pc.marginal_tax_rate_pct(89_000, "ON")
    assert rate is not None
    assert 25 < rate < 35


def test_marginal_rate_handles_missing():
    assert pc.marginal_tax_rate_pct(None, "ON") is None
    assert pc.marginal_tax_rate_pct(89_000, None) is None
    assert pc.marginal_tax_rate_pct(89_000, "FAKE") is None


def test_emergency_fund_target_with_dependents():
    assert pc.emergency_fund_target_cad(2_000, 0) == 6_000
    assert pc.emergency_fund_target_cad(2_000, 2) == 12_000
    assert pc.emergency_fund_target_cad(None, 0) is None


def test_fhsa_priority_first_logic():
    assert pc.fhsa_priority_first(True, 3) is True
    assert pc.fhsa_priority_first(True, 6) is False
    assert pc.fhsa_priority_first(False, 3) is False
    assert pc.fhsa_priority_first(None, None) is False


def test_account_waterfall_fhsa_first():
    order = pc.account_waterfall(8_000, 30_000, 50_000, fhsa_first=True)
    assert order.index("FHSA") < order.index("RRSP")
    assert order[-1] == "Non-Reg"


def test_account_waterfall_default_order():
    order = pc.account_waterfall(8_000, 30_000, 50_000, fhsa_first=False)
    assert order.index("RRSP") < order.index("TFSA")
    assert order.index("TFSA") < order.index("FHSA")


def test_account_waterfall_skips_zero_room():
    order = pc.account_waterfall(0, 30_000, 0, fhsa_first=False)
    assert "FHSA" not in order
    assert "TFSA" not in order
    assert "RRSP" in order


# ─── context_hash + self-learning gating ────────────────────────────────────


def test_context_hash_deterministic_for_same_inputs(_isolate_context_file):
    ctx1 = PersonalContext(age=33, province="ON", gross_salary_cad=89_000)
    ctx2 = PersonalContext(age=33, province="ON", gross_salary_cad=89_000)
    assert pc.context_hash(ctx1) == pc.context_hash(ctx2)


def test_context_hash_changes_on_field_change(_isolate_context_file):
    ctx_a = PersonalContext(age=33, fthb_yes=True, home_horizon_years=2)
    ctx_b = PersonalContext(age=33, fthb_yes=True, home_horizon_years=10)
    assert pc.context_hash(ctx_a) != pc.context_hash(ctx_b)


def test_context_hash_absent_when_none():
    assert pc.context_hash(None) == "absent"


def test_hash_for_skill_returns_none_for_independent_skills(_isolate_context_file):
    pc.save(PersonalContext(age=33, province="ON"))
    assert pc.hash_for_skill("stock-analysis") is None
    assert pc.hash_for_skill("momentum-scanner") is None
    assert pc.hash_for_skill("adversarial-research") is None


def test_hash_for_skill_returns_hash_for_personal_skills(_isolate_context_file):
    pc.save(PersonalContext(age=33, province="ON"))
    assert pc.hash_for_skill("cash-deployment") is not None
    assert pc.hash_for_skill("tax-loss-review") is not None
    assert pc.hash_for_skill("portfolio-health") is not None


def test_is_personal_skill():
    assert pc.is_personal_skill("cash-deployment") is True
    assert pc.is_personal_skill("stock-analysis") is False
    assert pc.is_personal_skill("") is False
    assert pc.is_personal_skill("nonexistent-skill") is False


# ─── PII filter ─────────────────────────────────────────────────────────────


def test_filter_full_returns_present_false_passthrough():
    env = {"present": False, "context_hash": "absent"}
    assert filter_personal_context_full(env) == env


def test_filter_full_scrubs_email_in_string_field():
    env = {
        "present": True,
        "context": {"espp_employer_label": "user@example.com"},
        "derived": {},
        "context_hash": "abc",
    }
    out = filter_personal_context_full(env)
    assert "@" not in out["context"]["espp_employer_label"]


def test_filter_external_bands_salary():
    env = {
        "present": True,
        "context": {"gross_salary_cad": 89_000, "age": 33},
        "derived": {},
        "context_hash": "abc",
    }
    out = filter_personal_context_external(env)
    assert "gross_salary_cad" not in out["context"]
    assert out["context"]["salary_band"] == "60-90k"
    assert "age" not in out["context"]
    assert out["context"]["age_band"] == "30-35"


def test_filter_external_drops_employer_label_and_iou():
    env = {
        "present": True,
        "context": {
            "espp_employer_label": "EmployerA",
            "iou_receivable_cad": 30_000,
            "institutions": ["BankA"],
        },
        "derived": {},
        "context_hash": "abc",
    }
    out = filter_personal_context_external(env)
    ctx = out["context"]
    assert "espp_employer_label" not in ctx
    assert "iou_receivable_cad" not in ctx
    assert "institutions" not in ctx
    assert ctx.get("has_iou_receivable_cad") is True
    assert ctx.get("has_institutions") is True


# ─── File security ──────────────────────────────────────────────────────────


def test_file_written_atomically_with_indent(_isolate_context_file):
    pc.save(PersonalContext(age=33, province="ON"))
    raw = Path(_isolate_context_file).read_text()
    parsed = json.loads(raw)
    assert parsed["age"] == 33
    assert parsed["schema_version"] == "1.0"
