"""File-based persistent investor memory for cross-session context.

Stores preferences, insights, and rules as JSON files in ~/.aifolimizer/memory/.
Keyword scorer (metadata hits 2x body hits) retrieves top-k relevant memories
each session — no database or external deps required.

Adapted from Vibe-Trading (HKUDS/Vibe-Trading, MIT License).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

_MEMORY_DIR = Path.home() / ".aifolimizer" / "memory"
_SCHEMA_VERSION = "1.0"
_VALID_TYPES = {"preference", "insight", "rule", "note", "observation"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip())[:32]


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower()))


def remember(
    memory_type: str,
    content: str,
    tags: list[str] | None = None,
) -> dict:
    """Store a memory.

    memory_type: preference | insight | rule | note | observation
    content: plain-English description
    tags: optional list of keyword tags for retrieval boosting
    """
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    mtype = memory_type if memory_type in _VALID_TYPES else "note"
    filename = f"{_slug(mtype)}_{_slug(content[:28])}_{int(time.time())}.json"
    path = _MEMORY_DIR / filename
    record = {
        "schema_version": _SCHEMA_VERSION,
        "type": mtype,
        "content": content,
        "tags": tags or [],
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(record, indent=2))
    return {"stored": True, "type": mtype, "path": str(path)}


def recall(query: str, top_k: int = 5) -> list[dict]:
    """Keyword-scored retrieval. Metadata matches weighted 2x body matches."""
    if not _MEMORY_DIR.exists():
        return []

    q_tokens = _tokenize(query)
    scored: list[tuple[int, dict]] = []

    for p in _MEMORY_DIR.glob("*.json"):
        try:
            record = json.loads(p.read_text())
        except Exception:
            continue

        body_score = len(q_tokens & _tokenize(record.get("content", "")))
        tag_score = len(q_tokens & _tokenize(" ".join(record.get("tags", []))))
        type_score = len(q_tokens & _tokenize(record.get("type", "")))
        total = body_score + 2 * tag_score + 2 * type_score

        if total > 0:
            scored.append((total, record))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "type": r.get("type"),
            "content": r.get("content"),
            "tags": r.get("tags", []),
            "created_utc": r.get("created_utc"),
            "relevance_score": s,
        }
        for s, r in scored[:top_k]
    ]


def list_memories(memory_type: str | None = None) -> list[dict]:
    """List all stored memories, optionally filtered by type."""
    if not _MEMORY_DIR.exists():
        return []
    records = []
    for p in _MEMORY_DIR.glob("*.json"):
        try:
            record = json.loads(p.read_text())
            if memory_type and record.get("type") != memory_type:
                continue
            records.append(
                {
                    "type": record.get("type"),
                    "content": record.get("content"),
                    "tags": record.get("tags", []),
                    "created_utc": record.get("created_utc"),
                }
            )
        except Exception:
            continue
    records.sort(key=lambda r: r.get("created_utc", ""), reverse=True)
    return records


def forget(query: str) -> dict:
    """Delete memories whose content contains query as substring."""
    if not _MEMORY_DIR.exists():
        return {"deleted": 0, "query": query}
    deleted = 0
    for p in _MEMORY_DIR.glob("*.json"):
        try:
            record = json.loads(p.read_text())
            if query.lower() in record.get("content", "").lower():
                p.unlink()
                deleted += 1
        except Exception:
            continue
    return {"deleted": deleted, "query": query}
