"""Run portfolio alert rules once, push via Telegram, log to history.

Usage:
  cd backend && .venv/Scripts/python scripts/run_alerts.py
  cd backend && .venv/Scripts/python scripts/run_alerts.py --account TFSA
  cd backend && .venv/Scripts/python scripts/run_alerts.py --dry-run

Schedule via cron / Task Scheduler / GitHub Actions for periodic checks.
Reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env. If unset, alerts only logged (no push).

Windows quick start (PowerShell, from backend/):
  ./scripts/schedule_alerts.ps1          # register every-30-min Mon-Fri task
  ./scripts/schedule_alerts.ps1 -DryRun  # same, but pass --dry-run
  ./scripts/schedule_alerts.ps1 -Unregister
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for sibling-script imports (mfa_notify)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND / ".env")

from app.services import wealthsimple, market_data  # noqa: E402
from app.services import alerts as alerts_svc  # noqa: E402
from app.services import positioning as positioning_svc  # noqa: E402


def _load_session() -> str:
    # Use the hardened restore path: it force-refreshes an expired access token
    # from the longer-lived refresh_token instead of dying on UNAUTHENTICATED.
    session_id = wealthsimple.restore_session()
    if not session_id:
        raise RuntimeError("No valid WS session. Run mcp_login.py to re-authenticate.")
    return session_id


async def _load_portfolio(account_id: str = ""):
    session_id = _load_session()
    session = wealthsimple.get_session(session_id)
    profile = session.get("profile") if session else None
    if not profile:
        raise RuntimeError("Session lost")

    per_account = session.get("per_account", {})
    if account_id and account_id in per_account:
        acc = per_account[account_id]
        cash = float(acc.get("cash_balance") or 0.0)
        nlv = float(acc.get("invested_value") or 0.0)
        upnl = float(acc.get("unrealized_pnl_cad") or 0.0)
        raw = await asyncio.to_thread(wealthsimple.get_positions, session_id, account_id)
    else:
        cash = sum(a.cash_balance for a in profile.accounts)
        nlv = sum(a.invested_value for a in profile.accounts)
        upnl = float(session.get("unrealized_pnl_cad") or 0.0)
        raw = await asyncio.to_thread(wealthsimple.get_all_positions, session_id)
    return market_data.enrich(raw, cash, nlv, upnl)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--account",
        default="",
        help="Account type filter (TFSA, RRSP, Non-Reg, Crypto). Empty = all.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate + log to history but skip Telegram push.",
    )
    ap.add_argument(
        "--price-drop-pct",
        type=float,
        default=5.0,
        help="Intraday drop threshold (positive number, default 5.0).",
    )
    ap.add_argument(
        "--min-severity",
        default="high",
        choices=["low", "medium", "high"],
        help="Only push alerts at/above this severity (default high = critical/major only). All are still logged.",
    )
    ap.add_argument(
        "--high-drop-pct",
        type=float,
        default=15.0,
        help="Single-day drop magnitude tagged 'high' (pushed). Raise to make pushes rarer (default 15.0).",
    )
    args = ap.parse_args()

    # Session check drives the once-per-expiry MFA heads-up. On a dead/
    # unrefreshable session, fire the (flag-deduped) notify and exit cleanly
    # instead of crashing the scheduled task every 30 min.
    import mfa_notify

    try:
        portfolio = asyncio.run(_load_portfolio(args.account))
    except RuntimeError as e:
        if "session" in str(e).lower() or "auth" in str(e).lower():
            mfa_notify.main()  # sends once per expiry event (24h reminder cap)
            print(json.dumps({"status": "session_expired", "mfa_notify": "fired"}))
            return 0
        raise
    mfa_notify.clear_flag()  # session valid → reset MFA cursor for next expiry

    triggered = alerts_svc.evaluate(
        portfolio,
        price_drop_pct=args.price_drop_pct,
        high_drop_pct=args.high_drop_pct,
    )
    from app.core.config import settings as _cfg

    tg_token = None if args.dry_run else _cfg.telegram_bot_token
    tg_chat = None if args.dry_run else _cfg.telegram_chat_id
    counts = alerts_svc.dispatch(
        triggered,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
        min_severity=args.min_severity,
    )

    # Piggyback: snapshot crowding scores for top 15 holdings (idempotent per day)
    top = sorted(portfolio.positions, key=lambda p: p.weight, reverse=True)[:15]
    top_symbols = [p.symbol for p in top]
    crowding_counts = positioning_svc.snapshot_to_history(top_symbols) if top_symbols else {}

    print(
        json.dumps(
            {
                "account": args.account or "all",
                "telegram": "off" if not tg_token else "on",
                **counts,
                "crowding_snapshot": crowding_counts,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
