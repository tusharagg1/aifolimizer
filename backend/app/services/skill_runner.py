"""Skill runner - background-executable Python implementations of analysis skills.

Per-skill pure-Python service functions that operate on a PortfolioResponse and
return a SkillSnapshot. These are executed by the scheduler (`app/jobs/scheduler.py`)
and cached to disk so the frontend can read pre-computed skill outputs.

LLM-required skills (adversarial-research, earnings-postmortem, stock-compare)
are NOT codified here - they remain on-demand via Claude.

Each runner emits four sections:
  summary:     headline metrics for the panel
  actionable:  concrete items the user can act on
  alerts:      warnings sorted by urgency
  key_insights: top 3 takeaways for the daily briefing composer
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.models.portfolio import PortfolioResponse
from app.services import portfolio_analytics
from app.services import health_score
from app.services import macro as macro_svc
from app.services import fundamentals as fund_svc
from app.services import recommendations as rec_svc
from app.services import data_router
from app.services import quant


_SNAP_DIR = Path(__file__).resolve().parents[2] / ".cache" / "skill_snapshots"
_SNAP_DIR.mkdir(parents=True, exist_ok=True)

# Active tenant for the current run - set by run_all_skills so composite
# runners (daily-briefing) read the correct namespace.
_active_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_tenant",
    default=None,
)

LLM_ONLY_SKILLS = frozenset(
    {
        "adversarial-research",
        "earnings-postmortem",
        "stock-compare",
    }
)

_DEFAULT_TTLS = {
    "portfolio-health": 30,
    "risk-assessment": 60,
    "macro-impact": 60,
    "dividend-strategy": 360,
    "sector-rotation": 60,
    "tax-loss-review": 360,
    "cash-deployment": 30,
    "daily-briefing": 15,
    "stock-analysis": 15,
    "earnings-analyzer": 360,
}

# Sector regime sensitivity - rough sign of correlation to each regime.
# +1 favored, -1 disfavored, 0 neutral. Used by macro-impact to flag
# sector exposure that fights the current regime.
_SECTOR_REGIME_BIAS: dict[str, dict[str, int]] = {
    "Technology": {"bull_low_fear": 1, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": -1},
    "Financials": {"bull_low_fear": 1, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": -1},
    "Healthcare": {"bull_low_fear": 0, "bull_high_fear": 1, "bear_low_fear": 1, "bear_high_fear": 0},
    "Consumer Defensive": {"bull_low_fear": -1, "bull_high_fear": 1, "bear_low_fear": 1, "bear_high_fear": 1},
    "Consumer Cyclical": {"bull_low_fear": 1, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": -1},
    "Energy": {"bull_low_fear": 0, "bull_high_fear": 0, "bear_low_fear": 0, "bear_high_fear": 0},
    "Utilities": {"bull_low_fear": -1, "bull_high_fear": 1, "bear_low_fear": 1, "bear_high_fear": 1},
    "Real Estate": {"bull_low_fear": 0, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": -1},
    "Industrials": {"bull_low_fear": 1, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": -1},
    "Communication Services": {"bull_low_fear": 1, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": 0},
    "Basic Materials": {"bull_low_fear": 1, "bull_high_fear": 0, "bear_low_fear": -1, "bear_high_fear": -1},
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _snapshot(
    skill: str,
    *,
    summary: dict | None = None,
    actionable: list | None = None,
    alerts: list | None = None,
    key_insights: list | None = None,
    status: str = "ok",
    confidence_source: str = "experimental",
    error: str | None = None,
) -> dict:
    ttl = _DEFAULT_TTLS.get(skill, 60)
    return {
        "skill": skill,
        "status": status,
        "computed_at": _now_iso(),
        "ttl_minutes": ttl,
        "expires_at": time.time() + ttl * 60,
        "confidence_source": confidence_source,
        "summary": summary or {},
        "actionable": actionable or [],
        "alerts": alerts or [],
        "key_insights": key_insights or [],
        "error": error,
    }


def _positions_as_dicts(portfolio: PortfolioResponse) -> list[dict]:
    return [
        {
            "symbol": p.symbol,
            "name": p.name,
            "weight": p.weight,
            "market_value_cad": p.market_value_cad,
            "total_return_pct": p.total_return_pct,
            "currency": p.currency,
            "asset_class": p.asset_class,
            "sector": p.sector,
        }
        for p in portfolio.positions
    ]


# ──────────────────────────────────────────────────────────────────────────────
# portfolio-health
# ──────────────────────────────────────────────────────────────────────────────


def _run_portfolio_health(portfolio: PortfolioResponse) -> dict:
    try:
        health = health_score.compute_health_score(portfolio)
        warnings = portfolio_analytics.concentration_warnings(portfolio)
        # Position-level health flags
        flags: list[dict] = []
        for p in portfolio.positions:
            issues: list[str] = []
            if p.weight > 20:
                issues.append(f"overweight {p.weight:.0f}%")
            if p.total_return_pct < -25:
                issues.append(f"drawdown {p.total_return_pct:.0f}%")
            if issues:
                flags.append(
                    {
                        "symbol": p.symbol,
                        "weight": p.weight,
                        "total_return_pct": p.total_return_pct,
                        "issues": issues,
                    }
                )
        insights = []
        score = health.get("score") or 0
        grade = health.get("grade") or "-"
        insights.append(f"Health {score}/100 ({grade})")
        if warnings:
            insights.append(f"{len(warnings)} concentration warnings")
        if flags:
            insights.append(f"{len(flags)} positions need attention")
        return _snapshot(
            "portfolio-health",
            summary={
                "score": health.get("score"),
                "grade": health.get("grade"),
                "verdict": health.get("verdict"),
                "breakdown": health.get("breakdown"),
                "n_positions": len(portfolio.positions),
                "n_warnings": len(warnings),
            },
            actionable=flags,
            alerts=warnings,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("portfolio-health", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# risk-assessment
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_returns_for(symbols: list[str], lookback_days: int = 252) -> dict[str, list[float]]:
    """Fetch daily simple returns for each symbol. Returns dict - empty if data missing."""
    out: dict[str, list[float]] = {}
    for sym in symbols:
        try:
            bars = data_router.get_history(sym, period="1y", interval="1d")
        except Exception:
            continue
        if not bars or len(bars) < 30:
            continue
        closes = [float(b.get("close") or b.get("adj_close") or 0) for b in bars]
        closes = [c for c in closes if c > 0]
        if len(closes) < 30:
            continue
        rets = quant.simple_returns(closes)
        out[sym] = rets[-lookback_days:]
    return out


def _run_risk_assessment(portfolio: PortfolioResponse) -> dict:
    try:
        total_value = sum(p.market_value_cad for p in portfolio.positions)
        if total_value <= 0:
            return _snapshot("risk-assessment", status="error", error="portfolio total value <= 0")
        # Use top 15 by weight to keep compute bounded
        top = sorted(portfolio.positions, key=lambda p: -p.weight)[:15]
        symbols = [p.symbol for p in top]
        weights = {p.symbol: (p.market_value_cad / total_value) for p in top}
        symbol_returns = _fetch_returns_for(symbols)
        if not symbol_returns:
            return _snapshot(
                "risk-assessment",
                status="ok",
                summary={"n_symbols": 0, "note": "no return data"},
            )
        metrics = quant.portfolio_risk_metrics(symbol_returns, weights)
        # Alerts driven by risk thresholds
        alerts: list[dict] = []
        vol = metrics.get("annualized_volatility_pct")
        mdd = metrics.get("max_drawdown_pct")
        var = metrics.get("var_95_pct")
        if vol is not None and vol > 35:
            alerts.append({"level": "warn", "message": f"Annualized vol {vol:.1f}% - elevated risk"})
        if mdd is not None and mdd < -25:
            alerts.append({"level": "warn", "message": f"Max drawdown {mdd:.1f}% - significant historical pain"})
        if var is not None and var < -3:
            alerts.append({"level": "info", "message": f"VaR(95) {var:.2f}% daily - 1-in-20 days expected loss"})
        insights = []
        if metrics.get("sharpe") is not None:
            insights.append(f"Sharpe {metrics['sharpe']:.2f}")
        if vol is not None:
            insights.append(f"Vol {vol:.1f}% ann.")
        if mdd is not None:
            insights.append(f"Max DD {mdd:.1f}%")
        return _snapshot(
            "risk-assessment",
            summary={
                "n_symbols_used": len(symbol_returns),
                "weight_coverage_pct": round(sum(weights[s] for s in symbol_returns) * 100, 1),
                **metrics,
            },
            alerts=alerts,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("risk-assessment", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# macro-impact
# ──────────────────────────────────────────────────────────────────────────────


def _run_macro_impact(portfolio: PortfolioResponse) -> dict:
    try:
        snap = macro_svc.macro_snapshot()
        breadth = macro_svc.market_breadth()
        regime = breadth.get("market_regime") or "bull_low_fear"
        sector_exp = portfolio_analytics.sector_concentration(portfolio)
        # Score sector alignment vs regime
        alignment = []
        misaligned_weight = 0.0
        for sector, weight in sector_exp.items():
            bias = _SECTOR_REGIME_BIAS.get(sector, {}).get(regime, 0)
            alignment.append(
                {
                    "sector": sector,
                    "weight_pct": round(weight, 2),
                    "regime_bias": bias,
                    "verdict": ("favored" if bias > 0 else "neutral" if bias == 0 else "disfavored"),
                }
            )
            if bias < 0:
                misaligned_weight += weight
        alignment.sort(key=lambda x: -x["weight_pct"])
        alerts = []
        if regime in ("bear_high_fear", "bear_low_fear"):
            alerts.append(
                {
                    "level": "warn",
                    "message": f"Regime {regime} - defensive bias recommended",
                }
            )
        if misaligned_weight > 30:
            alerts.append(
                {
                    "level": "warn",
                    "message": (f"{misaligned_weight:.0f}% of portfolio in disfavored sectors for {regime} regime"),
                }
            )
        if snap.get("portfolio_signal"):
            alerts.append(
                {
                    "level": "info",
                    "message": f"Macro signal: {snap['portfolio_signal']} ({snap.get('portfolio_signal_strength')})",
                }
            )
        insights = [f"Regime: {regime}"]
        if snap.get("vix") is not None:
            insights.append(f"VIX {snap['vix']:.1f}")
        if snap.get("yield_curve_signal"):
            insights.append(f"Yield curve: {snap['yield_curve_signal']}")
        return _snapshot(
            "macro-impact",
            summary={
                "regime": regime,
                "macro": snap,
                "sector_alignment": alignment,
                "misaligned_weight_pct": round(misaligned_weight, 1),
            },
            alerts=alerts,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("macro-impact", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# dividend-strategy
# ──────────────────────────────────────────────────────────────────────────────


def _run_dividend_strategy(portfolio: PortfolioResponse) -> dict:
    try:
        symbols = [p.symbol for p in portfolio.positions]
        fund_data = fund_svc.get_fundamentals(symbols) if symbols else {}
        divs = []
        total_w = sum(p.weight or 0 for p in portfolio.positions)
        weighted_yield = 0.0
        annual_income_cad = 0.0
        for p in portfolio.positions:
            f = fund_data.get(p.symbol) or {}
            dy = f.get("dividend_yield")
            payout = f.get("payout_ratio")
            if not dy or dy <= 0:
                continue
            sustainable = payout is None or payout < 0.75
            position_income = (p.market_value_cad or 0) * dy
            annual_income_cad += position_income
            weighted_yield += dy * (p.weight or 0)
            divs.append(
                {
                    "symbol": p.symbol,
                    "weight_pct": p.weight,
                    "dividend_yield_pct": round(dy * 100, 2),
                    "payout_ratio_pct": round(payout * 100, 1) if payout else None,
                    "annual_income_cad": round(position_income, 2),
                    "sustainable": sustainable,
                }
            )
        divs.sort(key=lambda x: -x["annual_income_cad"])
        portfolio_yield_pct = round(weighted_yield / total_w * 100, 2) if total_w else None
        unsustainable = [d for d in divs if not d["sustainable"]]
        alerts = [
            {
                "level": "warn",
                "message": f"{d['symbol']} payout ratio {d['payout_ratio_pct']}% - dividend may be at risk",
            }
            for d in unsustainable[:3]
        ]
        insights = [
            f"Portfolio yield {portfolio_yield_pct}%" if portfolio_yield_pct else "no dividend data",
            f"Est. annual income ${annual_income_cad:,.0f} CAD",
            f"{len(divs)} dividend payers",
        ]
        return _snapshot(
            "dividend-strategy",
            summary={
                "portfolio_yield_pct": portfolio_yield_pct,
                "annual_income_cad": round(annual_income_cad, 2),
                "n_dividend_payers": len(divs),
                "n_unsustainable": len(unsustainable),
                "top_income_payers": divs[:8],
            },
            alerts=alerts,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("dividend-strategy", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# sector-rotation
# ──────────────────────────────────────────────────────────────────────────────

_SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLU", "XLRE", "XLB", "XLC"]


def _sector_momentum() -> dict[str, dict[str, float | None]]:
    """1m / 3m / 6m return per sector ETF, ranked."""
    out: dict[str, dict[str, float | None]] = {}
    for sym in _SECTOR_ETFS:
        try:
            bars = data_router.get_history(sym, period="1y", interval="1d")
        except Exception:
            continue
        if not bars or len(bars) < 130:
            continue
        closes = [float(b.get("close") or 0) for b in bars]
        closes = [c for c in closes if c > 0]
        if len(closes) < 130:
            continue
        last = closes[-1]
        out[sym] = {
            "1m_pct": round((last / closes[-21] - 1) * 100, 2) if len(closes) > 21 else None,
            "3m_pct": round((last / closes[-63] - 1) * 100, 2) if len(closes) > 63 else None,
            "6m_pct": round((last / closes[-126] - 1) * 100, 2) if len(closes) > 126 else None,
        }
    return out


def _run_sector_rotation(portfolio: PortfolioResponse) -> dict:
    try:
        sectors = portfolio_analytics.sector_concentration(portfolio)
        asset = portfolio_analytics.asset_class_breakdown(portfolio)
        xray = portfolio_analytics.xray_exposures(portfolio)
        momentum = _sector_momentum()
        # Rank sector ETFs by 3m momentum
        ranked = []
        for sym, m in momentum.items():
            if m.get("3m_pct") is None:
                continue
            ranked.append({"etf": sym, **m})
        ranked.sort(key=lambda x: -(x.get("3m_pct") or 0))
        insights = []
        if ranked:
            insights.append(f"Top sector 3m: {ranked[0]['etf']} {ranked[0]['3m_pct']:+.1f}%")
            insights.append(f"Worst sector 3m: {ranked[-1]['etf']} {ranked[-1]['3m_pct']:+.1f}%")
        return _snapshot(
            "sector-rotation",
            summary={
                "portfolio_sectors": sectors,
                "asset_class": asset,
                "xray_exposures": xray,
                "sector_momentum_ranked": ranked,
            },
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("sector-rotation", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# tax-loss-review (Canadian rules: 30-day superficial-loss window both sides)
# ──────────────────────────────────────────────────────────────────────────────


def _run_tax_loss_review(portfolio: PortfolioResponse) -> dict:
    try:
        cands = portfolio_analytics.tax_loss_candidates(portfolio)
        total_loss_cad = sum(c.get("loss_cad") or 0 for c in cands)
        # Tax-saving estimate at 50% inclusion + 30% marginal rate
        est_tax_saving = round(abs(total_loss_cad) * 0.5 * 0.30, 2)
        insights = [
            f"{len(cands)} candidate{'s' if len(cands) != 1 else ''}",
            f"Loss harvest potential ${abs(total_loss_cad):,.0f} CAD",
            f"Est. tax saved ${est_tax_saving:,.0f} (50% inclusion × 30% bracket)",
        ]
        alerts = (
            [
                {
                    "level": "info",
                    "message": (
                        "Superficial-loss rule: do not repurchase same security "
                        "within 30 days before or after sale (or spouse/RRSP "
                        "buy in same window). Use a non-identical correlated proxy."
                    ),
                },
            ]
            if cands
            else []
        )
        return _snapshot(
            "tax-loss-review",
            summary={
                "n_candidates": len(cands),
                "total_loss_cad": round(total_loss_cad, 2),
                "est_tax_saving_cad": est_tax_saving,
            },
            actionable=cands,
            alerts=alerts,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("tax-loss-review", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# cash-deployment (with Kelly dollar sizing)
# ──────────────────────────────────────────────────────────────────────────────


def _run_cash_deployment(portfolio: PortfolioResponse) -> dict:
    try:
        cash = portfolio.summary.cash_available or 0.0
        warnings = portfolio_analytics.concentration_warnings(portfolio)
        recs = rec_svc.get_recommendations(_positions_as_dicts(portfolio))
        # Only consider acceptable-entry BUY/ADD with positive risk_reward
        candidates = [
            r
            for r in recs
            if r.get("action") in ("BUY", "ADD")
            and r.get("entry_timing") == "acceptable"
            and (r.get("risk_reward") or 0) > 0
        ]
        # Exclude already-overweight
        candidates = [c for c in candidates if (c.get("weight") or 0) < 15]
        # Rank: signal_quality desc, then score desc
        candidates.sort(
            key=lambda r: (
                -(r.get("signal_quality") or 0),
                -(r.get("score") or 0),
            )
        )
        # Dollar-size with Kelly% of CASH available
        sized: list[dict] = []
        cash_remaining = cash
        for r in candidates[:5]:
            kelly = r.get("kelly_pct") or 0
            if kelly <= 0:
                continue
            allocation = round(cash * (kelly / 100), 2)
            allocation = min(allocation, cash_remaining)
            if allocation < 50:
                continue
            cash_remaining -= allocation
            sized.append(
                {
                    "symbol": r["symbol"],
                    "action": r["action"],
                    "score": r["score"],
                    "confidence": r.get("confidence"),
                    "signal_quality": r.get("signal_quality"),
                    "current_price": r.get("current_price"),
                    "stop_loss": r.get("stop_loss"),
                    "take_profit": r.get("take_profit"),
                    "risk_reward": r.get("risk_reward"),
                    "kelly_pct": kelly,
                    "allocation_cad": allocation,
                    "reasons": (r.get("reasons") or [])[:3],
                }
            )
        insights = [
            f"Cash available ${cash:,.0f} CAD",
            f"{len(sized)} BUY/ADD candidates sized" if sized else "no qualified candidates",
            (f"Top allocation: {sized[0]['symbol']} ${sized[0]['allocation_cad']:,.0f}") if sized else "-",
        ]
        return _snapshot(
            "cash-deployment",
            summary={
                "cash_available_cad": round(cash, 2),
                "cash_remaining_after_plan_cad": round(cash_remaining, 2),
                "n_qualified_candidates": len(sized),
                "candidates": sized,
            },
            actionable=sized,
            alerts=warnings[:3],
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("cash-deployment", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# stock-analysis (top buy/sell pulls from recommendations engine)
# ──────────────────────────────────────────────────────────────────────────────


def _run_stock_analysis(portfolio: PortfolioResponse) -> dict:
    try:
        recs = rec_svc.get_recommendations(_positions_as_dicts(portfolio))
        by_action: dict[str, int] = {}
        for r in recs:
            by_action[r["action"]] = by_action.get(r["action"], 0) + 1
        buys = sorted([r for r in recs if r["action"] in ("BUY", "ADD")], key=lambda x: -(x.get("score") or 0))[:5]
        sells = sorted([r for r in recs if r["action"] in ("SELL", "TRIM")], key=lambda x: x.get("score") or 99)[:5]
        hedges = [{"symbol": r["symbol"], "reason": r["hedge_reason"]} for r in recs if r.get("hedge_flag")]
        insights = [
            f"{by_action.get('BUY', 0)} BUY · {by_action.get('SELL', 0)} SELL · {by_action.get('NO_EDGE', 0)} NO_EDGE",
            (f"Top BUY: {buys[0]['symbol']} ({buys[0].get('score'):.1f})" if buys else "no BUYs"),
            f"{len(hedges)} hedge flag{'s' if len(hedges) != 1 else ''}",
        ]
        return _snapshot(
            "stock-analysis",
            summary={
                "n_positions": len(recs),
                "by_action": by_action,
                "top_buys": buys,
                "top_sells": sells,
                "hedge_flags": hedges,
            },
            actionable=recs,
            alerts=[{"level": "warn", "message": f"{h['symbol']}: {h['reason']}"} for h in hedges],
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("stock-analysis", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# earnings-analyzer
# ──────────────────────────────────────────────────────────────────────────────


def _run_earnings_analyzer(portfolio: PortfolioResponse) -> dict:
    try:
        symbols = [p.symbol for p in portfolio.positions]
        fund_data = fund_svc.get_fundamentals(symbols) if symbols else {}
        upcoming: list[dict] = []
        for p in portfolio.positions:
            f = fund_data.get(p.symbol) or {}
            days = f.get("days_to_earnings")
            if days is None or days > 21:
                continue
            expected_move = f.get("expected_move_pct")
            position_at_risk = None
            if expected_move and p.market_value_cad:
                position_at_risk = round(
                    p.market_value_cad * expected_move / 100,
                    2,
                )
            upcoming.append(
                {
                    "symbol": p.symbol,
                    "earnings_date": f.get("earnings_date"),
                    "days_to_earnings": days,
                    "weight_pct": p.weight,
                    "market_value_cad": p.market_value_cad,
                    "expected_move_pct": expected_move,
                    "position_at_risk_cad": position_at_risk,
                    "eps_estimate": f.get("earnings_estimate_eps"),
                }
            )
        upcoming.sort(key=lambda x: x["days_to_earnings"])
        imminent = [u for u in upcoming if u["days_to_earnings"] <= 7]
        alerts = [
            {
                "level": "warn",
                "message": (
                    f"{u['symbol']} earnings in {u['days_to_earnings']}d - "
                    f"±{u.get('expected_move_pct') or '?'}% expected move"
                    + (f", ${u['position_at_risk_cad']:,.0f} CAD at risk" if u.get("position_at_risk_cad") else "")
                ),
            }
            for u in imminent
        ]
        total_at_risk = sum(u.get("position_at_risk_cad") or 0 for u in upcoming)
        insights = [
            f"{len(upcoming)} earnings within 21d",
            f"{len(imminent)} imminent (≤7d)",
            f"Total ${total_at_risk:,.0f} CAD at expected-move risk",
        ]
        return _snapshot(
            "earnings-analyzer",
            summary={
                "n_within_21d": len(upcoming),
                "n_imminent_7d": len(imminent),
                "total_at_risk_cad": round(total_at_risk, 2),
                "upcoming": upcoming,
            },
            alerts=alerts,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("earnings-analyzer", status="error", error=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# daily-briefing (composite - synthesizes other snapshots)
# ──────────────────────────────────────────────────────────────────────────────


def _run_daily_briefing(portfolio: PortfolioResponse) -> dict:
    try:
        tenant = _active_tenant.get()
        composed: dict[str, Any] = {}
        all_alerts: list[dict] = []
        all_insights: list[str] = []
        for s in (
            "macro-impact",
            "portfolio-health",
            "stock-analysis",
            "earnings-analyzer",
            "cash-deployment",
            "risk-assessment",
            "tax-loss-review",
        ):
            snap = read_snapshot(s, tenant_id=tenant)
            if not snap or snap.get("status") != "ok":
                continue
            composed[s] = {
                "summary": snap.get("summary"),
                "key_insights": snap.get("key_insights") or [],
            }
            for a in snap.get("alerts") or []:
                if a.get("level") == "warn":
                    all_alerts.append({"source": s, **a})
            for ins in snap.get("key_insights") or []:
                all_insights.append(f"{s.split('-')[0]}: {ins}")
        # Next action: prefer earnings-imminent > sell signals > rebalance > cash deployment
        next_action: str | None = None
        ea = composed.get("earnings-analyzer", {}).get("summary") or {}
        sa = composed.get("stock-analysis", {}).get("summary") or {}
        cd = composed.get("cash-deployment", {}).get("summary") or {}
        ph = composed.get("portfolio-health", {}).get("summary") or {}
        if ea.get("n_imminent_7d", 0) > 0:
            next_action = (
                f"Review {ea['n_imminent_7d']} position(s) with earnings ≤7d - trim or hedge before binary event"
            )
        elif sa.get("by_action", {}).get("SELL", 0) > 0:
            top = sa.get("top_sells") or []
            sym = top[0]["symbol"] if top else "?"
            next_action = f"Review SELL signal on {sym} - thesis broken or deteriorating"
        elif ph.get("n_warnings", 0) > 0:
            next_action = "Rebalance - concentration warning active"
        elif cd.get("n_qualified_candidates", 0) > 0:
            cands = cd.get("candidates") or []
            sym = cands[0]["symbol"] if cands else "?"
            next_action = f"Deploy cash - top candidate {sym}"
        return _snapshot(
            "daily-briefing",
            summary={
                "composed_from": list(composed.keys()),
                "next_action": next_action,
                "details": composed,
            },
            alerts=all_alerts[:8],
            key_insights=all_insights[:8],
        )
    except Exception as e:
        return _snapshot("daily-briefing", status="error", error=str(e))


SKILL_RUNNERS: dict[str, Callable[[PortfolioResponse], dict]] = {
    "portfolio-health": _run_portfolio_health,
    "risk-assessment": _run_risk_assessment,
    "macro-impact": _run_macro_impact,
    "dividend-strategy": _run_dividend_strategy,
    "sector-rotation": _run_sector_rotation,
    "tax-loss-review": _run_tax_loss_review,
    "cash-deployment": _run_cash_deployment,
    "stock-analysis": _run_stock_analysis,
    "earnings-analyzer": _run_earnings_analyzer,
    "daily-briefing": _run_daily_briefing,
}


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot store
# ──────────────────────────────────────────────────────────────────────────────


def _tenant_dir(tenant_id: str | None) -> Path:
    """Resolve the snapshot directory for a tenant.

    `None` returns the legacy global directory for backwards compatibility.
    Otherwise snapshots are namespaced under `_SNAP_DIR/tenants/<sha1>/`
    so per-user output never leaks across tenants. We hash the tenant id
    so session ids never appear on disk in plaintext.
    """
    if not tenant_id:
        return _SNAP_DIR
    import hashlib

    h = hashlib.sha1(tenant_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    d = _SNAP_DIR / "tenants" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_snapshot(snap: dict, *, tenant_id: str | None = None) -> Path:
    skill = snap["skill"]
    target_dir = _tenant_dir(tenant_id)
    path = target_dir / f"{skill}.json"
    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    return path


def read_snapshot(skill: str, *, tenant_id: str | None = None) -> dict | None:
    target_dir = _tenant_dir(tenant_id)
    path = target_dir / f"{skill}.json"
    if not path.exists():
        # Fall back to global only when tenant_id is set but no tenant
        # snapshot exists yet - avoids a blank UI on first run.
        if tenant_id and (_SNAP_DIR / f"{skill}.json").exists():
            path = _SNAP_DIR / f"{skill}.json"
        else:
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    expires = data.get("expires_at") or 0
    data["fresh"] = time.time() < expires
    return data


def list_snapshots(*, tenant_id: str | None = None) -> list[dict]:
    target_dir = _tenant_dir(tenant_id)
    out: list[dict] = []
    if not target_dir.exists():
        return out
    for path in sorted(target_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            expires = data.get("expires_at") or 0
            data["fresh"] = time.time() < expires
            out.append(data)
        except json.JSONDecodeError:
            continue
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Parallel runner
# ──────────────────────────────────────────────────────────────────────────────


def run_all_skills(
    portfolio: PortfolioResponse,
    *,
    skills: list[str] | None = None,
    persist: bool = True,
    max_workers: int = 8,
    tenant_id: str | None = None,
) -> dict[str, dict]:
    """Run requested (or all codified) skills in parallel. daily-briefing runs last.

    `tenant_id` namespaces the on-disk snapshot path. None writes to the
    legacy global directory (single-tenant backwards-compat).
    """
    requested = skills or list(SKILL_RUNNERS.keys())
    parallel_skills = [s for s in requested if s != "daily-briefing"]
    out: dict[str, dict] = {}

    token = _active_tenant.set(tenant_id)
    try:
        pass
    finally:
        pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(SKILL_RUNNERS[s], portfolio): s for s in parallel_skills if s in SKILL_RUNNERS}
        for fut in concurrent.futures.as_completed(futures):
            s = futures[fut]
            try:
                snap = fut.result(timeout=60)
            except Exception as e:
                snap = _snapshot(s, status="error", error=f"runner timeout/exception: {e}")
            out[s] = snap
            if persist:
                try:
                    write_snapshot(snap, tenant_id=tenant_id)
                except Exception:
                    logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    if "daily-briefing" in requested and "daily-briefing" in SKILL_RUNNERS:
        snap = SKILL_RUNNERS["daily-briefing"](portfolio)
        out["daily-briefing"] = snap
        if persist:
            try:
                write_snapshot(snap, tenant_id=tenant_id)
            except Exception:
                logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    _active_tenant.reset(token)
    return out


def codified_skills() -> list[str]:
    return list(SKILL_RUNNERS.keys())


def llm_only_skills() -> list[str]:
    return sorted(LLM_ONLY_SKILLS)


# ──────────────────────────────────────────────────────────────────────────────
# Composer fallback runners (ctx-based, deterministic - no LLM, no PII egress)
#
# Resolved by scripts/run_skill_fallback.py when Claude is unavailable. Unlike
# the LLM-narrative skills in skill_llm_runner, these are pure-Python: they load
# the portfolio from the WS session and reuse the same recommendation engine the
# dashboard uses, so they degrade gracefully even with no LLM provider key set.
# ──────────────────────────────────────────────────────────────────────────────


async def _load_portfolio_from_session(session_id: str) -> PortfolioResponse:
    """Build an enriched PortfolioResponse from a restored WS session id."""
    from app.services import market_data, wealthsimple

    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    if not profile:
        raise RuntimeError("session lost or has no profile")
    cash = sum(a.cash_balance for a in profile.accounts)
    nlv = sum(a.invested_value for a in profile.accounts)
    upnl = float(session.get("unrealized_pnl_cad") or 0.0)
    raw = await asyncio.to_thread(wealthsimple.get_all_positions, session_id)
    return market_data.enrich(raw, cash, nlv, upnl)


def _ctx_session_id(context: dict | None) -> str | None:
    return (context or {}).get("session_id")


async def run_top_trades_today(context: dict | None = None) -> dict:
    """Ranked, decision-ready trade ideas across holdings + watchlist.

    Codified mirror of the top-trades-today skill - reuses the recommendation
    engine + trade_ideas.rank_trade_ideas so output matches the MCP tool.
    """
    from app.services import trade_ideas as trade_ideas_svc
    from app.services import watchlist as watchlist_svc

    sid = _ctx_session_id(context)
    if not sid:
        return _snapshot("top-trades-today", status="error", error="no_session")
    try:
        portfolio = await _load_portfolio_from_session(sid)
        held = {p.symbol for p in portfolio.positions if p.symbol}
        positions = _positions_as_dicts(portfolio)
        try:
            wl = await asyncio.to_thread(watchlist_svc.load_watchlist)
        except Exception:
            wl = []
        for i in wl:
            sym = i.get("symbol")
            if not sym or sym in held:
                continue
            positions.append(
                {
                    "symbol": sym,
                    "name": i.get("name") or sym,
                    "weight": 0.0,
                    "market_value_cad": 0.0,
                    "total_return_pct": 0.0,
                    "currency": "CAD" if sym.endswith((".TO", ".V")) else "USD",
                    "asset_class": i.get("asset_class") or "stock",
                    "sector": "",
                }
            )
        recs = await asyncio.to_thread(rec_svc.get_recommendations, positions)
        ranked = trade_ideas_svc.rank_trade_ideas(recs, held, top_n=5, min_risk_reward=1.5)
        ideas = ranked["ideas"]
        insights = [
            f"{ranked['scored']} scored · {ranked['actionable']} actionable",
        ]
        for idea in ideas[:3]:
            rr = idea.get("risk_reward")
            rr_str = f" · R:R {rr:.1f}" if rr else ""
            insights.append(
                f"{idea['symbol']} ({'held' if idea['held'] else 'watch'}) "
                f"{idea['action']} {idea.get('conviction') or ''}{rr_str}".strip()
            )
        if not ideas:
            insights.append("No actionable setups - all HOLD/WATCH or below R:R floor.")
        return _snapshot(
            "top-trades-today",
            summary={
                "universe": "holdings+watchlist",
                "scored": ranked["scored"],
                "actionable": ranked["actionable"],
            },
            actionable=ideas,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("top-trades-today", status="error", error=str(e))


_REVIEW_VERDICT = {"SELL": "SELL", "TRIM": "TRIM", "ADD": "HOLD", "BUY": "HOLD"}


async def run_position_review(context: dict | None = None) -> dict:
    """HOLD/TRIM/SELL verdict per top holding (deterministic routing fallback).

    Codified mirror of the position-review sweep - derives the verdict from the
    recommendation engine action and attaches stop/target levels. No per-name
    LLM routing; the deep adversarial path is Claude-only by design.
    """
    sid = _ctx_session_id(context)
    if not sid:
        return _snapshot("position-review", status="error", error="no_session")
    try:
        portfolio = await _load_portfolio_from_session(sid)
        top = sorted(portfolio.positions, key=lambda p: -(p.weight or 0))[:6]
        positions = _positions_as_dicts(portfolio)
        recs = await asyncio.to_thread(rec_svc.get_recommendations, positions)
        rec_by_sym = {r.get("symbol"): r for r in recs}
        roster = {"HOLD": 0, "TRIM": 0, "SELL": 0}
        reviewed: list[dict] = []
        alerts: list[dict] = []
        for p in top:
            r = rec_by_sym.get(p.symbol) or {}
            action = (r.get("action") or "HOLD").upper()
            verdict = _REVIEW_VERDICT.get(action, "HOLD")
            roster[verdict] += 1
            reasons = (r.get("reasons") or [])[:2]
            reviewed.append(
                {
                    "symbol": p.symbol,
                    "weight_pct": p.weight,
                    "total_return_pct": p.total_return_pct,
                    "verdict": verdict,
                    "conviction": r.get("confidence"),
                    "score": r.get("score"),
                    "stop_loss": r.get("stop_loss"),
                    "take_profit": r.get("take_profit"),
                    "reasons": reasons,
                }
            )
            if verdict in ("SELL", "TRIM"):
                alerts.append(
                    {
                        "level": "warn",
                        "message": f"{p.symbol}: {verdict} - {(reasons or ['signal deterioration'])[0]}",
                    }
                )
        # Worst first: SELL, then TRIM, then HOLD; within each, lowest score first.
        order = {"SELL": 0, "TRIM": 1, "HOLD": 2}
        reviewed.sort(key=lambda x: (order[x["verdict"]], x.get("score") or 99))
        insights = [
            f"HOLD x{roster['HOLD']} · TRIM x{roster['TRIM']} · SELL x{roster['SELL']}",
        ]
        for x in reviewed[:3]:
            insights.append(f"{x['symbol']} {x['verdict']} (score {x.get('score')})")
        return _snapshot(
            "position-review",
            summary={
                "n_reviewed": len(reviewed),
                "roster": roster,
            },
            actionable=reviewed,
            alerts=alerts,
            key_insights=insights,
        )
    except Exception as e:
        return _snapshot("position-review", status="error", error=str(e))
