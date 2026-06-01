"""Champion-challenger shadow recommendations (Phase 12).

Bare-bones placeholder. Full DB-backed champion-challenger needs a new
`shadow_recs` Postgres table and a parallel scoring path; out of scope for
this audit pass. This module logs every recommendation alongside the
weights version that produced it, so a follow-up backfill can reconstruct
challenger performance against the champion.

Today:
  * `log_shadow(rec, weights_version)` — append to a JSONL beside the
    main recommendations file.
  * `compare_versions(window_days=30)` — rolling profit-factor delta per
    weights version; intended to drive auto-rollback when ready.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.shadow_recs")

_CTX = Path(__file__).resolve().parents[2] / ".claude" / "context"
_SHADOW_FILE = _CTX / "shadow_recs.jsonl"


def log_shadow(rec: dict, weights_version: int | None = None) -> None:
    """Append one recommendation snapshot tagged with the weights version."""
    payload = {
        "ts": time.time(),
        "weights_version": weights_version,
        "rec": rec,
    }
    try:
        _CTX.mkdir(parents=True, exist_ok=True)
        with _SHADOW_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as e:
        _LOG.warning("shadow_recs: log failed: %s", e)


def compare_versions(window_days: int = 30) -> dict[str, Any]:
    """Return per-version aggregate stats over the trailing window.

    Stats are placeholder — full marking-to-market per version belongs in
    the upcoming `shadow_recs` Postgres path. Useful today as a sanity-
    check that the challenger logging is firing.
    """
    if not _SHADOW_FILE.exists():
        return {"status": "empty"}

    cutoff = time.time() - window_days * 86400
    by_version: dict[int | None, int] = {}
    for line in _SHADOW_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("ts", 0) < cutoff:
            continue
        v = row.get("weights_version")
        by_version[v] = by_version.get(v, 0) + 1

    return {
        "status": "ok",
        "window_days": window_days,
        "counts_by_version": {str(k): v for k, v in by_version.items()},
        "todo": (
            "Wire log_shadow() into recommendations.score; backfill "
            "`shadow_recs` Postgres table for true champion-challenger."
        ),
    }
