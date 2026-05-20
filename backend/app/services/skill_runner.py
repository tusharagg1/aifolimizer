"""Skill runner — background-executable Python implementations of analysis skills.

Per-skill pure-Python service functions that operate on a PortfolioResponse and
return a SkillSnapshot. These are intended to be executed by the scheduler
(`app/jobs/scheduler.py`) and cached to disk so the frontend can read pre-
computed skill outputs instead of invoking Claude interactively for every view.

LLM-required skills (adversarial-research, earnings-postmortem, stock-compare)
are deliberately NOT codified here — they remain on-demand via Claude.

Snapshot lifecycle:
  scheduler -> run_all_skills(portfolio) -> {skill: SkillSnapshot}
            -> write each to .cache/skill_snapshots/{skill}.json
  REST/MCP  -> read_snapshot(skill) -> {payload}

Each SkillSnapshot is a dict with: skill, status, computed_at, ttl_minutes,
freshness, summary, actionable, alerts, confidence_source, error (if any).
"""
from __future__ import annotations

import concurrent.futures
import json
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


_SNAP_DIR = Path(__file__).resolve().parents[2] / ".cache" / "skill_snapshots"
_SNAP_DIR.mkdir(parents=True, exist_ok=True)

# Skills that need an LLM and cannot be codified.
LLM_ONLY_SKILLS = frozenset({
    "adversarial-research",
    "earnings-postmortem",
    "stock-compare",
})

