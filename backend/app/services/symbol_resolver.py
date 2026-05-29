"""Canonical symbol resolution + validation.

yfinance resolves exact tickers correctly (GLD -> SPDR Gold Shares ETF,
MU -> Micron Technology) but two gaps let bad identities through:

  1. The fundamentals path keyed everything off `quoteSummary`, which 404s for
     ETFs, so ETF name/type were lost and callers fell back to the bare symbol.
  2. `watchlist.add_symbol` did no validation, so typos / unknown tickers
     entered the watchlist unchecked and surfaced as wrong-looking rows.

resolve() returns one trustworthy identity record per ticker plus a `valid`
flag callers use to reject unknown symbols. It reads `.info` (works for ETFs)
rather than the fundamentals endpoint.
"""
from __future__ import annotations

import time

import yfinance as yf

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.symbol_resolver")

_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 6 * 3600  # identity rarely changes

_ETF_TYPES = {"ETF", "MUTUALFUND"}


def _asset_class(quote_type: str | None) -> str:
    qt = (quote_type or "").upper()
    if qt in _ETF_TYPES:
        return "etf"
    if qt == "CRYPTOCURRENCY":
        return "crypto"
    if qt == "INDEX":
        return "index"
    return "stock"


def _unverified(req: str, reason: str) -> dict:
    return {
        "symbol": req, "requested": req, "name": None, "quote_type": None,
        "asset_class": "stock", "exchange": None, "currency": None,
        "price": None, "valid": False, "reason": reason,
    }


def resolve(symbol: str) -> dict:
    """Canonical identity for a ticker. Cached 6h.

    Returns: {symbol, requested, name, quote_type, asset_class, exchange,
    currency, price, valid, reason}.

    reason "not_found" = network worked but Yahoo has no such security
    (caller should reject). reason "network_error" = could not verify (caller
    may fail-open). "ok" = resolved. Unverified results are not cached so a
    later call can retry.
    """
    req = (symbol or "").strip().upper()
    if not req:
        return {**_unverified("", "empty")}

    entry = _CACHE.get(req)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]

    try:
        info = yf.Ticker(req).info or {}
    except Exception as e:
        _LOG.warning(f"[resolver] {req}: {type(e).__name__}: {e}")
        return _unverified(req, "network_error")

    name = info.get("longName") or info.get("shortName")
    quote_type = info.get("quoteType")
    canonical = (info.get("symbol") or "").upper()
    price = (
        info.get("regularMarketPrice")
        or info.get("currentPrice")
        or info.get("previousClose")
    )

    # Yahoo returns an essentially empty info dict for unknown tickers.
    if not name and price is None and not canonical:
        result = _unverified(req, "not_found")
        _CACHE[req] = (result, time.time())
        return result

    result = {
        "symbol": canonical or req,
        "requested": req,
        "name": name or (canonical or req),
        "quote_type": quote_type,
        "asset_class": _asset_class(quote_type),
        "exchange": info.get("exchange"),
        "currency": info.get("currency"),
        "price": price,
        "valid": name is not None or price is not None,
        "reason": "ok",
    }
    _CACHE[req] = (result, time.time())
    return result
