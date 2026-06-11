"""Qualitative trade journal - felt-state capture at entry/exit + insights.

Complements two existing systems without overlap:
  - decision_memory.py  → facts (price, conviction label, thesis, outcome)
  - shadow_account.py    → behavioral biases derived from price/date only

This captures the psychological layer those cannot: emotion, conviction
source, self-rated confidence, plan adherence, and the felt lesson. Insights
cross-tab that felt-state against realized outcomes (joined from decisions)
to surface which emotional states actually lose money - the empirical answer
to "the one journaling question".

Stored as JSONL at ~/.aifolimizer/journal.jsonl - parallel to decisions.jsonl.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import app.services.decision_memory as decision_memory

_JOURNAL_FILE = Path.home() / ".aifolimizer" / "journal.jsonl"
_SCHEMA_VERSION = "1.0"

_EMOTIONS = {"calm", "fomo", "fear", "revenge", "conviction", "bored", "uncertain"}
_CONVICTION_SOURCES = {"thesis", "chart", "tip", "social", "news", "gut"}
_SURPRISE_LEVELS = {"expected", "mild_surprise", "shock"}


def _load_all() -> list[dict]:
    if not _JOURNAL_FILE.exists():
        return []
    records: list[dict] = []
    for line in _JOURNAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _save_all(records: list[dict]) -> None:
    _JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    _JOURNAL_FILE.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _append_one(record: dict) -> None:
    _JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def log_entry(
    ticker: str,
    emotion: str,
    conviction_source: str,
    confidence_1to5: int,
    plan_intended: str,
    felt_note: str,
    pre_trade_check_passed: bool,
    linked_decision_utc: str = "",
) -> dict:
    """Capture felt-state at trade entry.

    emotion: calm | fomo | fear | revenge | conviction | bored | uncertain
    conviction_source: thesis | chart | tip | social | news | gut
    confidence_1to5: self-rated conviction, 1 (none) .. 5 (max)
    """
    if emotion not in _EMOTIONS:
        raise ValueError(f"emotion must be one of {sorted(_EMOTIONS)}, got {emotion!r}")
    if conviction_source not in _CONVICTION_SOURCES:
        raise ValueError(f"conviction_source must be one of {sorted(_CONVICTION_SOURCES)}, got {conviction_source!r}")
    if not isinstance(confidence_1to5, int) or not 1 <= confidence_1to5 <= 5:
        raise ValueError(f"confidence_1to5 must be int 1..5, got {confidence_1to5!r}")

    record = {
        "schema_version": _SCHEMA_VERSION,
        "ticker": ticker.upper(),
        "phase": "entry",
        "emotion": emotion,
        "conviction_source": conviction_source,
        "confidence_1to5": confidence_1to5,
        "plan_intended": plan_intended,
        "felt_note": felt_note,
        "pre_trade_check_passed": bool(pre_trade_check_passed),
        "linked_decision_utc": linked_decision_utc,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # exit fields - filled by log_exit
        "plan_followed": None,
        "exit_emotion": None,
        "outcome_surprise": None,
        "lesson": None,
        "exit_utc": None,
    }
    _append_one(record)
    return {"logged": True, "ticker": ticker.upper(), "emotion": emotion}


def log_exit(
    ticker: str,
    plan_followed: bool,
    exit_emotion: str,
    outcome_surprise: str,
    lesson: str,
) -> dict:
    """Reconcile the newest open (entry-phase) journal record for a ticker.

    outcome_surprise: expected | mild_surprise | shock
    """
    if exit_emotion not in _EMOTIONS:
        raise ValueError(f"exit_emotion must be one of {sorted(_EMOTIONS)}, got {exit_emotion!r}")
    if outcome_surprise not in _SURPRISE_LEVELS:
        raise ValueError(f"outcome_surprise must be one of {sorted(_SURPRISE_LEVELS)}, got {outcome_surprise!r}")

    ticker = ticker.upper()
    records = _load_all()
    open_idx = [i for i, r in enumerate(records) if r.get("ticker") == ticker and r.get("phase") == "entry"]
    if not open_idx:
        return {"reconciled": False, "ticker": ticker}

    rec = records[max(open_idx, key=lambda i: records[i].get("created_utc", ""))]
    rec["phase"] = "exit"
    rec["plan_followed"] = bool(plan_followed)
    rec["exit_emotion"] = exit_emotion
    rec["outcome_surprise"] = outcome_surprise
    rec["lesson"] = lesson
    rec["exit_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_all(records)
    return {"reconciled": True, "ticker": ticker}


def _outcome_by_ticker() -> dict[str, str]:
    """Map ticker -> 'win' | 'loss' from resolved decisions (nearest by recency)."""
    out: dict[str, str] = {}
    for rec in decision_memory._load_all():
        oc = rec.get("outcome")
        if oc not in {"target_hit", "stop_hit"}:
            continue
        out[rec["ticker"]] = "win" if oc == "target_hit" else "loss"
    return out


def _rate_bucket(items: list[tuple[str, int]]) -> dict[str, dict]:
    """items: list of (win_or_loss, confidence). Group caller does the keying."""
    n = len(items)
    wins = sum(1 for r, _ in items if r == "win")
    return {
        "n": n,
        "wins": wins,
        "win_rate_pct": round(wins / n * 100, 1) if n else 0.0,
    }


def get_insights() -> dict:
    """Cross-tab felt-state against realized outcomes joined from decisions.

    Surfaces which emotions / conviction sources actually lose money, and
    whether self-rated confidence is calibrated (higher on wins than losses).
    """
    entries = _load_all()
    outcomes = _outcome_by_ticker()

    # Only entries whose ticker has a resolved win/loss can be scored.
    scored = [(e, outcomes[e["ticker"]]) for e in entries if e.get("ticker") in outcomes]

    by_emotion: dict[str, list[tuple[str, int]]] = {}
    by_source: dict[str, list[tuple[str, int]]] = {}
    win_conf: list[int] = []
    loss_conf: list[int] = []

    for e, result in scored:
        conf = e.get("confidence_1to5") or 0
        by_emotion.setdefault(e.get("emotion", "unknown"), []).append((result, conf))
        by_source.setdefault(e.get("conviction_source", "unknown"), []).append((result, conf))
        (win_conf if result == "win" else loss_conf).append(conf)

    calibration = {
        "avg_confidence_wins": round(sum(win_conf) / len(win_conf), 2) if win_conf else None,
        "avg_confidence_losses": round(sum(loss_conf) / len(loss_conf), 2) if loss_conf else None,
        "calibrated": bool(
            win_conf and loss_conf and (sum(win_conf) / len(win_conf)) > (sum(loss_conf) / len(loss_conf))
        ),
    }

    unreconciled = sum(1 for e in entries if e.get("phase") == "entry")

    return {
        "total_entries": len(entries),
        "scored_entries": len(scored),
        "open_unreconciled": unreconciled,
        "win_rate_by_emotion": {k: _rate_bucket(v) for k, v in by_emotion.items()},
        "win_rate_by_conviction_source": {k: _rate_bucket(v) for k, v in by_source.items()},
        "confidence_calibration": calibration,
    }
