"""User-defined watchlist — persisted to backend/watchlist.json.

Watchlist items are analyzed using the same multi-signal engine as portfolio
positions but with no held size (weight=0, cost=N/A).
Actions remapped: SELL→PASS, HOLD→WATCH (can't exit an unowned position).
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path

from app.services import fundamentals as fund_svc
from app.services import technicals as tech_svc
from app.services.fundamentals import get_earnings_expected_moves
from app.services.macro import market_breadth
from app.services.recommendations import _get_sentiment, _score_position
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.watchlist")


# Persist in the user's home so watchlist survives across branch switches,
# git worktrees, repo moves and clean checkouts. The legacy in-repo location
# (backend/watchlist.json) is migrated on first read if it still exists.
_WATCHLIST_PATH = Path.home() / ".aifolimizer" / "watchlist.json"
_LEGACY_PATH = Path(__file__).parent.parent.parent / "watchlist.json"


def _migrate_legacy() -> None:
    """One-time copy from repo-adjacent watchlist.json to ~/.aifolimizer/.

    Idempotent — runs only when the new path is missing and the legacy file
    exists. Leaves the legacy file in place; user can delete manually.
    """
    if _WATCHLIST_PATH.exists() or not _LEGACY_PATH.exists():
        return
    try:
        _WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WATCHLIST_PATH.write_text(
            _LEGACY_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    except Exception as e:
        _LOG.warning(f"[watchlist] migration failed: {e}")


_REC_CACHE: dict[str, tuple[list, float]] = {}
_REC_CACHE_TTL = 1800  # 30 minutes

_ACTION_ORDER = {"BUY": 0, "WATCH": 1, "PASS": 2}


def load_watchlist() -> list[dict]:
    _migrate_legacy()
    if not _WATCHLIST_PATH.exists():
        return []
    try:
        return json.loads(_WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    _WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WATCHLIST_PATH.write_text(
        json.dumps(items, indent=2), encoding="utf-8",
    )


def add_symbol(symbol: str, notes: str = "") -> list[dict]:
    sym = symbol.strip().upper()
    items = load_watchlist()
    if any(i["symbol"] == sym for i in items):
        return items
    items.append({"symbol": sym, "notes": notes, "added_at": time.strftime("%Y-%m-%d")})
    _save(items)
    return items


def remove_symbol(symbol: str) -> list[dict]:
    sym = symbol.strip().upper()
    items = [i for i in load_watchlist() if i["symbol"] != sym]
    _save(items)
    return items


def get_watchlist_recommendations() -> list[dict]:
    items = load_watchlist()
    if not items:
        return []

    cache_key = ",".join(sorted(i["symbol"] for i in items))
    entry = _REC_CACHE.get(cache_key)
    if entry and time.time() - entry[1] < _REC_CACHE_TTL:
        return entry[0]

    symbols = [i["symbol"] for i in items]
    notes_map = {i["symbol"]: i.get("notes", "") for i in items}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tech_f = pool.submit(tech_svc.get_technicals, symbols)
        fund_f = pool.submit(fund_svc.get_fundamentals, symbols)
        macro_f = pool.submit(market_breadth)
        sent_fs = {sym: pool.submit(_get_sentiment, sym) for sym in symbols}
        tech_data = tech_f.result()
        fund_data = fund_f.result()
        macro_data = macro_f.result()
        sent_data = {sym: f.result() for sym, f in sent_fs.items()}

    try:
        em_data = get_earnings_expected_moves(symbols, fund_data, tech_data)
    except Exception:
        em_data = {}

    results = []
    for sym in symbols:
        fd = fund_data.get(sym) or {}
        currency = "CAD" if (sym.endswith(".TO") or sym.endswith(".V")) else "USD"
        position = {
            "symbol": sym,
            "name": fd.get("longName") or fd.get("shortName") or sym,
            "currency": currency,
            "asset_class": fd.get("quoteType", "stock").lower(),
            "weight": 0.0,
            "market_value_cad": 0.0,
            "total_return_pct": 0.0,
            "quantity": 0.0,
        }

        rec = _score_position(
            sym,
            position,
            tech_data.get(sym) or {},
            fd,
            macro_data,
            sent_data.get(sym, 0.0),
            em_data.get(sym) or {},
        )

        # Remap: can't SELL or HOLD what isn't owned
        if rec["action"] == "SELL":
            rec["action"] = "PASS"
        elif rec["action"] == "HOLD":
            rec["action"] = "WATCH"

        rec["source"] = "watchlist"
        rec["notes"] = notes_map.get(sym, "")
        results.append(rec)

    results.sort(key=lambda r: (_ACTION_ORDER.get(r["action"], 9), -r["score"]))
    _REC_CACHE[cache_key] = (results, time.time())
    return results
