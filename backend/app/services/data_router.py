"""Multi-source data router with disk cache, retry, and reliability logging.

Public API:
  get_quote(symbol, max_age_s=300)               -> dict
  get_history(symbol, period, interval, max_age_s=86400) -> list[dict]
  get_fundamentals(symbol, max_age_s=21600)      -> dict
  get_source_reliability(since_s=86400*7)        -> list[dict]

Order of providers per call type:
  Quote (US)     : massive -> yfinance -> finnhub -> tiingo -> stooq
  Quote (TSX)    : yfinance -> finnhub -> tiingo -> stooq
  History (US)   : massive -> yfinance -> tiingo -> stooq
  History (TSX)  : yfinance -> tiingo -> stooq
  Fundamentals   : yfinance -> finnhub -> alpha_vantage  (best for P/E/div/beta)

Each provider call is wrapped with:
  - disk cache hit/miss (skips network if fresh)
  - latency timing
  - SourceUnavailable -> next source
  - unexpected exception -> next source + logged

Every successful payload includes a `source` field so callers can present
provenance to the user (trust signal).
"""

from __future__ import annotations

import time
from typing import Callable

from app.services import data_cache as cache
from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    PriceBar,
    Quote,
    SourceUnavailable,
)
from app.services.data_sources.alphavantage_src import AlphaVantageSource
from app.services.data_sources.finnhub_src import FinnhubSource
from app.services.data_sources.massive_src import MassiveSource, is_tsx
from app.services.data_sources.stooq_src import StooqSource
from app.services.data_sources.tiingo_src import TiingoSource
from app.services.data_sources.yfinance_src import YFinanceSource


class DataRouterError(Exception):
    """Raised when all configured sources fail for a call."""


_yf = YFinanceSource()
_finnhub = FinnhubSource()
_alpha = AlphaVantageSource()
_tiingo = TiingoSource()
_stooq = StooqSource()
_massive = MassiveSource()


def _quote_chain(symbol: str) -> list[DataSource]:
    if is_tsx(symbol):
        return [_yf, _finnhub, _tiingo, _stooq]
    return [_massive, _yf, _finnhub, _tiingo, _stooq]


def _history_chain(symbol: str) -> list[DataSource]:
    if is_tsx(symbol):
        return [_yf, _tiingo, _stooq]
    return [_massive, _yf, _tiingo, _stooq]


def _fundamentals_chain(symbol: str) -> list[DataSource]:
    return [_yf, _finnhub, _alpha]


def _try_source(
    src: DataSource,
    fn: Callable[[DataSource], object],
) -> tuple[bool, object | None, str | None, float]:
    if not src.is_configured():
        return False, None, "not_configured", 0.0
    start = time.perf_counter()
    try:
        out = fn(src)
        latency = (time.perf_counter() - start) * 1000
        cache.log_source_call(src.name, True, latency, None)
        return True, out, None, latency
    except SourceUnavailable as e:
        latency = (time.perf_counter() - start) * 1000
        cache.log_source_call(src.name, False, latency, str(e))
        return False, None, str(e), latency
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        cache.log_source_call(src.name, False, latency, f"unexpected:{e}")
        return False, None, f"unexpected:{e}", latency


def get_quote(symbol: str, max_age_s: float = 300) -> dict:
    for src in _quote_chain(symbol):
        cached = cache.get_quote(symbol, src.name, max_age_s)
        if cached:
            return cached
    errors: list[str] = []
    for src in _quote_chain(symbol):
        ok, payload, err, _ = _try_source(src, lambda s: s.get_quote(symbol))
        if ok and isinstance(payload, Quote):
            d = payload.to_dict()
            cache.put_quote(symbol, src.name, d)
            return d
        if err:
            errors.append(f"{src.name}: {err}")
    raise DataRouterError(
        f"all sources failed for quote {symbol}: {'; '.join(errors)}"
    )


def get_history(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    max_age_s: float = 86400,
) -> list[dict]:
    for src in _history_chain(symbol):
        cached = cache.get_history(
            symbol, src.name, period, interval, max_age_s
        )
        if cached:
            return cached
    errors: list[str] = []
    for src in _history_chain(symbol):
        ok, payload, err, _ = _try_source(
            src,
            lambda s: s.get_history(symbol, period=period, interval=interval),
        )
        if ok and isinstance(payload, list) and payload:
            bars = [
                b.to_dict() if isinstance(b, PriceBar) else b
                for b in payload
            ]
            cache.put_history(symbol, src.name, period, interval, bars)
            return bars
        if err:
            errors.append(f"{src.name}: {err}")
    raise DataRouterError(
        f"all sources failed for history {symbol}: {'; '.join(errors)}"
    )


