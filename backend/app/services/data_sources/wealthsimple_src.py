"""Wealthsimple-as-source — derive live quotes for HELD tickers.

Why: the WS GraphQL session is already authenticated for the user and
streams live positional values that include unit price * quantity. For
the user's held tickers this is the most authoritative source available
(it's literally what their broker shows on screen) and costs zero extra
API quota.

Limitation: only held tickers. Unknown symbols raise SourceUnavailable
so the router falls through.

The adapter snapshots positions once, caches the unit-price map for
60 s, and serves quotes from it. Background refresh happens lazily on
the next get_quote past the TTL.
"""

from __future__ import annotations

import threading
import time

from app.services.data_sources.base import (
    DataSource,
    Quote,
    SourceUnavailable,
)

_TTL_S = 60.0
_lock = threading.Lock()
_cache: dict[str, dict] = {}  # symbol -> {price, currency, as_of}
_cache_built_at = 0.0


def _refresh_from_ws() -> None:
    """Pull live positions from any authed WS session and cache unit prices."""
    global _cache_built_at
    try:
        from app.services import wealthsimple as ws_mod
    except Exception:
        return

    sessions = getattr(ws_mod, "_sessions", {}) or {}
    authed_sid = None
    for sid, sess in sessions.items():
        if sess.get("state") == "authed":
            authed_sid = sid
            break
    if not authed_sid:
        return

    try:
        positions = ws_mod.get_all_positions(authed_sid)
    except Exception:
        return

    new_cache: dict[str, dict] = {}
    for p in positions:
        sec = p.get("security") or {}
        sym = (sec.get("symbol") or "").upper()
        qty = float(p.get("quantity") or 0)
        mv = p.get("market_value") or {}
        amt = float(mv.get("amount") or 0)
        ccy = (mv.get("currency") or "").upper() or None
        if not sym or qty <= 0 or amt <= 0:
            continue
        unit_price = amt / qty
        new_cache[sym] = {
            "price": unit_price,
            "currency": ccy,
            "as_of": time.time(),
        }

    if new_cache:
        with _lock:
            _cache.clear()
            _cache.update(new_cache)
            _cache_built_at = time.time()


def _ensure_fresh() -> None:
    if (time.time() - _cache_built_at) > _TTL_S or not _cache:
        _refresh_from_ws()


def held_symbols_cached() -> set[str]:
    """Held tickers from the cache ONLY — never triggers a WS refresh.

    Zero-latency: returns whatever the last positions snapshot loaded (the
    session warms it during normal portfolio loads). Empty if nothing cached
    yet, so callers add no network cost on a cold cache.
    """
    with _lock:
        return set(_cache.keys())


def peek_quote(symbol: str) -> Quote | None:
    """Cache-only WS quote (no refresh). None if not held / not cached."""
    with _lock:
        row = _cache.get(symbol.upper())
    if not row:
        return None
    price = float(row.get("price") or 0)
    if price <= 0:
        return None
    return Quote(
        symbol=symbol,
        price=price,
        prev_close=price,
        currency=row.get("currency"),
        day_change_pct=None,
        source="wealthsimple",
        as_of=row.get("as_of", time.time()),
    )


class WealthsimpleSource(DataSource):
    name = "wealthsimple"

    def is_configured(self) -> bool:
        try:
            from app.services import wealthsimple as ws_mod

            sessions = getattr(ws_mod, "_sessions", {}) or {}
            return any(s.get("state") == "authed" for s in sessions.values())
        except Exception:
            return False

    def get_quote(self, symbol: str) -> Quote:
        if not self.is_configured():
            raise SourceUnavailable("wealthsimple: no authed session")
        _ensure_fresh()
        sym = symbol.upper()
        with _lock:
            row = _cache.get(sym)
        if not row:
            raise SourceUnavailable(f"wealthsimple: not held {symbol}")
        price = float(row["price"])
        if price <= 0:
            raise SourceUnavailable(f"wealthsimple: zero price {symbol}")
        # WS doesn't expose prev_close in positions feed — caller's day_change
        # path will fall through to a different source if needed.
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=price,  # unknown; caller must not infer change from this alone
            currency=row.get("currency"),
            day_change_pct=None,
            source=self.name,
            as_of=row["as_of"],
        )
