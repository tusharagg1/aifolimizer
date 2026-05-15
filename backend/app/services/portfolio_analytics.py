"""Portfolio-level analytics: x-ray exposures, tax-loss candidates, sector/asset concentration.

Adapted from ai-portfolio-analyzer/analytics.py — uses our Position/PortfolioResponse models.
"""
from __future__ import annotations

from typing import Any

from app.models.portfolio import Position, PortfolioResponse


# ETF underlying exposures (curated; based on iShares/Vanguard published holdings)
ETF_EXPOSURE_MAP: dict[str, dict[str, float]] = {
    "XEQT": {"US Equity": 0.45, "Canada Equity": 0.25, "International Equity": 0.30},
    "VEQT": {"US Equity": 0.45, "Canada Equity": 0.30, "International Equity": 0.25},
    "VFV":  {"US Large Cap": 1.0},
    "XUS":  {"US Large Cap": 1.0},
    "SPY":  {"US Large Cap": 1.0},
    "VOO":  {"US Large Cap": 1.0},
    "VTI":  {"US Total Market": 1.0},
    "VT":   {"Global Equity": 1.0},
    "VXUS": {"International Equity": 1.0},
    "QQQ":  {"US Growth/Technology": 1.0},
    "XQQ":  {"US Growth/Technology": 1.0},
    "BND":  {"US Bonds": 1.0},
    "XBB":  {"Canada Bonds": 1.0},
    "ZAG":  {"Canada Bonds": 1.0},
    "VAB":  {"Canada Bonds": 1.0},
    "VCN":  {"Canada Equity": 1.0},
    "XIC":  {"Canada Equity": 1.0},
    "VIU":  {"International Developed": 1.0},
    "XEF":  {"International Developed": 1.0},
    "VEE":  {"Emerging Markets": 1.0},
    "XEC":  {"Emerging Markets": 1.0},
}


def xray_exposures(portfolio: PortfolioResponse) -> dict[str, float]:
    """Map ETF holdings to their underlying exposure. Direct stocks counted by sector."""
    total = portfolio.summary.total_value
    if total <= 0:
        return {}

    exposures: dict[str, float] = {}
    for pos in portfolio.positions:
        weight = pos.market_value / total
        mapped = ETF_EXPOSURE_MAP.get(pos.symbol.upper())
        if mapped:
            for label, inner_weight in mapped.items():
                exposures[label] = exposures.get(label, 0.0) + weight * inner_weight
        else:
            label = pos.sector if pos.sector and pos.sector != "Unknown" else pos.asset_class
            exposures[label] = exposures.get(label, 0.0) + weight

    return dict(sorted(exposures.items(), key=lambda item: item[1], reverse=True))


def sector_concentration(portfolio: PortfolioResponse) -> dict[str, float]:
    """Sector-only breakdown (no ETF expansion). Useful as concentration heatmap."""
    total = portfolio.summary.total_value
    if total <= 0:
        return {}
    out: dict[str, float] = {}
    for pos in portfolio.positions:
        label = pos.sector or pos.asset_class or "Unknown"
        out[label] = out.get(label, 0.0) + (pos.market_value_cad / total)
    return dict(sorted(out.items(), key=lambda item: item[1], reverse=True))


def asset_class_breakdown(portfolio: PortfolioResponse) -> dict[str, float]:
    total = portfolio.summary.total_value
    if total <= 0:
        return {}
    out: dict[str, float] = {}
    for pos in portfolio.positions:
        label = pos.asset_class or "Unknown"
        out[label] = out.get(label, 0.0) + (pos.market_value_cad / total)
    return dict(sorted(out.items(), key=lambda item: item[1], reverse=True))


def tax_loss_candidates(portfolio: PortfolioResponse, threshold_pct: float = -5.0) -> list[dict[str, Any]]:
    """Positions sitting below threshold % return. Flags for tax-loss harvesting review."""
    candidates: list[dict[str, Any]] = []
    for pos in portfolio.positions:
        if pos.total_return_pct < threshold_pct and pos.market_value > 0:
            unrealized_loss_value = pos.market_value - pos.book_cost
            candidates.append({
                "symbol": pos.symbol,
                "name": pos.name,
                "unrealized_loss": round(unrealized_loss_value, 2),
                "unrealized_loss_pct": round(pos.total_return_pct, 2),
                "market_value": round(pos.market_value, 2),
                "note": "Potential tax-loss review. In Canada, check superficial-loss rules (30-day window) and account type (TFSA/RRSP losses are not deductible) before acting.",
            })
    return sorted(candidates, key=lambda item: item["unrealized_loss_pct"])


def concentration_warnings(portfolio: PortfolioResponse, single_position_max_pct: float = 10.0, sector_max_pct: float = 35.0) -> list[dict[str, Any]]:
    """Flag any single position > X% of portfolio, or any sector > Y%."""
    warnings: list[dict[str, Any]] = []
    total = portfolio.summary.total_value
    if total <= 0:
        return warnings

    for pos in portfolio.positions:
        weight_pct = (pos.market_value_cad / total) * 100
        if weight_pct > single_position_max_pct:
            warnings.append({
                "type": "single_position",
                "symbol": pos.symbol,
                "weight_pct": round(weight_pct, 2),
                "threshold_pct": single_position_max_pct,
                "note": f"{pos.symbol} is {weight_pct:.1f}% of portfolio (threshold {single_position_max_pct}%)",
            })

    sectors = sector_concentration(portfolio)
    for sector, weight in sectors.items():
        weight_pct = weight * 100
        if weight_pct > sector_max_pct:
            warnings.append({
                "type": "sector",
                "sector": sector,
                "weight_pct": round(weight_pct, 2),
                "threshold_pct": sector_max_pct,
                "note": f"Sector '{sector}' is {weight_pct:.1f}% of portfolio (threshold {sector_max_pct}%)",
            })

    return warnings
