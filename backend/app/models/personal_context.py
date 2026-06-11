"""User personal-finance context model.

Separate from the WS-account `UserContext` in portfolio.py - that one holds
live brokerage data; this one holds life context (salary, age, room,
expenses, ESPP, etc.) that a user enters once and reuses across skills.

All fields optional. App must degrade gracefully when absent.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
INSURANCE_TIERS = {"basic", "middle", "comprehensive"}
RISK_TIERS = {"conservative", "moderate", "aggressive"}
SCHEMA_VERSION = "1.0"


class Debt(BaseModel):
    type: str
    balance_cad: float = Field(ge=0)
    rate_pct: Optional[float] = Field(default=None, ge=0, le=100)


class ExternalCryptoLot(BaseModel):
    symbol: str
    qty: float = Field(ge=0)
    cost_basis_cad: float = Field(ge=0)
    platform_label: Optional[str] = None


class PersonalContext(BaseModel):
    age: Optional[int] = Field(default=None, ge=14, le=120)
    province: Optional[str] = None

    gross_salary_cad: Optional[float] = Field(default=None, ge=0)
    employer_match_pct: Optional[float] = Field(default=None, ge=0, le=100)
    bonus_target_cad: Optional[float] = Field(default=None, ge=0)

    monthly_expenses_cad: Optional[float] = Field(default=None, ge=0)
    monthly_savings_target_cad: Optional[float] = Field(default=None, ge=0)

    fthb_yes: Optional[bool] = None
    home_horizon_years: Optional[float] = Field(default=None, ge=0, le=50)
    mortgage_planned: Optional[bool] = None
    dependents_count: Optional[int] = Field(default=None, ge=0)
    spouse_present: Optional[bool] = None

    room_rrsp_cad: Optional[float] = Field(default=None, ge=0)
    room_tfsa_cad: Optional[float] = Field(default=None, ge=0)
    room_fhsa_cad: Optional[float] = Field(default=None, ge=0)

    risk_tier_per_account: Optional[dict[str, str]] = None

    espp_employer_label: Optional[str] = None
    espp_discount_pct: Optional[float] = Field(default=None, ge=0, le=100)
    espp_cycle_months: Optional[int] = Field(default=None, ge=1, le=24)
    espp_monthly_contrib_pct: Optional[float] = Field(default=None, ge=0, le=100)
    espp_qualifying_hold_months: Optional[int] = Field(default=None, ge=0, le=120)

    iou_receivable_cad: Optional[float] = Field(default=None, ge=0)
    debts: Optional[list[Debt]] = None

    institutions: Optional[list[str]] = None
    external_cash_cad: Optional[float] = Field(default=None, ge=0)

    insurance_tier: Optional[Literal["basic", "middle", "comprehensive"]] = None

    external_crypto: Optional[list[ExternalCryptoLot]] = None

    updated_utc: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    @field_validator("province")
    @classmethod
    def _province_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.upper()
        if v not in PROVINCES:
            raise ValueError(f"Unknown province: {v}. Use one of {sorted(PROVINCES)}.")
        return v

    @field_validator("risk_tier_per_account")
    @classmethod
    def _risk_tier_valid(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if v is None:
            return None
        for acct, tier in v.items():
            if tier not in RISK_TIERS:
                raise ValueError(f"Risk tier for {acct} must be one of {sorted(RISK_TIERS)}, got {tier!r}")
        return v