def get_fundamentals(symbol: str, max_age_s: float = 21600) -> dict:
    for src in _fundamentals_chain(symbol):
        cached = cache.get_fundamentals(symbol, src.name, max_age_s)
        if cached:
            return cached
    errors: list[str] = []
    for src in _fundamentals_chain(symbol):
        ok, payload, err, _ = _try_source(
            src, lambda s: s.get_fundamentals(symbol)
        )
        if ok and isinstance(payload, Fundamentals):
            d = payload.to_dict()
            cache.put_fundamentals(symbol, src.name, d)
            return d
        if err:
            errors.append(f"{src.name}: {err}")
    raise DataRouterError(
        f"all sources failed for fundamentals {symbol}: {'; '.join(errors)}"
    )


def get_source_reliability(since_s: float = 86400 * 7) -> list[dict]:
    """Return success rate + avg latency per source over the last `since_s`.

    Used by the trust-signal report to publish honest provider stats.
    """
    return cache.source_stats_summary(since_s=since_s)


def get_quotes_batch(
    symbols: list[str], max_age_s: float = 300
) -> dict[str, dict]:
    """Fetch quotes for N symbols in one yfinance download call.

    ~5x faster than N serial get_quote() calls because yfinance.download
    batches the HTTP request. Falls back to serial get_quote for any
    symbol that fails the batch parse.

    Returns {symbol: quote_dict} — missing symbols silently absent.
    """
    import yfinance as yf
    import pandas as pd

    # Check cache first — return only those not fresh
    out: dict[str, dict] = {}
    to_fetch: list[str] = []
    for sym in symbols:
        cached = cache.get_quote(sym, "yfinance", max_age_s)
        if cached:
            out[sym] = cached
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return out

    start = time.perf_counter()
    try:
        df = yf.download(
            to_fetch,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
        latency = (time.perf_counter() - start) * 1000

        if df is None or df.empty:
            raise ValueError("empty yf.download result")

        # MultiIndex when >1 symbol, flat when =1
        if isinstance(df.columns, pd.MultiIndex):
            close_df = df["Close"]
        else:
            if len(to_fetch) == 1:
                close_df = df[["Close"]].rename(
                    columns={"Close": to_fetch[0]}
                )
            else:
                raise ValueError(
                    "unexpected flat columns for multi-symbol"
                )

        for sym in to_fetch:
            if sym not in close_df.columns:
                continue
            prices = close_df[sym].dropna()
            if len(prices) < 2:
                continue
            price = float(prices.iloc[-1])
            prev = float(prices.iloc[-2])
            if price <= 0:
                continue
            q = Quote(
                symbol=sym,
                price=price,
                prev_close=prev,
                currency=None,
                day_change_pct=((price - prev) / prev * 100) if prev else None,
                source="yfinance_batch",
                as_of=time.time(),
            )
            d = q.to_dict()
            cache.put_quote(sym, "yfinance", d)
            cache.log_source_call(
                "yfinance_batch", True, latency / len(to_fetch)
            )
            out[sym] = d

    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        cache.log_source_call("yfinance_batch", False, latency, str(e))
        for sym in to_fetch:
            if sym not in out:
                try:
                    out[sym] = get_quote(sym, max_age_s)
                except DataRouterError:
                    pass

    return out


def prewarm(symbols: list[str]) -> dict[str, str]:
    """Batch-fetch quotes + fundamentals for a list of symbols at startup.

    Returns {symbol: "ok"|"error"} status map.
    Called from backend main.py so first real MCP request hits cache.
    """
    results: dict[str, str] = {}
    batch = get_quotes_batch(symbols, max_age_s=300)
    for sym in symbols:
        results[sym] = "ok" if sym in batch else "miss"

    for sym in symbols:
        try:
            get_fundamentals(sym, max_age_s=21600)
            results[sym] += "+fundamentals"
        except DataRouterError:
            pass

    return results


def configured_sources() -> dict[str, bool]:
    """Snapshot of which sources have credentials at startup."""
    return {
        "massive": _massive.is_configured(),
        "yfinance": _yf.is_configured(),
        "stooq": _stooq.is_configured(),
        "finnhub": _finnhub.is_configured(),
        "alpha_vantage": _alpha.is_configured(),
        "tiingo": _tiingo.is_configured(),
    }
