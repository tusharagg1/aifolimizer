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
