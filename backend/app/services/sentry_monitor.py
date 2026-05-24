"""Sentry issue digest — pull top errors for self-learn loop.

Read-only Sentry API client. Resolves recent unresolved issues with
counts, last-seen, stack frames. Used by:
  - MCP tool (`get_sentry_issues`) for Claude-on-demand triage
  - Scheduled RQ job (`sentry_digest`) for hourly passive monitoring
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("aifolimizer.sentry_monitor")

_API_BASE = "https://sentry.io/api/0"
_TIMEOUT = httpx.Timeout(10.0)


@dataclass
class SentryIssue:
    id: str
    short_id: str
    title: str
    culprit: str | None
    level: str
    status: str
    count: int
    user_count: int
    first_seen: str
    last_seen: str
    permalink: str
    metadata: dict[str, Any]
    platform: str | None


def _auth_headers() -> dict[str, str]:
    if not settings.sentry_auth_token:
        raise RuntimeError("SENTRY_AUTH_TOKEN not configured")
    return {"Authorization": f"Bearer {settings.sentry_auth_token}"}


def list_issues(
    *,
    query: str = "is:unresolved",
    limit: int = 25,
    environment: str | None = None,
) -> list[SentryIssue]:
    if not settings.sentry_org or not settings.sentry_project:
        raise RuntimeError("SENTRY_ORG / SENTRY_PROJECT not configured")

    params: dict[str, Any] = {
        "query": query,
        "limit": limit,
        "sort": "freq",
        "statsPeriod": "24h",
    }
    if environment:
        params["environment"] = environment

    url = f"{_API_BASE}/projects/{settings.sentry_org}/{settings.sentry_project}/issues/"
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(url, headers=_auth_headers(), params=params)
        r.raise_for_status()
        raw = r.json()

    issues = []
    for it in raw:
        issues.append(
            SentryIssue(
                id=it["id"],
                short_id=it.get("shortId", ""),
                title=it.get("title", ""),
                culprit=it.get("culprit"),
                level=it.get("level", "error"),
                status=it.get("status", "unresolved"),
                count=int(it.get("count", 0)),
                user_count=int(it.get("userCount", 0)),
                first_seen=it.get("firstSeen", ""),
                last_seen=it.get("lastSeen", ""),
                permalink=it.get("permalink", ""),
                metadata=it.get("metadata", {}),
                platform=it.get("platform"),
            )
        )
    return issues


def get_issue_detail(issue_id: str) -> dict[str, Any]:
    url = f"{_API_BASE}/issues/{issue_id}/"
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(url, headers=_auth_headers())
        r.raise_for_status()
        return r.json()


def get_latest_event(issue_id: str) -> dict[str, Any]:
    """Latest event = newest stack trace + breadcrumbs for this issue."""
    url = f"{_API_BASE}/issues/{issue_id}/events/latest/"
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(url, headers=_auth_headers())
        r.raise_for_status()
        return r.json()


def extract_stack_summary(event: dict[str, Any], max_frames: int = 8) -> list[dict[str, Any]]:
    """Pull just the in-app frames from an event — what Claude needs to reason."""
    exc = event.get("entries", [])
    for entry in exc:
        if entry.get("type") != "exception":
            continue
        values = entry.get("data", {}).get("values", [])
        if not values:
            continue
        frames = values[-1].get("stacktrace", {}).get("frames", [])
        in_app = [f for f in frames if f.get("inApp")][-max_frames:]
        return [
            {
                "file": f.get("filename"),
                "function": f.get("function"),
                "line": f.get("lineNo"),
                "context": f.get("contextLine", "").strip(),
            }
            for f in in_app
        ]
    return []


def build_digest(limit: int = 10) -> dict[str, Any]:
    """One-shot digest for daily briefing / scheduled job."""
    issues = list_issues(limit=limit)
    items = []
    for iss in issues:
        item = asdict(iss)
        try:
            ev = get_latest_event(iss.id)
            item["stack"] = extract_stack_summary(ev)
        except Exception as e:
            logger.warning("sentry event fetch failed for %s: %s", iss.short_id, e)
            item["stack"] = []
        items.append(item)
    return {
        "org": settings.sentry_org,
        "project": settings.sentry_project,
        "count": len(items),
        "issues": items,
    }
