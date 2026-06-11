"""Maintenance runner - feeds the dormant track-record / calibration / equity loops.

These data-pipeline jobs mutate local state files; they produce no human-facing
brief (that's run-claude-skill.ps1's job). Intended for a daily Scheduled Task
after US market close.

Two tiers:
  - No-WS jobs ALWAYS run: score open recommendations + resolve trade outcomes
    (market data via data_router + local jsonl - no Wealthsimple session needed).
  - WS-gated jobs run ONLY when a cached WS session restores: portfolio equity
    snapshot + positioning-history snapshot. Missing session => logged + skipped,
    never fatal (so the no-WS half still runs on a stale-token day).

Run:  backend/.venv/Scripts/python.exe backend/scripts/run_maintenance.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # put backend/ on sys.path

_LOG = Path.home() / ".aifolimizer" / "maintenance.log"


def _log(msg: str) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [maintenance] {msg}"
    print(line, flush=True)
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_no_ws_jobs() -> None:
    """Score recommendations + resolve trade outcomes. Market data + jsonl only."""
    from app.services import data_router, decision_memory, paper_trade

    try:
        res = paper_trade.score_recommendations()
        _log(f"score_recommendations: {res.get('scored', res)}")
    except Exception as e:
        _log(f"score_recommendations FAILED: {type(e).__name__}: {e}")

    try:
        records = decision_memory._load_all()
        open_tickers = list({r["ticker"] for r in records if r.get("outcome") == "open"})
        if not open_tickers:
            _log("resolve_trade_outcomes: no open decisions")
            return
        price_map: dict[str, float] = {}
        for ticker in open_tickers:
            try:
                q = data_router.get_quote(ticker)
                if q and q.get("price"):
                    price_map[ticker] = float(q["price"])
            except Exception:
                continue
        res = decision_memory.resolve_outcomes(price_map)
        _log(f"resolve_trade_outcomes: {res.get('resolved', res)}")
    except Exception as e:
        _log(f"resolve_trade_outcomes FAILED: {type(e).__name__}: {e}")


def run_ws_jobs() -> None:
    """Equity + positioning snapshots from the shared portfolio snapshot.

    Reads the cached snapshot (written by the MCP server on live fetches)
    instead of calling Wealthsimple - a bg WS call rotates the shared
    single-use refresh token and races interactive sessions into forced MFA.
    Skip cleanly if the snapshot isn't populated yet.
    """
    from app.services import portfolio_snapshot

    portfolio = portfolio_snapshot.read()
    if portfolio is None:
        _log("no portfolio snapshot - skipping equity + positioning snapshots (open aifolimizer in Claude)")
        return

    try:
        from app.services import alpha_attribution

        nlv = float(portfolio.summary.total_value or 0)
        if nlv > 0:
            res = alpha_attribution.snapshot_equity(round(nlv, 2))
            _log(f"snapshot_equity: nlv={nlv:.2f} {res}")
        else:
            _log("snapshot_equity: NLV is 0 - skipped")
    except Exception as e:
        _log(f"snapshot_equity FAILED: {type(e).__name__}: {e}")

    try:
        from app.services import positioning

        symbols = list(dict.fromkeys(p.symbol for p in portfolio.positions if p.symbol))[:15]
        if symbols:
            res = positioning.snapshot_to_history(symbols)
            _log(f"snapshot_positioning_history: {len(symbols)} symbols, {res}")
        else:
            _log("snapshot_positioning_history: no symbols - skipped")
    except Exception as e:
        _log(f"snapshot_positioning_history FAILED: {type(e).__name__}: {e}")


def main() -> int:
    _log("=== maintenance run start ===")
    run_no_ws_jobs()
    run_ws_jobs()
    _log("=== maintenance run end ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
