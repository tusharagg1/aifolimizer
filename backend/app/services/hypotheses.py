"""Hypothesis registry: durable investment theses with lifecycle status.

Complements decision_memory (which tracks EXECUTED trades) by capturing
un-executed or in-flight theses - "I believe X because Y; confirmed if Z,
refuted if W" - so research converts to action and ideas are not
re-litigated. Stored as JSONL at ~/.aifolimizer/hypotheses.jsonl.

Status lifecycle: open -> confirmed | refuted | expired.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FILE = Path.home() / ".aifolimizer" / "hypotheses.jsonl"
_VALID_STATUS = {"open", "confirmed", "refuted", "expired"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_all() -> list[dict]:
    if not _FILE.exists():
        return []
    out: list[dict] = []
    for line in _FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _save_all(records: list[dict]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with _FILE.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def log_hypothesis(
    thesis: str,
    ticker: str = "",
    acceptance_criteria: str = "",
    invalidation_criteria: str = "",
    horizon_days: int = 90,
    linked_run_card: str = "",
) -> dict:
    """Record a new open thesis. Returns the stored record (with its id)."""
    if not thesis.strip():
        return {"error": "empty_thesis"}
    rec = {
        "id": uuid.uuid4().hex[:12],
        "created_utc": _now_iso(),
        "ticker": ticker.upper(),
        "thesis": thesis.strip(),
        "acceptance_criteria": acceptance_criteria.strip(),
        "invalidation_criteria": invalidation_criteria.strip(),
        "horizon_days": int(horizon_days),
        "linked_run_card": linked_run_card,
        "status": "open",
        "resolved_utc": None,
        "resolution_note": None,
    }
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with _FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def list_hypotheses(status: str = "", ticker: str = "") -> list[dict]:
    """List theses, newest first. Filter by status and/or ticker."""
    recs = _load_all()
    if status:
        recs = [r for r in recs if r.get("status") == status]
    if ticker:
        t = ticker.upper()
        recs = [r for r in recs if r.get("ticker") == t]
    return sorted(recs, key=lambda r: r.get("created_utc", ""), reverse=True)


def resolve_hypothesis(hypothesis_id: str, status: str, resolution_note: str = "") -> dict:
    """Mark a thesis confirmed/refuted/expired. Returns the updated record."""
    if status not in _VALID_STATUS or status == "open":
        return {"error": "invalid_status", "valid": sorted(_VALID_STATUS - {"open"})}
    recs = _load_all()
    found: dict[str, Any] | None = None
    for r in recs:
        if r.get("id") == hypothesis_id:
            r["status"] = status
            r["resolved_utc"] = _now_iso()
            r["resolution_note"] = resolution_note.strip()
            found = r
            break
    if not found:
        return {"error": "not_found", "id": hypothesis_id}
    _save_all(recs)
    return found


def expire_stale() -> dict:
    """Auto-expire open theses past their horizon. Returns count expired."""
    recs = _load_all()
    now = time.time()
    expired = 0
    for r in recs:
        if r.get("status") != "open":
            continue
        try:
            created = datetime.strptime(r["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
            created = created.replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, KeyError):
            continue
        if now - created > r.get("horizon_days", 90) * 86400:
            r["status"] = "expired"
            r["resolved_utc"] = _now_iso()
            r["resolution_note"] = "auto-expired past horizon"
            expired += 1
    if expired:
        _save_all(recs)
    return {"expired": expired}