# Default TTL per skill in minutes. Scheduler uses this to decide refresh.
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


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _snapshot(
    skill: str,
    *,
    summary: dict | None = None,
    actionable: list | None = None,
    alerts: list | None = None,
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
        "error": error,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Codified skill runners (10 of 13 — LLM-required 3 are not here)
# ──────────────────────────────────────────────────────────────────────────────


def _run_portfolio_health(portfolio: PortfolioResponse) -> dict:
    try:
        health = health_score.compute_health_score(portfolio)
        warnings = portfolio_analytics.concentration_warnings(portfolio)
        return _snapshot(
            "portfolio-health",
            summary={
                "score": health.get("score"),
                "grade": health.get("grade"),
                "verdict": health.get("verdict"),
                "breakdown": health.get("breakdown"),
                "n_positions": len(portfolio.positions),
            },
            actionable=[],
            alerts=warnings,
        )
    except Exception as e:
        return _snapshot("portfolio-health", status="error", error=str(e))


def _run_risk_assessment(portfolio: PortfolioResponse) -> dict:
    try:
        from app.services import quant
        symbols = [p.symbol for p in portfolio.positions][:20]
        risk = quant.portfolio_risk_metrics(symbols) if symbols else {}
        return _snapshot(
            "risk-assessment",
            summary={
                "ann_vol_pct": risk.get("annualized_vol"),
                "sharpe": risk.get("sharpe"),
                "sortino": risk.get("sortino"),
                "var_95_pct": risk.get("var_95"),
                "max_drawdown_pct": risk.get("max_drawdown"),
                "n_symbols": len(symbols),
            },
        )
    except Exception as e:
        return _snapshot("risk-assessment", status="error", error=str(e))


def _run_macro_impact(portfolio: PortfolioResponse) -> dict:
    try:
        snap = macro_svc.macro_snapshot()
        regime = macro_svc.market_breadth().get("market_regime")
        sector_exp = portfolio_analytics.sector_concentration(portfolio)
        alerts = []
        if regime in ("bear_high_fear", "bear_low_fear"):
            alerts.append({
                "level": "warn",
                "message": f"Regime {regime} — defensive bias recommended",
            })
        return _snapshot(
            "macro-impact",
            summary={
                "regime": regime,
                "macro": snap,
                "portfolio_sectors": sector_exp,
            },
            alerts=alerts,
        )
    except Exception as e:
        return _snapshot("macro-impact", status="error", error=str(e))


def _run_dividend_strategy(portfolio: PortfolioResponse) -> dict:
    try:
        symbols = [p.symbol for p in portfolio.positions]
        fund_data = fund_svc.get_fundamentals(symbols) if symbols else {}
        divs = []
        for sym, f in fund_data.items():
            dy = f.get("dividend_yield")
            payout = f.get("payout_ratio")
            if dy and dy > 0:
                divs.append({
                    "symbol": sym,
                    "dividend_yield_pct": round(dy * 100, 2),
                    "payout_ratio_pct": round(payout * 100, 1) if payout else None,
                    "sustainable": (payout is None or payout < 0.75),
                })
        divs.sort(key=lambda x: x["dividend_yield_pct"], reverse=True)
        weighted_yield = 0.0
        total_w = 0.0
        for p in portfolio.positions:
            f = fund_data.get(p.symbol) or {}
            dy = f.get("dividend_yield") or 0
            weighted_yield += dy * (p.weight or 0)
            total_w += p.weight or 0
        portfolio_yield_pct = (
            round(weighted_yield / total_w * 100, 2) if total_w else None
        )
        return _snapshot(
            "dividend-strategy",
            summary={
                "portfolio_yield_pct": portfolio_yield_pct,
                "top_yielders": divs[:5],
                "n_dividend_payers": len(divs),
            },
        )
    except Exception as e:
        return _snapshot("dividend-strategy", status="error", error=str(e))


def _run_sector_rotation(portfolio: PortfolioResponse) -> dict:
    try:
        sectors = portfolio_analytics.sector_concentration(portfolio)
        asset = portfolio_analytics.asset_class_breakdown(portfolio)
        xray = portfolio_analytics.xray_exposures(portfolio)
        return _snapshot(
            "sector-rotation",
            summary={
                "sector_concentration": sectors,
                "asset_class": asset,
                "xray_exposures": xray,
            },
        )
    except Exception as e:
        return _snapshot("sector-rotation", status="error", error=str(e))


def _run_tax_loss_review(portfolio: PortfolioResponse) -> dict:
    try:
        cands = portfolio_analytics.tax_loss_candidates(portfolio)
        return _snapshot(
            "tax-loss-review",
            summary={"n_candidates": len(cands)},
            actionable=cands,
        )
    except Exception as e:
        return _snapshot("tax-loss-review", status="error", error=str(e))


def _run_cash_deployment(portfolio: PortfolioResponse) -> dict:
    try:
        warnings = portfolio_analytics.concentration_warnings(portfolio)
        # Pull recommendations to find best add-to candidates
        positions_dicts = [
            {"symbol": p.symbol, "weight": p.weight,
             "market_value_cad": p.market_value_cad,
             "total_return_pct": p.total_return_pct,
             "currency": p.currency, "asset_class": p.asset_class,
             "name": p.name}
            for p in portfolio.positions
        ]
        recs = rec_svc.get_recommendations(positions_dicts)
        buy_or_add = [
            r for r in recs
            if r.get("action") in ("BUY", "ADD")
            and r.get("entry_timing") == "acceptable"
        ]
        crowded = {
            (w.get("symbol") or w.get("ticker")) for w in warnings
            if "concentration" in (w.get("type") or "").lower()
        }
        buy_or_add = [r for r in buy_or_add if r["symbol"] not in crowded]
        buy_or_add.sort(
            key=lambda r: (
                -(r.get("signal_quality") or 0),
                -(r.get("score") or 0),
            )
        )
        return _snapshot(
            "cash-deployment",
            summary={
                "n_candidates": len(buy_or_add),
                "top_candidates": [
                    {
                        "symbol": r["symbol"],
                        "score": r.get("score"),
                        "action": r.get("action"),
                        "signal_quality": r.get("signal_quality"),
                        "kelly_pct": r.get("kelly_pct"),
                        "reasons": (r.get("reasons") or [])[:3],
                    }
                    for r in buy_or_add[:5]
                ],
            },
            alerts=warnings,
        )
    except Exception as e:
        return _snapshot("cash-deployment", status="error", error=str(e))


def _run_stock_analysis(portfolio: PortfolioResponse) -> dict:
    try:
        positions_dicts = [
            {"symbol": p.symbol, "weight": p.weight,
             "market_value_cad": p.market_value_cad,
             "total_return_pct": p.total_return_pct,
             "currency": p.currency, "asset_class": p.asset_class,
             "name": p.name}
            for p in portfolio.positions
        ]
        recs = rec_svc.get_recommendations(positions_dicts)
        by_action: dict[str, int] = {}
        for r in recs:
            by_action[r["action"]] = by_action.get(r["action"], 0) + 1
        return _snapshot(
            "stock-analysis",
            summary={
                "n_positions": len(recs),
                "by_action": by_action,
                "buys": [r for r in recs if r["action"] == "BUY"][:5],
                "sells": [r for r in recs if r["action"] in ("SELL", "TRIM")][:5],
                "hedge_flags": [
                    {"symbol": r["symbol"], "reason": r["hedge_reason"]}
                    for r in recs if r.get("hedge_flag")
                ],
            },
            actionable=recs,
        )
    except Exception as e:
        return _snapshot("stock-analysis", status="error", error=str(e))


def _run_earnings_analyzer(portfolio: PortfolioResponse) -> dict:
    try:
        symbols = [p.symbol for p in portfolio.positions]
        fund_data = fund_svc.get_fundamentals(symbols) if symbols else {}
        upcoming = []
        for sym, f in fund_data.items():
            ed = f.get("earnings_date")
            days = f.get("days_to_earnings")
            if days is not None and days <= 21:
                upcoming.append({
                    "symbol": sym,
                    "earnings_date": ed,
                    "days_to_earnings": days,
                    "weight": next(
                        (p.weight for p in portfolio.positions if p.symbol == sym),
                        None,
                    ),
                })
        upcoming.sort(key=lambda x: x["days_to_earnings"])
        alerts = [
            {"level": "warn", "message": f"{u['symbol']} earnings in {u['days_to_earnings']}d"}
            for u in upcoming if u["days_to_earnings"] <= 7
        ]
        return _snapshot(
            "earnings-analyzer",
            summary={"n_within_21d": len(upcoming), "upcoming": upcoming},
            alerts=alerts,
        )
    except Exception as e:
        return _snapshot("earnings-analyzer", status="error", error=str(e))


def _run_daily_briefing(portfolio: PortfolioResponse) -> dict:
    """Composite — reads from other already-computed snapshots when available."""
    try:
        composed: dict[str, Any] = {}
        for s in (
            "portfolio-health", "macro-impact", "stock-analysis",
            "tax-loss-review", "cash-deployment", "earnings-analyzer",
        ):
            snap = read_snapshot(s)
            if snap and snap.get("status") == "ok":
                composed[s] = {
                    "summary": snap.get("summary"),
                    "alerts": snap.get("alerts"),
                }
        return _snapshot(
            "daily-briefing",
            summary={"composed_from": list(composed.keys()), "details": composed},
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
    # daily-briefing reads others — run last
    "daily-briefing": _run_daily_briefing,
}


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot store
# ──────────────────────────────────────────────────────────────────────────────


def write_snapshot(snap: dict) -> Path:
    skill = snap["skill"]
    path = _SNAP_DIR / f"{skill}.json"
    _SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    return path


def read_snapshot(skill: str) -> dict | None:
    path = _SNAP_DIR / f"{skill}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    # Compute freshness
    expires = data.get("expires_at") or 0
    data["fresh"] = time.time() < expires
    return data


def list_snapshots() -> list[dict]:
    out: list[dict] = []
    if not _SNAP_DIR.exists():
        return out
    for path in sorted(_SNAP_DIR.glob("*.json")):
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
) -> dict[str, dict]:
    """Run requested (or all codified) skills in parallel. Writes snapshots.

    daily-briefing depends on other snapshots — run it last sequentially.
    """
    requested = skills or list(SKILL_RUNNERS.keys())
    parallel_skills = [s for s in requested if s != "daily-briefing"]
    out: dict[str, dict] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(SKILL_RUNNERS[s], portfolio): s
            for s in parallel_skills
            if s in SKILL_RUNNERS
        }
        for fut in concurrent.futures.as_completed(futures):
            s = futures[fut]
            try:
                snap = fut.result(timeout=60)
            except Exception as e:
                snap = _snapshot(s, status="error", error=f"runner timeout/exception: {e}")
            out[s] = snap
            if persist:
                try:
                    write_snapshot(snap)
                except Exception:
                    pass

    # daily-briefing depends on others — run after they've been written
    if "daily-briefing" in requested and "daily-briefing" in SKILL_RUNNERS:
        snap = SKILL_RUNNERS["daily-briefing"](portfolio)
        out["daily-briefing"] = snap
        if persist:
            try:
                write_snapshot(snap)
            except Exception:
                pass

    return out


def codified_skills() -> list[str]:
    return list(SKILL_RUNNERS.keys())


def llm_only_skills() -> list[str]:
    return sorted(LLM_ONLY_SKILLS)
