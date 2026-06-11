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
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for sibling-script imports (mfa_notify)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND / ".env")

from app.services import portfolio_snapshot, data_router  # noqa: E402
from app.services import alerts as alerts_svc  # noqa: E402
from app.services import positioning as positioning_svc  # noqa: E402


def _load_portfolio(account_id: str = ""):
    # Read the shared portfolio snapshot (written by the MCP server on its live
    # fetches) INSTEAD of calling Wealthsimple. A bg WS call would rotate the
    # shared single-use refresh token and race interactive sessions into forced
    # MFA - the documented "random expiry". No snapshot => skip (never hit WS).
    portfolio = portfolio_snapshot.read(account_id)
    if portfolio is None:
        raise RuntimeError("No portfolio snapshot - open aifolimizer in Claude to populate it.")
    _overlay_live_prices(portfolio)
    return portfolio


def _overlay_live_prices(portfolio) -> None:
    """Refresh day_change_pct from real-time public-ticker quotes so price
    alerts fire on live moves, not the snapshot-time day change. Holdings come
    from the WS snapshot (cached); prices come from public market data - no WS.
    Best-effort: a quote miss leaves the snapshot value for that symbol intact.
    """
    symbols = [p.symbol for p in portfolio.positions if p.symbol]
    if not symbols:
        return
    try:
        quotes = data_router.get_quotes_batch(symbols)
    except Exception as e:
        print(json.dumps({"status": "live_price_refresh_failed", "detail": f"{type(e).__name__}: {e}"}))
        return
    refreshed = []
    for p in portfolio.positions:
        q = quotes.get(p.symbol)
        dcp = q.get("day_change_pct") if q else None
        if dcp is not None:
            refreshed.append(p.model_copy(update={"day_change_pct": round(float(dcp), 2)}))
        else:
            refreshed.append(p)
    portfolio.positions = refreshed


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

    # Holdings come from the shared WS snapshot (no live WS login here - that
    # would rotate the shared token). If the snapshot isn't populated yet, skip
    # cleanly instead of crashing the 30-min scheduled task.
    try:
        portfolio = _load_portfolio(args.account)
    except RuntimeError as e:
        print(json.dumps({"status": "no_snapshot", "detail": str(e)}))
        return 0

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
