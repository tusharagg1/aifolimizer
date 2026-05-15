from app.models.portfolio import PortfolioResponse


def compute_health_score(portfolio: PortfolioResponse) -> dict:
    positions = portfolio.positions
    summary = portfolio.summary

    if not positions:
        return {
            "score": 0, "grade": "N/A",
            "verdict": "No positions found",
            "breakdown": {}, "inputs": {},
        }

    # 1. Diversification (0-30): more holdings = better, max at 15
    n = len(positions)
    div_score = min(30, n * 2)

    # 2. Concentration (0-30): penalize large single-position weight
    max_weight = max(p.weight for p in positions)
    if max_weight <= 10:
        conc_score = 30
    elif max_weight <= 20:
        conc_score = 20
    elif max_weight <= 30:
        conc_score = 10
    else:
        conc_score = 5

    # 3. Return (0-20): positive total return rewards investor
    ret = summary.total_return_pct
    if ret >= 20:
        ret_score = 20
    elif ret >= 10:
        ret_score = 16
    elif ret >= 0:
        ret_score = 12
    elif ret >= -10:
        ret_score = 7
    else:
        ret_score = 3

    # 4. Cash efficiency (0-10): low idle cash drag for growth investor
    total = summary.total_value
    cash = summary.cash_available
    cash_pct = (cash / total * 100) if total > 0 else 0
    if cash_pct < 5:
        cash_score = 10
    elif cash_pct < 15:
        cash_score = 7
    elif cash_pct < 30:
        cash_score = 4
    else:
        cash_score = 1

    # 5. Asset class diversity (0-10): spread across multiple classes
    asset_classes = {p.asset_class for p in positions}
    asset_score = min(10, len(asset_classes) * 3)

    total_score = div_score + conc_score + ret_score + cash_score + asset_score

    if total_score >= 80:
        grade = "A"
    elif total_score >= 65:
        grade = "B"
    elif total_score >= 50:
        grade = "C"
    elif total_score >= 35:
        grade = "D"
    else:
        grade = "F"

    verdicts = {
        "A": "Excellent portfolio composition",
        "B": "Good — minor improvements possible",
        "C": "Fair — review concentration and diversification",
        "D": "Needs attention — significant imbalances present",
        "F": "Critical issues — rebalancing recommended",
    }

    return {
        "score": total_score,
        "grade": grade,
        "verdict": verdicts[grade],
        "breakdown": {
            "diversification": div_score,
            "concentration": conc_score,
            "performance": ret_score,
            "cash_efficiency": cash_score,
            "asset_class_diversity": asset_score,
        },
        "inputs": {
            "position_count": n,
            "max_single_weight_pct": round(max_weight, 1),
            "total_return_pct": round(ret, 2),
            "cash_pct": round(cash_pct, 1),
            "asset_classes": sorted(asset_classes),
        },
    }
