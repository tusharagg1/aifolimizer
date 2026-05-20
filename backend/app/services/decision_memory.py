"""Per-ticker trade decision log with outcome tracking and reflection injection.

Adapted from TradingAgents (TauricResearch) memory log pattern:
  Phase A — store decision at time of recommendation
  Phase B — resolve outcome N days later (hit target / hit stop / still open)
  Phase C — surface lessons for same-ticker and cross-ticker context injection

Stored as JSONL at ~/.aifolimizer/decisions.jsonl — no database required.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

_DECISIONS_FILE = Path.home() / ".aifolimizer" / "decisions.jsonl"
_SCHEMA_VERSION = "1.0"

Outcome = Literal["open", "target_hit", "stop_hit", "expired", "manual"]


def _load_all() -> list[dict]:
    if not _DECISIONS_FILE.exists():
        return []
    records = []
    for line in _DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _save_all(records: list[dict]) -> None:
    """Full rewrite — used only when records are mutated in place
    (resolve_outcomes). For log_decision use _append_one.
    """
    _DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DECISIONS_FILE.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _append_one(record: dict) -> None:
    """Append a single record without rewriting the whole file."""
    _DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _DECISIONS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_decision(
    ticker: str,
    action: str,
    conviction: str,
    entry_price: float,
    target_price: float,
    stop_price: float,
    thesis_summary: str,
    skill_used: str = "",
) -> dict:
    """Phase A — record a new trade decision.

    conviction: Strong Buy | Buy | Neutral | Sell | Strong Sell
    skill_used: which skill produced this (adversarial-research, cash-deployment, etc.)
    """
    record = {
        "schema_version": _SCHEMA_VERSION,
        "ticker": ticker.upper(),
        "action": action,
        "conviction": conviction,
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "thesis_summary": thesis_summary,
        "skill_used": skill_used,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "outcome": "open",
        "outcome_price": None,
        "outcome_utc": None,
        "reflection": None,
    }
    _append_one(record)
    return {"logged": True, "ticker": ticker.upper(), "action": action}


def resolve_outcomes(price_map: dict[str, float], days_expiry: int = 90) -> dict:
    """Phase B — mark-to-market open decisions using current prices.

    price_map: {TICKER: current_price}
    Decisions older than days_expiry with no target/stop hit are marked 'expired'.
    Returns summary of resolved count by outcome.
    """
    records = _load_all()
    now = time.time()
    resolved: dict[str, int] = {"target_hit": 0, "stop_hit": 0, "expired": 0}

    for rec in records:
        if rec.get("outcome") != "open":
            continue

        ticker = rec["ticker"]
        price = price_map.get(ticker)
        if price is None:
            continue

        entry = rec.get("entry_price") or 0
        target = rec.get("target_price") or 0
        stop = rec.get("stop_price") or 0
        created_ts = _parse_utc(rec.get("created_utc", ""))
        age_days = (now - created_ts) / 86400 if created_ts else 0

        outcome: Outcome | None = None

        action_norm = str(rec.get("action") or "").strip().upper()
        if action_norm in {"BUY", "STRONG BUY"}:
            if target and price >= target:
                outcome = "target_hit"
            elif stop and price <= stop:
                outcome = "stop_hit"
        elif action_norm in {"SELL", "STRONG SELL"}:
            if target and price <= target:
                outcome = "target_hit"
            elif stop and price >= stop:
                outcome = "stop_hit"

        if outcome is None and age_days >= days_expiry:
            outcome = "expired"

        if outcome:
            rec["outcome"] = outcome
            rec["outcome_price"] = round(price, 4)
            rec["outcome_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            pnl_pct = round((price - entry) / entry * 100, 2) if entry else None
            rec["reflection"] = _generate_reflection(rec, pnl_pct)
            resolved[outcome] = resolved.get(outcome, 0) + 1

    _save_all(records)
    return {"resolved": resolved, "total_open_remaining": sum(1 for r in records if r.get("outcome") == "open")}


def get_ticker_history(ticker: str, max_decisions: int = 5) -> list[dict]:
    """Phase C — last N decisions for a ticker, newest first.

    Returns condensed records suitable for injecting into skill prompts.
    """
    ticker = ticker.upper()
    records = _load_all()
    matches = [r for r in records if r.get("ticker") == ticker]
    matches.sort(key=lambda r: r.get("created_utc", ""), reverse=True)

    return [
        {
            "date": r.get("created_utc", "")[:10],
            "action": r.get("action"),
            "conviction": r.get("conviction"),
            "entry_price": r.get("entry_price"),
            "target_price": r.get("target_price"),
            "stop_price": r.get("stop_price"),
            "outcome": r.get("outcome"),
            "outcome_price": r.get("outcome_price"),
            "reflection": r.get("reflection"),
            "thesis_summary": (r.get("thesis_summary") or "")[:200],
            "skill_used": r.get("skill_used"),
        }
        for r in matches[:max_decisions]
    ]


def get_cross_ticker_lessons(max_lessons: int = 3) -> list[dict]:
    """Phase C — top resolved decisions across all tickers for cross-ticker lesson injection.

    Prioritises: target_hit (wins) and stop_hit (losses) — excludes open/expired.
    Returns newest-first, capped at max_lessons each for wins and losses.
    """
    records = _load_all()
    wins = [r for r in records if r.get("outcome") == "target_hit"]
    losses = [r for r in records if r.get("outcome") == "stop_hit"]

    for lst in (wins, losses):
        lst.sort(key=lambda r: r.get("outcome_utc", ""), reverse=True)

    lessons = []
    for r in wins[:max_lessons] + losses[:max_lessons]:
        entry = r.get("entry_price") or 0
        out = r.get("outcome_price") or 0
        pnl_pct = round((out - entry) / entry * 100, 2) if entry else None
        lessons.append({
            "ticker": r.get("ticker"),
            "action": r.get("action"),
            "outcome": r.get("outcome"),
            "pnl_pct": pnl_pct,
            "reflection": r.get("reflection"),
            "date": (r.get("outcome_utc") or r.get("created_utc", ""))[:10],
        })
    return lessons


def _generate_reflection(rec: dict, pnl_pct: float | None) -> str:
    outcome = rec.get("outcome")
    ticker = rec.get("ticker")
    action = rec.get("action")
    conviction = rec.get("conviction", "")
    thesis = (rec.get("thesis_summary") or "")[:120]

    if outcome == "target_hit":
        return (
            f"{ticker} {action} target hit"
            + (f" (+{pnl_pct}%)" if pnl_pct else "")
            + f". Conviction '{conviction}' confirmed. Thesis: {thesis}"
        )
    elif outcome == "stop_hit":
        return (
            f"{ticker} {action} stopped out"
            + (f" ({pnl_pct}%)" if pnl_pct else "")
            + f". Conviction '{conviction}' was wrong. Thesis failed: {thesis}"
        )
    else:
        return f"{ticker} {action} expired without resolution. Thesis: {thesis}"


def _parse_utc(utc_str: str) -> float:
    """Parse ISO-8601 UTC string to epoch float. Returns 0 on failure."""
    try:
        import datetime
        dt = datetime.datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return 0.0
