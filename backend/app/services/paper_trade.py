"""Forward paper-trade logger and scorer.

Two concerns:
1. Log — append every skill recommendation to recommendations.jsonl.
   Each record: date, skill, ticker, action (BUY/SELL/HOLD), entry_price,
   conviction (HIGH/MED/LOW), target_pct, stop_pct, rationale_hash.
   Called by the skill runner or manually via MCP tool `log_recommendation`.

2. Score — mark-to-market every open recommendation daily.
   Reads recommendations.jsonl, fetches current price via data_router,
   computes unrealized P&L, win/loss. Writes to scored_recommendations.jsonl.
   Called daily (schedule or manual MCP `score_recommendations`).

Output paths (gitignored):
  .claude/context/recommendations.jsonl
  .claude/context/scored_recommendations.jsonl
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path

from app.services import data_router

_CTX = Path(__file__).resolve().parents[2] / ".claude" / "context"
_REC_FILE = _CTX / "recommendations.jsonl"
_SCORED_FILE = _CTX / "scored_recommendations.jsonl"

_VALID_ACTIONS = {"BUY", "SELL", "HOLD", "ADD", "TRIM"}
_VALID_CONV = {"HIGH", "MED", "LOW"}


def log_recommendation(
    skill: str,
    ticker: str,
    action: str,
    conviction: str,
    rationale: str,
    target_pct: float | None = None,
    stop_pct: float | None = None,
    account: str | None = None,
) -> dict:
    """Append one recommendation to recommendations.jsonl.

    Returns the written record so MCP can echo it back to Claude.
    entry_price is fetched live from data_router so it's the actual price
    at recommendation time.
    """
    action = action.upper()
    conviction = conviction.upper()
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be one of {_VALID_ACTIONS}")
    if conviction not in _VALID_CONV:
        raise ValueError(f"conviction must be one of {_VALID_CONV}")

    try:
        q = data_router.get_quote(ticker.upper())
        entry_price = q.get("price") or 0.0
        source = q.get("source", "unknown")
    except Exception:
        entry_price = 0.0
        source = "unavailable"

    rec = {
        "id": _make_id(skill, ticker, action),
        "date": date.today().isoformat(),
        "ts": time.time(),
        "skill": skill,
        "ticker": ticker.upper(),
        "action": action,
        "conviction": conviction,
        "entry_price": round(entry_price, 4),
        "price_source": source,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "account": account,
        "rationale_hash": hashlib.sha256(rationale.encode()).hexdigest()[:16],
        "status": "open",
        "exit_price": None,
        "exit_date": None,
        "return_pct": None,
        "win": None,
    }
    _CTX.mkdir(parents=True, exist_ok=True)
    with _REC_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def score_recommendations(max_age_days: int = 90) -> dict:
    """Mark-to-market all open recommendations, close stops/targets.

    Writes updated records to scored_recommendations.jsonl.
    Returns summary stats.
    """
    if not _REC_FILE.exists():
        return {"error": "no recommendations.jsonl found"}

    recs = [json.loads(line) for line in _REC_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    cutoff_ts = time.time() - max_age_days * 86400
    recs = [r for r in recs if r.get("ts", 0) >= cutoff_ts]

    open_recs = [r for r in recs if r.get("status") == "open"]
    scored: list[dict] = []
    skipped = 0

    for rec in open_recs:
        ticker = rec["ticker"]
        action = rec["action"]
        entry = rec.get("entry_price") or 0.0

        if entry <= 0:
            skipped += 1
            continue

        try:
            q = data_router.get_quote(ticker)
            current = q.get("price") or 0.0
        except Exception:
            skipped += 1
            continue

        if current <= 0:
            skipped += 1
            continue

        if action in ("BUY", "ADD"):
            ret_pct = (current - entry) / entry * 100
        elif action in ("SELL", "TRIM"):
            ret_pct = (entry - current) / entry * 100
        else:
            ret_pct = 0.0

        stop = rec.get("stop_pct")
        target = rec.get("target_pct")
        status = "open"

        if stop is not None and ret_pct <= -abs(stop):
            status = "stopped_out"
        elif target is not None and ret_pct >= abs(target):
            status = "target_hit"

        scored_rec = dict(rec)
        scored_rec.update({
            "current_price": round(current, 4),
            "unrealized_pct": round(ret_pct, 2),
            "win": ret_pct > 0,
            "status": status,
            "scored_at": time.time(),
        })
        if status in ("stopped_out", "target_hit"):
            scored_rec["exit_price"] = round(current, 4)
            scored_rec["exit_date"] = date.today().isoformat()
            scored_rec["return_pct"] = round(ret_pct, 2)

        scored.append(scored_rec)

    _CTX.mkdir(parents=True, exist_ok=True)
    with _SCORED_FILE.open("w", encoding="utf-8") as f:
        for r in scored:
            f.write(json.dumps(r) + "\n")

    return _summary(scored, skipped)


def get_track_record(windows: list[int] | None = None) -> dict:
    """Rolling win-rate and P&L for 7/30/90 day windows from scored file."""
    if windows is None:
        windows = [7, 30, 90]
    if not _SCORED_FILE.exists():
        return {"error": "no scored_recommendations.jsonl — run score_recommendations first"}

    recs = [json.loads(line) for line in _SCORED_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    now = time.time()
    out: dict = {"windows": {}}

    for days in windows:
        cutoff = now - days * 86400
        window_recs = [r for r in recs if r.get("ts", 0) >= cutoff]
        out["windows"][f"{days}d"] = _summary(window_recs, 0)

    out["total_logged"] = len(recs)
    out["as_of"] = now
    return out


def _summary(scored: list[dict], skipped: int) -> dict:
    if not scored:
        return {"count": 0, "skipped": skipped, "win_rate_pct": None, "avg_return_pct": None}
    wins = sum(1 for r in scored if r.get("win"))
    returns = [r["unrealized_pct"] for r in scored if r.get("unrealized_pct") is not None]
    by_conviction: dict[str, dict] = {}
    for r in scored:
        c = r.get("conviction", "?")
        if c not in by_conviction:
            by_conviction[c] = {"count": 0, "wins": 0, "returns": []}
        by_conviction[c]["count"] += 1
        if r.get("win"):
            by_conviction[c]["wins"] += 1
        if r.get("unrealized_pct") is not None:
            by_conviction[c]["returns"].append(r["unrealized_pct"])

    conv_stats = {}
    for c, d in by_conviction.items():
        conv_stats[c] = {
            "count": d["count"],
            "win_rate_pct": round(d["wins"] / d["count"] * 100, 1),
            "avg_return_pct": round(sum(d["returns"]) / len(d["returns"]), 2) if d["returns"] else None,
        }

    return {
        "count": len(scored),
        "skipped": skipped,
        "win_rate_pct": round(wins / len(scored) * 100, 1),
        "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "by_conviction": conv_stats,
    }


def _make_id(skill: str, ticker: str, action: str) -> str:
    raw = f"{skill}:{ticker}:{action}:{date.today().isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
