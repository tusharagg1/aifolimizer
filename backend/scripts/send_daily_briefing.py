"""Compose the codified daily briefing and push it to Telegram.

Headless / no-LLM path: runs the deterministic codified skill set
(skill_runner.run_all_skills) which composes the daily-briefing snapshot
from portfolio-health / risk / macro / earnings / cash-deployment / etc.,
then formats a compact HTML digest and sends it via the Telegram bot.

Usage:
  cd backend && .venv/Scripts/python scripts/send_daily_briefing.py
  cd backend && .venv/Scripts/python scripts/send_daily_briefing.py --dry-run

Schedule via Task Scheduler (see schedule_alerts.ps1 for the pattern).
Reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env; --dry-run prints only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import date
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND / ".env")

from app.services import market_data, skill_runner, wealthsimple  # noqa: E402


def _load_session() -> str:
    session_id = wealthsimple.restore_session()
    if not session_id:
        raise RuntimeError("No valid WS session. Run mcp_login.py to re-authenticate.")
    return session_id


async def _load_portfolio(session_id: str):
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    if not profile:
        raise RuntimeError("Session lost")
    cash = sum(a.cash_balance for a in profile.accounts)
    nlv = sum(a.invested_value for a in profile.accounts)
    upnl = float(session.get("unrealized_pnl_cad") or 0.0)
    raw = await asyncio.to_thread(wealthsimple.get_all_positions, session_id)
    return market_data.enrich(raw, cash, nlv, upnl)


def _format_briefing(portfolio, briefing: dict) -> str:
    summary = briefing.get("summary") or {}
    next_action = summary.get("next_action")
    insights = briefing.get("key_insights") or []
    alerts = briefing.get("alerts") or []
    composed = summary.get("composed_from") or []

    ps = portfolio.summary
    lines = [f"📊 <b>aifolimizer Daily Briefing</b> - {date.today().isoformat()}"]
    lines.append(f"NLV ${ps.total_value:,.0f} | Return {ps.total_return_pct:+.1f}% | Cash ${ps.cash_available:,.0f}")
    if next_action:
        lines.append(f"\n⚡ <b>Next action:</b> {next_action}")
    if insights:
        lines.append("\n🔑 <b>Insights</b>")
        lines += [f"• {i}" for i in insights[:6]]
    if alerts:
        lines.append(f"\n⚠️ <b>Alerts ({len(alerts)})</b>")
        for a in alerts[:6]:
            title = a.get("title") or a.get("message") or a.get("source") or "alert"
            lines.append(f"• {title}")
    lines.append(f"\n<i>Composed from: {', '.join(composed) or 'no snapshots'}</i>")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Compose + print only, skip Telegram push.")
    args = ap.parse_args()

    session_id = _load_session()
    portfolio = asyncio.run(_load_portfolio(session_id))

    tenant = hashlib.sha1(session_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    out = skill_runner.run_all_skills(portfolio, tenant_id=tenant)
    briefing = out.get("daily-briefing") or {}
    if briefing.get("status") == "error":
        print(f"briefing compose error: {briefing.get('error')}", file=sys.stderr)
        return 1

    text = _format_briefing(portfolio, briefing)

    if args.dry_run:
        print(text)
        return 0

    from app.core.config import settings as _cfg

    if not (_cfg.telegram_bot_token and _cfg.telegram_chat_id):
        print("Telegram not configured; printing instead:\n" + text)
        return 0

    import httpx

    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{_cfg.telegram_bot_token}/sendMessage",
            json={"chat_id": _cfg.telegram_chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10.0,
        )
        r.raise_for_status()
        print("daily briefing pushed to Telegram")
        return 0
    except Exception as e:
        print(f"telegram push failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Windows consoles default to cp1252 and choke on emoji in --dry-run output.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            # Stream not reconfigurable - emoji may garble but output still flows.
            pass
    sys.exit(main())
