_ACCOUNT_TYPE_LABELS = {
    "TFSA": "Tax-Free Savings Account",
    "RRSP": "Registered Retirement Account",
    "RESP": "Registered Education Account",
    "Non-Reg": "Non-Registered Investment Account",
    "Crypto": "Crypto Account",
    "LIRA": "Locked-In Retirement Account",
}


def filter_portfolio(portfolio: dict) -> dict:
    """
    Removes all PII from a portfolio dict before sending to Claude.
    Input: PortfolioResponse.model_dump()
    Output: safe dict with no account IDs, names, emails, or WS internal IDs.
    """
    safe = {
        "positions": [_filter_position(p) for p in portfolio.get("positions", [])],
        "summary": _filter_summary(portfolio.get("summary", {})),
    }
    return safe


def filter_user_context(context: dict) -> dict:
    """
    Strips PII from UserContext before injecting into prompts.
    Pseudonymizes account types; keeps only financial figures.
    """
    safe_accounts = []
    for acc in context.get("accounts", []):
        acc_type = acc.get("type", "")
        safe_accounts.append(
            {
                "label": _ACCOUNT_TYPE_LABELS.get(acc_type, "Investment Account"),
                "currency": acc.get("currency", "CAD"),
                "cash_balance": acc.get("cash_balance", 0),
                "invested_value": acc.get("invested_value", 0),
            }
        )

    return {
        "accounts": safe_accounts,
        "total_cash": context.get("total_cash", 0),
        "total_invested": context.get("total_invested", 0),
        "account_types": [_ACCOUNT_TYPE_LABELS.get(t, "Investment Account") for t in context.get("account_types", [])],
    }


def _filter_position(pos: dict) -> dict:
    return {
        "symbol": pos.get("symbol", ""),
        "name": pos.get("name", ""),
        "quantity": pos.get("quantity", 0),
        "book_cost": pos.get("book_cost", 0),
        "market_value": pos.get("market_value", 0),
        "day_change_pct": pos.get("day_change_pct", 0),
        "total_return_pct": pos.get("total_return_pct", 0),
        "weight": pos.get("weight", 0),
        "asset_class": pos.get("asset_class", ""),
        "sector": pos.get("sector"),
    }


def _filter_summary(summary: dict) -> dict:
    return {
        "total_value": summary.get("total_value", 0),
        "total_cost": summary.get("total_cost", 0),
        "total_return_pct": summary.get("total_return_pct", 0),
        "cash_available": summary.get("cash_available", 0),
    }


import re

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _scrub_emails(value):
    if isinstance(value, str):
        return _EMAIL_RE.sub("[REDACTED]", value)
    if isinstance(value, list):
        return [_scrub_emails(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_emails(v) for k, v in value.items()}
    return value


def filter_personal_context_full(env: dict) -> dict:
    """Local-session view of personal context. Scrubs only email-like strings.

    Used by the MCP tool returning personal context to the local Claude session.
    Numeric fields (salary, expenses, room) are kept raw - they stay on machine.
    """
    if not env.get("present"):
        return env
    out = dict(env)
    if "context" in out:
        out["context"] = _scrub_emails(out["context"])
    return out


def _salary_band(cad):
    if cad is None:
        return None
    if cad < 30_000:
        return "<30k"
    if cad < 60_000:
        return "30-60k"
    if cad < 90_000:
        return "60-90k"
    if cad < 120_000:
        return "90-120k"
    if cad < 180_000:
        return "120-180k"
    if cad < 250_000:
        return "180-250k"
    return ">250k"


def _age_band(age):
    if age is None:
        return None
    if age < 25:
        return "<25"
    if age < 30:
        return "25-30"
    if age < 35:
        return "30-35"
    if age < 40:
        return "35-40"
    if age < 50:
        return "40-50"
    if age < 65:
        return "50-65"
    return ">65"


def _expenses_band(cad):
    if cad is None:
        return None
    if cad < 1_500:
        return "<1.5k"
    if cad < 3_000:
        return "1.5-3k"
    if cad < 5_000:
        return "3-5k"
    if cad < 8_000:
        return "5-8k"
    return ">8k"


def _room_band(cad):
    if cad is None:
        return None
    if cad < 5_000:
        return "<5k"
    if cad < 25_000:
        return "5-25k"
    if cad < 75_000:
        return "25-75k"
    return ">75k"


_DROPPED_FIELDS = {
    "espp_employer_label",
    "iou_receivable_cad",
    "institutions",
    "external_crypto",
    "debts",
}

_BANDED_FIELDS = {
    "gross_salary_cad": ("salary_band", _salary_band),
    "bonus_target_cad": ("bonus_band", _salary_band),
    "monthly_expenses_cad": ("expenses_band", _expenses_band),
    "monthly_savings_target_cad": ("savings_band", _expenses_band),
    "external_cash_cad": ("external_cash_band", _room_band),
    "room_rrsp_cad": ("room_rrsp_band", _room_band),
    "room_tfsa_cad": ("room_tfsa_band", _room_band),
    "room_fhsa_cad": ("room_fhsa_band", _room_band),
    "age": ("age_band", _age_band),
}


def filter_personal_context_external(env: dict) -> dict:
    """External-LLM view of personal context. Bands raw dollars + age, drops
    counterparty / employer / institution labels, replaces dropped lists with
    has_X boolean flags. Used by free-tier LLM fallbacks (GitHub Models, Gemini,
    OpenRouter, Qwen) so prompts never carry absolute dollars or labeled IDs.
    """
    if not env.get("present"):
        return env
    src_ctx = env.get("context", {})
    out_ctx = {}
    for k, v in src_ctx.items():
        if k in _DROPPED_FIELDS:
            if v not in (None, "", [], {}):
                out_ctx[f"has_{k}"] = True
            continue
        if k in _BANDED_FIELDS:
            band_name, band_fn = _BANDED_FIELDS[k]
            band = band_fn(v)
            if band is not None:
                out_ctx[band_name] = band
            continue
        out_ctx[k] = v
    out = dict(env)
    out["context"] = out_ctx
    return out
