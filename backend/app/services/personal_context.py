"""Personal-finance context store: load/save/derive helpers.

Stored at ~/.aifolimizer/personal_context.json (mode 0o600, atomic write).
All fields optional; missing file returns {present: false} skeleton.

Derived block (marginal tax rate, emergency-fund target, account waterfall)
is computed here so skill prompts don't duplicate the math.

Self-learning hook: `context_hash()` returns a deterministic SHA256 of the
canonicalized profile so log_recommendation / log_trade_decision can stratify
historical track-record by life-stage (FTHB-horizon, salary band, age band).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app.models.personal_context import SCHEMA_VERSION, PersonalContext

_CONTEXT_FILE = Path.home() / ".aifolimizer" / "personal_context.json"


# Skills that produce advice on the user's actual portfolio — their
# track record should be segmented by life-stage hash so self-learning
# (weights tuner, threshold calibration) can stratify by personal context.
_PERSONAL_SKILLS = {
    "cash-deployment",
    "tax-loss-review",
    "dividend-strategy",
    "portfolio-health",
    "risk-assessment",
    "daily-briefing",
    "pre-trade-check",
    "weekly-mirror",
    "auto-rebalance",
    "position-review",
    "earnings-postmortem",
    "earnings-analyzer",
    "macro-impact",
    "sector-rotation",
    "profile-setup",
}


def is_personal_skill(skill_name: str) -> bool:
    return (skill_name or "").strip() in _PERSONAL_SKILLS


def hash_for_skill(skill_name: str) -> str | None:
    """Return personal_context_hash IFF skill is a personal-portfolio skill.

    Independent skills (stock-analysis, momentum-scanner, adversarial-research,
    etc.) get None to keep their track records bias-free.
    """
    if not is_personal_skill(skill_name):
        return None
    return context_hash(load())


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Mirror of wealthsimple._atomic_write_json. Local copy to avoid import cycle."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load() -> PersonalContext | None:
    """Load context from disk. Returns None when no file exists."""
    if not _CONTEXT_FILE.exists():
        return None
    try:
        payload = json.loads(_CONTEXT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return PersonalContext.model_validate(payload)


def save(ctx: PersonalContext) -> None:
    """Validate via Pydantic and atomically write to disk."""
    payload = ctx.model_copy(update={"updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _atomic_write_json(_CONTEXT_FILE, payload.model_dump(exclude_none=False))


def update_field(field: str, value: Any) -> PersonalContext:
    """Set one field, validate, write. Idempotent."""
    existing = load() or PersonalContext()
    if field not in PersonalContext.model_fields:
        raise ValueError(f"Unknown field: {field}")
    new_ctx = existing.model_copy(update={field: value})
    PersonalContext.model_validate(new_ctx.model_dump())
    save(new_ctx)
    return new_ctx


def clear() -> bool:
    if _CONTEXT_FILE.exists():
        _CONTEXT_FILE.unlink()
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Derived helpers — computed once, cached on the dict before returning.
# ─────────────────────────────────────────────────────────────────────────────

# 2026 federal + provincial combined marginal rates (approximate, top-of-bracket
# at common income levels). Update annually. Source: published CRA + provincial
# bracket tables. Used as guidance only — not a substitute for a tax pro.
_MARGINAL_RATE_TABLE: dict[str, list[tuple[float, float]]] = {
    "ON": [
        (55_867, 0.2005),
        (90_000, 0.2965),
        (111_733, 0.3148),
        (150_000, 0.4341),
        (220_000, 0.4641),
        (float("inf"), 0.5353),
    ],
    "BC": [
        (47_937, 0.2050),
        (95_875, 0.2870),
        (110_076, 0.3850),
        (150_000, 0.4070),
        (240_000, 0.4750),
        (float("inf"), 0.5350),
    ],
    "AB": [(55_867, 0.2500), (148_269, 0.3050), (216_511, 0.3600), (322_171, 0.4200), (float("inf"), 0.4800)],
    "QC": [(51_780, 0.2653), (103_545, 0.3712), (126_000, 0.4146), (246_752, 0.4571), (float("inf"), 0.5375)],
    "MB": [(47_000, 0.2580), (100_000, 0.3375), (150_000, 0.4340), (float("inf"), 0.5040)],
    "SK": [(52_057, 0.2550), (148_734, 0.3300), (float("inf"), 0.4750)],
    "NS": [(29_590, 0.2479), (59_180, 0.3000), (93_000, 0.3500), (150_000, 0.3800), (float("inf"), 0.5400)],
    "NB": [(49_958, 0.2468), (99_916, 0.2982), (185_064, 0.3852), (float("inf"), 0.5300)],
    "NL": [
        (43_198, 0.2300),
        (86_395, 0.2950),
        (154_244, 0.3500),
        (215_943, 0.4080),
        (275_870, 0.4350),
        (550_000, 0.4730),
        (float("inf"), 0.5480),
    ],
    "PE": [(32_656, 0.2480), (64_313, 0.2780), (105_000, 0.3870), (140_000, 0.4737), (float("inf"), 0.5137)],
    "YT": [
        (55_867, 0.2400),
        (111_733, 0.2950),
        (173_205, 0.3640),
        (246_752, 0.4180),
        (500_000, 0.4750),
        (float("inf"), 0.4800),
    ],
    "NT": [(50_597, 0.2090), (101_198, 0.2330), (164_525, 0.3170), (float("inf"), 0.4705)],
    "NU": [(53_268, 0.1900), (106_537, 0.2900), (173_205, 0.3300), (float("inf"), 0.4150)],
}


def marginal_tax_rate_pct(salary_cad: float | None, province: str | None) -> float | None:
    if salary_cad is None or province is None:
        return None
    table = _MARGINAL_RATE_TABLE.get(province)
    if not table:
        return None
    for top, rate in table:
        if salary_cad <= top:
            return round(rate * 100, 2)
    return None


def emergency_fund_target_cad(monthly_expenses: float | None, dependents: int | None) -> float | None:
    if monthly_expenses is None:
        return None
    months = 6 if (dependents or 0) > 0 else 3
    return round(monthly_expenses * months, 2)


def fhsa_priority_first(fthb_yes: bool | None, home_horizon_years: float | None) -> bool:
    return bool(fthb_yes) and (home_horizon_years is not None and home_horizon_years <= 5)


def account_waterfall(
    room_fhsa: float | None,
    room_rrsp: float | None,
    room_tfsa: float | None,
    fhsa_first: bool,
) -> list[str]:
    """Ordered list of account types to fund next, given remaining room.

    FHSA is prioritized only when the user is FTHB with horizon ≤5y. Otherwise
    follows the standard heuristic for a salaried Canadian: RRSP for tax
    deduction, TFSA for tax-free growth, FHSA last (or skipped if not FTHB),
    Non-Reg as overflow.
    """
    order: list[str] = []
    if fhsa_first and (room_fhsa or 0) > 0:
        order.append("FHSA")
    if (room_rrsp or 0) > 0:
        order.append("RRSP")
    if (room_tfsa or 0) > 0:
        order.append("TFSA")
    if not fhsa_first and (room_fhsa or 0) > 0:
        order.append("FHSA")
    order.append("Non-Reg")
    return order


def derive(ctx: PersonalContext) -> dict:
    fhsa_first = fhsa_priority_first(ctx.fthb_yes, ctx.home_horizon_years)
    return {
        "marginal_tax_rate_pct": marginal_tax_rate_pct(ctx.gross_salary_cad, ctx.province),
        "emergency_fund_target_cad": emergency_fund_target_cad(ctx.monthly_expenses_cad, ctx.dependents_count),
        "fhsa_priority_first": fhsa_first,
        "account_waterfall": account_waterfall(ctx.room_fhsa_cad, ctx.room_rrsp_cad, ctx.room_tfsa_cad, fhsa_first),
        "schema_version": SCHEMA_VERSION,
    }


def context_hash(ctx: PersonalContext | None) -> str:
    """Deterministic SHA256 over a canonicalized subset of fields.

    Used to stratify historical recommendation track-records by life stage:
    when the user's situation changes (FTHB horizon shrinks, salary band
    changes, dependents added), the hash flips and self-tuning code can
    reweight learning toward post-change samples.
    """
    if ctx is None:
        return "absent"
    keys = [
        "age",
        "province",
        "gross_salary_cad",
        "monthly_expenses_cad",
        "fthb_yes",
        "home_horizon_years",
        "dependents_count",
        "spouse_present",
        "room_rrsp_cad",
        "room_tfsa_cad",
        "room_fhsa_cad",
    ]
    payload = {k: getattr(ctx, k) for k in keys}
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def envelope() -> dict:
    """Top-level shape returned by the MCP tool. Always include `present` flag."""
    ctx = load()
    if ctx is None:
        return {"present": False, "schema_version": SCHEMA_VERSION, "context_hash": "absent"}
    return {
        "present": True,
        "context": ctx.model_dump(exclude_none=True),
        "derived": derive(ctx),
        "context_hash": context_hash(ctx),
    }
