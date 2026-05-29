"""Free-LLM fallback for a skill when `claude -p` is unavailable (Pro lost / not
logged in / no API key).

Resolves the agent_registry runner for <skill> and runs it via the free-provider
LLM route (skill_llm_runner → llm_router), then prints a plain-text rendering of
the snapshot to stdout for Telegram. This is the resilience tier — degraded
quality vs Claude, but keeps the brief flowing.

Restores the WS session from disk to build the runner context (tenant_hash +
session_id). Only skills with a registered backend runner can fall back; new
composer skills (top-trades-today, position-review) have none and exit 4.

Exit: 0 produced output · 4 no runner / no session · 5 runner error.

Usage:
    python scripts/run_skill_fallback.py daily-briefing
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

# Load .env before any service imports that read config at module level.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_BACKEND_DIR / ".env", override=False)

from app.services import agent_registry as ar  # noqa: E402
from app.services import wealthsimple  # noqa: E402


def _render(snap: dict) -> str:
    """Flatten a skill snapshot dict into readable plain text."""
    lines: list[str] = []
    summary = snap.get("summary")
    if isinstance(summary, dict):
        for k, v in summary.items():
            lines.append(f"{k}: {v}")
    elif summary:
        lines.append(str(summary))
    for section in ("key_insights", "actionable", "alerts"):
        items = snap.get(section) or []
        if items:
            lines.append(f"\n{section}:")
            for it in items:
                lines.append(f"- {it}")
    return "\n".join(lines).strip()


async def _run(skill: str) -> tuple[dict | None, int]:
    spec = ar.get_agent(skill)
    if spec is None:
        return None, 4
    runner = ar.resolve_runner(spec)
    if runner is None:
        return None, 4
    sid = await asyncio.to_thread(wealthsimple.restore_session)
    if not sid:
        return None, 4
    ctx = {
        "session_id": sid,
        "tenant_hash": hashlib.sha1(sid.encode("utf-8")).hexdigest()[:16],
    }
    if asyncio.iscoroutinefunction(runner):
        snap = await runner(ctx)
    else:
        snap = await asyncio.to_thread(runner, ctx)
    return snap, 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Free-LLM fallback for a skill.")
    ap.add_argument("skill", help="skill name (must have an agent_registry runner)")
    args = ap.parse_args()
    try:
        snap, code = asyncio.run(_run(args.skill))
        if code != 0 or not snap:
            print(f"no free-LLM fallback available for '{args.skill}'", file=sys.stderr)
            return 4
        text = _render(snap)
    except Exception as e:  # noqa: BLE001 — report and signal failure
        print(f"fallback runner error: {e}", file=sys.stderr)
        return 5
    if not text:
        print(f"fallback produced no content for '{args.skill}'", file=sys.stderr)
        return 4
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
