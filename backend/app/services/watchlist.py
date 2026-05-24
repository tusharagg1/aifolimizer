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
_REC_CACHE_TTL = 1800  # 30 minutes (legacy bulk cache, kept for fallback)

# Per-symbol rec cache — adding 1 symbol only re-scores that symbol.
_PER_SYM_CACHE: dict[str, tuple[dict, float]] = {}
_PER_SYM_TTL = 1800
# Cache shared market_breadth (macro) — same for every symbol
_MACRO_CACHE: tuple[dict, float] | None = None
_MACRO_TTL = 3600

_PER_FETCH_TIMEOUT_S = 15.0
_SENT_TIMEOUT_S = 8.0

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
    items.append({
        "symbol": sym, "notes": notes,
        "added_at": time.strftime("%Y-%m-%d"),
    })
    _save(items)
    return items


def remove_symbol(symbol: str) -> list[dict]:
    sym = symbol.strip().upper()
    items = [i for i in load_watchlist() if i["symbol"] != sym]
    _save(items)
    _PER_SYM_CACHE.pop(sym, None)
    return items


def _get_macro_cached() -> dict:
    global _MACRO_CACHE
    if _MACRO_CACHE and time.time() - _MACRO_CACHE[1] < _MACRO_TTL:
        return _MACRO_CACHE[0]
    try:
        m = market_breadth()
    except Exception:
        m = {}
    _MACRO_CACHE = (m, time.time())
    return m


def _score_one_symbol(sym: str, notes: str, macro_data: dict) -> dict:
    """Fetch + score a single symbol with hard per-call timeouts."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tech_f = pool.submit(tech_svc.get_technicals, [sym])
        fund_f = pool.submit(fund_svc.get_fundamentals, [sym])
        sent_f = pool.submit(_get_sentiment, sym)
        try:
            tech_data = tech_f.result(timeout=_PER_FETCH_TIMEOUT_S)
        except Exception:
            tech_data = {}
        try:
            fund_data = fund_f.result(timeout=_PER_FETCH_TIMEOUT_S)
        except Exception:
            fund_data = {}
        try:
            sent_score = sent_f.result(timeout=_SENT_TIMEOUT_S)
        except Exception:
            sent_score = 0.0

    try:
        em_data = get_earnings_expected_moves([sym], fund_data, tech_data)
    except Exception:
        em_data = {}

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
        sym, position, tech_data.get(sym) or {}, fd, macro_data,
        sent_score, em_data.get(sym) or {},
    )
    if rec["action"] == "SELL":
        rec["action"] = "PASS"
    elif rec["action"] == "HOLD":
        rec["action"] = "WATCH"
    rec["source"] = "watchlist"
    rec["notes"] = notes
    return rec


def get_watchlist_recommendations() -> list[dict]:
    items = load_watchlist()
    if not items:
        return []

    macro_data = _get_macro_cached()
    now = time.time()
    notes_map = {i["symbol"]: i.get("notes", "") for i in items}
    results: list[dict] = []

    # Fetch symbols not in cache (or stale) in parallel.
    stale_syms: list[str] = []
    for i in items:
        sym = i["symbol"]
        entry = _PER_SYM_CACHE.get(sym)
        if entry and now - entry[1] < _PER_SYM_TTL:
            rec = dict(entry[0])
            rec["notes"] = notes_map.get(sym, "")
            results.append(rec)
        else:
            stale_syms.append(sym)

    if stale_syms:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(stale_syms))
        ) as pool:
            futures = {
                pool.submit(
                    _score_one_symbol, sym, notes_map.get(sym, ""), macro_data,
                ): sym
                for sym in stale_syms
            }
            # Per-future timeout — one slow yfinance call can't block others.
            wait_s = _PER_FETCH_TIMEOUT_S + _SENT_TIMEOUT_S
            for f in concurrent.futures.as_completed(futures, timeout=None):
                sym = futures[f]
                try:
                    rec = f.result(timeout=wait_s)
                    _PER_SYM_CACHE[sym] = (rec, time.time())
                    results.append(rec)
                except Exception as e:
                    _LOG.warning(
                        "watchlist: scoring %s failed: %s", sym, e,
                    )
                    # Placeholder so frontend still sees the symbol.
                    results.append({
                        "symbol": sym, "action": "WATCH", "score": 0,
                        "confidence": "low", "current_price": None,
                        "take_profit": None, "stop_loss": None,
                        "risk_reward": None,
                        "reasons": [f"data fetch failed: {e}"],
                        "flags": ["data_unavailable"], "currency": "USD",
                        "source": "watchlist", "notes": notes_map.get(sym, ""),
                    })

    results.sort(key=lambda r: (_ACTION_ORDER.get(r["action"], 9), -r["score"]))
    return results
