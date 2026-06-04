"""Multi-source data router — currency-aware, fault-tolerant.

Public API:
  get_quote(symbol, max_age_s=300, verify=False)        -> dict
  get_history(symbol, period, interval, max_age_s)      -> list[dict]
  get_fundamentals(symbol, max_age_s)                   -> dict
  get_quotes_batch(symbols, max_age_s)                  -> {sym: dict}
  get_source_reliability(since_s)                       -> list[dict]
  prewarm(symbols)                                      -> {sym: status}

Routing matrix (per-asset, real-time first, EOD last):
  us_equity quote  : finnhub -> twelve_data -> yfinance -> tiingo -> stooq
                     -> massive
  ca_equity quote  : twelve_data -> yfinance -> finnhub -> tiingo
                     -> eodhd -> stooq
  uk_equity quote  : twelve_data -> yfinance -> eodhd -> stooq
  eu_equity quote  : twelve_data -> yfinance -> eodhd -> stooq
  crypto quote     : binance -> coingecko -> twelve_data -> yfinance
  fx quote         : frankfurter -> twelve_data -> yfinance
  index quote      : yfinance -> twelve_data -> stooq
  history (any)    : massive(US) | twelve_data | yfinance | tiingo
                     | eodhd | stooq
  fundamentals     : eodhd -> yfinance -> finnhub -> alpha_vantage

Wealthsimple-as-source is VERIFICATION-ONLY:
  - Never in primary chain (broker positions lag ticks; session can expire).
  - When session is authed AND verify=True, used as a second opinion to
    confirm or flag the primary. When session is expired, silently skipped
    so quote freshness is unaffected.

Fault-tolerance features:
  - Per-asset chain selection via classify_asset()
  - In-process circuit breaker per source (6 failures / 60s -> 5min cooldown)
  - Staleness gate: cached quote rejected if older than asset-specific budget
  - Currency stamp validation: payload currency must be in expected set
  - Optional verify=True: fetch from 2 sources, return mean if delta<2%, else
    pick lower-latency-confirmed source and flag confidence
  - Source attribution: every payload carries `source` for trust report
  - Reliability log: success/failure latency persisted to source_stats
"""

from __future__ import annotations

import logging
import re
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
from app.services.data_sources.binance_src import BinanceSource
from app.services.data_sources.circuit_breaker import default_breaker
from app.services.data_sources.coingecko_src import CoinGeckoSource
from app.services.data_sources.eodhd_src import EODHDSource
from app.services.data_sources.finnhub_src import FinnhubSource
from app.services.data_sources.frankfurter_src import FrankfurterSource
from app.services.data_sources.massive_src import MassiveSource
from app.services.data_sources.stooq_src import StooqSource
from app.services.data_sources.symbol_classifier import (
    classify_asset,
    expected_currencies,
    staleness_budget_s,
)
from app.services.data_sources.tiingo_src import TiingoSource
from app.services.data_sources.twelve_data_src import TwelveDataSource
from app.services.data_sources.wealthsimple_src import WealthsimpleSource
from app.services.data_sources.yfinance_src import YFinanceSource


class DataRouterError(Exception):
    """Raised when all configured sources fail for a call."""


_yf = YFinanceSource()
_finnhub = FinnhubSource()
_alpha = AlphaVantageSource()
_tiingo = TiingoSource()
_stooq = StooqSource()
_massive = MassiveSource()
_twelve = TwelveDataSource()
_eodhd = EODHDSource()
_frankfurter = FrankfurterSource()
_binance = BinanceSource()
_coingecko = CoinGeckoSource()
_ws_src = WealthsimpleSource()
_breaker = default_breaker()

# Sources demoted by source_drift (chronically failing / rate-limited) are
# routed LAST so a bad provider can't keep heading the chain and burning
# latency + log noise. In-process; re-evaluated each nightly drift pass.
_DEMOTED: set[str] = set()


def demote(name: str) -> None:
    """Route `name` to the back of every chain until cleared/restart."""
    _DEMOTED.add(name)


def clear_demotion(name: str) -> None:
    _DEMOTED.discard(name)


def demoted_sources() -> list[str]:
    return sorted(_DEMOTED)


# Reliability-ranked routing: each chain is reordered best-first by the
# source's observed 30-day success rate (cached). Sources with too little
# evidence keep their authored position (neutral default); demoted sources
# are forced to the back regardless. Pure reorder — never drops a source.
_RANK_TTL = 600.0  # refresh the reliability snapshot at most every 10 min
_RANK_MIN_CALLS = 20  # below this, not enough evidence to re-rank
_RANK_DEFAULT = 85.0  # assumed success% for unproven sources (keeps them mid)
_rank_cache: tuple[float, dict[str, float]] | None = None


def _reliability_rank() -> dict[str, float]:
    global _rank_cache
    now = time.time()
    if _rank_cache and now - _rank_cache[0] < _RANK_TTL:
        return _rank_cache[1]
    rank: dict[str, float] = {}
    try:
        for r in cache.source_stats_summary(since_s=30 * 86400):
            if (r.get("calls") or 0) >= _RANK_MIN_CALLS:
                rank[r["source"]] = float(r.get("success_rate_pct") or 0.0)
    except Exception:
        pass
    _rank_cache = (now, rank)
    return rank


def _order_chain(chain: list[DataSource]) -> list[DataSource]:
    """Stable best-first reorder by observed reliability; demoted last."""
    rank = _reliability_rank()
    if not rank and not _DEMOTED:
        return chain

    def _key(item: tuple[int, DataSource]) -> tuple:
        idx, src = item
        if src.name in _DEMOTED:
            return (1, 0.0, idx)  # hard back, preserve relative order
        rel = rank.get(src.name, _RANK_DEFAULT)
        return (0, -rel, idx)  # proven: reliability desc; ties keep authored order

    return [s for _, s in sorted(enumerate(chain), key=_key)]


def _quote_chain(symbol: str) -> list[DataSource]:
    return _order_chain(_quote_chain_base(symbol))


def _history_chain(symbol: str) -> list[DataSource]:
    return _order_chain(_history_chain_base(symbol))


def _fundamentals_chain(symbol: str) -> list[DataSource]:
    return _order_chain(_fundamentals_chain_base(symbol))


def _quote_chain_base(symbol: str) -> list[DataSource]:
    info = classify_asset(symbol)
    ac = info.asset_class
    if ac == "crypto":
        return [_binance, _coingecko, _twelve, _yf]
    if ac == "fx":
        return [_frankfurter, _twelve, _yf]
    if ac == "index":
        return [_yf, _twelve, _stooq]
    if ac == "us_equity":
        return [_finnhub, _twelve, _yf, _tiingo, _stooq, _massive]
    if ac == "ca_equity":
        return [_twelve, _yf, _finnhub, _tiingo, _eodhd, _stooq]
    if ac in ("uk_equity", "eu_equity"):
        return [_twelve, _yf, _eodhd, _stooq]
    return [_yf, _twelve, _tiingo, _stooq]


def _history_chain_base(symbol: str) -> list[DataSource]:
    info = classify_asset(symbol)
    ac = info.asset_class
    if ac == "crypto":
        return [_binance, _coingecko, _yf]
    if ac == "fx":
        return [_frankfurter, _twelve, _yf]
    if ac == "index":
        return [_yf, _twelve, _stooq]
    if ac == "us_equity":
        # yfinance leads (most reliable here); massive last — it is free-tier
        # rate-limited (429s) and net-negative as a primary for history.
        return [_yf, _twelve, _tiingo, _stooq, _massive]
    if ac == "ca_equity":
        return [_twelve, _yf, _tiingo, _eodhd, _stooq]
    if ac in ("uk_equity", "eu_equity"):
        return [_twelve, _yf, _eodhd, _stooq]
    return [_yf, _twelve, _tiingo, _stooq]


def _fundamentals_chain_base(symbol: str) -> list[DataSource]:
    info = classify_asset(symbol)
    if info.asset_class == "ca_equity":
        return [_eodhd, _yf, _finnhub, _alpha]
    return [_eodhd, _yf, _finnhub, _alpha]


_SECRET_RE = re.compile(r"(?i)(api[_-]?key|apikey|token|key)=[^&\s]+")


def _scrub(msg: str) -> str:
    """Strip credential query-params from provider error strings before they are logged/persisted."""
    return _SECRET_RE.sub(r"\1=***", msg)


def _try_source(
    src: DataSource,
    fn: Callable[[DataSource], object],
) -> tuple[bool, object | None, str | None, float]:
    if not src.is_configured():
        return False, None, "not_configured", 0.0
    if _breaker.is_open(src.name):
        return False, None, "breaker_open", 0.0
    start = time.perf_counter()
    try:
        out = fn(src)
        latency = (time.perf_counter() - start) * 1000
        cache.log_source_call(src.name, True, latency, None)
        _breaker.record(src.name, ok=True)
        return True, out, None, latency
    except SourceUnavailable as e:
        latency = (time.perf_counter() - start) * 1000
        msg = _scrub(str(e))
        cache.log_source_call(src.name, False, latency, msg)
        _breaker.record(src.name, ok=False)
        return False, None, msg, latency
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        msg = _scrub(f"unexpected:{e}")
        cache.log_source_call(src.name, False, latency, msg)
        _breaker.record(src.name, ok=False)
        return False, None, msg, latency


def _validate_currency(payload: dict, asset_class: str) -> bool:
    """True if payload's currency is plausible for the asset class.

    Empty / None currency is permissive — many sources don't stamp it and
    we don't want to reject otherwise-good data.
    """
    cur = (payload.get("currency") or "").upper()
    if not cur:
        return True
    expected = expected_currencies(asset_class)
    if not expected:
        return True
    return cur in expected


def get_quote(
    symbol: str,
    max_age_s: float | None = None,
    verify: bool = False,
) -> dict:
    """Fetch quote with currency-aware routing and staleness validation.

    max_age_s: override the asset-specific staleness budget. None uses
    classify_asset budget (60s crypto, 300s equity intraday, etc).
    verify: when True, also probe the next provider in the chain AND, if
    the WS session is authed, the user's broker quote, then return the
    consensus payload. WS being expired never blocks the call.
    """
    info = classify_asset(symbol)
    if max_age_s is None:
        max_age_s = staleness_budget_s(info.asset_class)

    # Cache-first scan honoring staleness budget
    for src in _quote_chain(symbol):
        cached = cache.get_quote(symbol, src.name, max_age_s)
        if cached and _validate_currency(cached, info.asset_class):
            if not cached.get("currency") and info.currency:
                cached = dict(cached)
                cached["currency"] = info.currency
            if verify:
                return _augment_with_ws_verification(cached, info.asset_class)
            return cached

    primary: dict | None = None
    primary_latency = 0.0
    errors: list[str] = []

    for src in _quote_chain(symbol):
        ok, payload, err, latency = _try_source(src, lambda s: s.get_quote(symbol))
        if ok and isinstance(payload, Quote):
            d = payload.to_dict()
            if not _validate_currency(d, info.asset_class):
                errors.append(f"{src.name}: currency mismatch ({d.get('currency')})")
                continue
            if not d.get("currency") and info.currency:
                d["currency"] = info.currency
            cache.put_quote(symbol, src.name, d)
            if not verify:
                return d
            if primary is None:
                primary, primary_latency = d, latency
                continue
            merged = _merge_verified(primary, d, primary_latency, latency, info.asset_class)
            return _augment_with_ws_verification(merged, info.asset_class)
        if err:
            errors.append(f"{src.name}: {err}")

    if verify and primary is not None:
        primary["_verify_status"] = "single_source_only"
        return _augment_with_ws_verification(primary, info.asset_class)

    raise DataRouterError(f"all sources failed for quote {symbol} ({info.asset_class}): {'; '.join(errors)}")


def _augment_with_ws_verification(payload: dict, asset_class: str) -> dict:
    """Cross-check primary quote against the user's broker (Wealthsimple).

    Only fires when the WS session is currently authed. Expired/missing
    session is silently ignored — the primary payload is returned
    untouched so freshness is never coupled to broker auth state.
    """
    if not _ws_src.is_configured():
        return payload
    sym = payload.get("symbol")
    if not sym:
        return payload
    try:
        ws_q = _ws_src.get_quote(sym)
    except SourceUnavailable:
        return payload
    except Exception:
        return payload

    p_main = float(payload.get("price") or 0)
    p_ws = float(ws_q.price or 0)
    if p_main <= 0 or p_ws <= 0:
        return payload
    delta_pct = abs(p_main - p_ws) / ((p_main + p_ws) / 2) * 100
    threshold = 2.0 if asset_class == "crypto" else 1.5
    out = dict(payload)
    out["_ws_verify_price"] = p_ws
    out["_ws_verify_delta_pct"] = round(delta_pct, 3)
    out["_ws_verify_status"] = "agreement" if delta_pct <= threshold else "broker_disagreement"
    return out


def _merge_verified(a: dict, b: dict, lat_a: float, lat_b: float, asset_class: str) -> dict:
    """Combine two source quotes into a verified payload."""
    pa = float(a.get("price") or 0)
    pb = float(b.get("price") or 0)
    if pa <= 0 or pb <= 0:
        winner = a if pa > 0 else b
        winner["_verify_status"] = "partial"
        return winner

    delta_pct = abs(pa - pb) / ((pa + pb) / 2) * 100
    threshold = 2.0 if asset_class == "crypto" else 1.5
    if delta_pct <= threshold:
        merged = dict(a)
        merged["price"] = (pa + pb) / 2
        merged["_verify_status"] = "agreement"
        merged["_verify_delta_pct"] = round(delta_pct, 3)
        merged["_verify_sources"] = [a.get("source"), b.get("source")]
        return merged

    # Disagreement — prefer lower-latency source, tag confidence
    winner = a if lat_a <= lat_b else b
    out = dict(winner)
    out["_verify_status"] = "disagreement"
    out["_verify_delta_pct"] = round(delta_pct, 3)
    out["_verify_sources"] = [a.get("source"), b.get("source")]
    return out


def get_history(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    max_age_s: float = 86400,
) -> list[dict]:
    for src in _history_chain(symbol):
        cached = cache.get_history(symbol, src.name, period, interval, max_age_s)
        if cached:
            return cached
    errors: list[str] = []
    for src in _history_chain(symbol):
        ok, payload, err, _ = _try_source(
            src,
            lambda s: s.get_history(symbol, period=period, interval=interval),
        )
        if ok and isinstance(payload, list) and payload:
            bars = [b.to_dict() if isinstance(b, PriceBar) else b for b in payload]
            cache.put_history(symbol, src.name, period, interval, bars)
            return bars
        if err:
            errors.append(f"{src.name}: {err}")
    raise DataRouterError(f"all sources failed for history {symbol}: {'; '.join(errors)}")


def get_fundamentals(symbol: str, max_age_s: float = 21600) -> dict:
    for src in _fundamentals_chain(symbol):
        cached = cache.get_fundamentals(symbol, src.name, max_age_s)
        if cached:
            return cached
    errors: list[str] = []
    for src in _fundamentals_chain(symbol):
        ok, payload, err, _ = _try_source(src, lambda s: s.get_fundamentals(symbol))
        if ok and isinstance(payload, Fundamentals):
            d = payload.to_dict()
            cache.put_fundamentals(symbol, src.name, d)
            return d
        if err:
            errors.append(f"{src.name}: {err}")
    raise DataRouterError(f"all sources failed for fundamentals {symbol}: {'; '.join(errors)}")


def get_source_reliability(since_s: float = 86400 * 7) -> list[dict]:
    return cache.source_stats_summary(since_s=since_s)


def get_quotes_batch(symbols: list[str], max_age_s: float | None = None) -> dict[str, dict]:
    """Batched fetch — yfinance batch path for US/CA equities, per-symbol
    fallback for crypto/fx/index since they need different providers.

    Returns {symbol: quote_dict}. Symbols that fail every source silently
    drop out of the map (caller must check membership).
    """
    import yfinance as yf
    import pandas as pd

    out: dict[str, dict] = {}
    batch_eligible: list[str] = []
    per_symbol: list[str] = []
    for sym in symbols:
        info = classify_asset(sym)
        if info.asset_class in ("us_equity", "ca_equity", "index"):
            batch_eligible.append(sym)
        else:
            per_symbol.append(sym)

    # Cache-first for batch-eligible
    to_fetch: list[str] = []
    for sym in batch_eligible:
        info = classify_asset(sym)
        budget = max_age_s if max_age_s is not None else staleness_budget_s(info.asset_class)
        cached = cache.get_quote(sym, "yfinance", budget)
        if cached and _validate_currency(cached, info.asset_class):
            out[sym] = cached
        else:
            to_fetch.append(sym)

    if to_fetch:
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
            if isinstance(df.columns, pd.MultiIndex):
                close_df = df["Close"]
            else:
                if len(to_fetch) == 1:
                    close_df = df[["Close"]].rename(columns={"Close": to_fetch[0]})
                else:
                    raise ValueError("unexpected flat columns for multi-symbol")

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
                info = classify_asset(sym)
                ccy = info.currency
                q = Quote(
                    symbol=sym,
                    price=price,
                    prev_close=prev,
                    currency=ccy,
                    day_change_pct=((price - prev) / prev * 100 if prev else None),
                    source="yfinance_batch",
                    as_of=time.time(),
                )
                d = q.to_dict()
                cache.put_quote(sym, "yfinance", d)
                cache.log_source_call("yfinance_batch", True, latency / len(to_fetch))
                out[sym] = d
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            cache.log_source_call("yfinance_batch", False, latency, str(e))
            # Cap per-symbol fall-through. Without a cap a 200-symbol
            # batch failure cascades into 200 sequential per-symbol
            # downloads (each running its own fallback chain across 4
            # providers = 800 HTTPs). Limit to 16 freshest names; the
            # rest stay missing this tick and refresh next call.
            _MAX_FALLBACK_SYMBOLS = 16
            spillover = [s for s in to_fetch if s not in out]
            for sym in spillover[:_MAX_FALLBACK_SYMBOLS]:
                per_symbol.append(sym)
            if len(spillover) > _MAX_FALLBACK_SYMBOLS:
                cache.log_source_call(
                    "yfinance_batch",
                    False,
                    0,
                    (f"fallback capped at {_MAX_FALLBACK_SYMBOLS} of {len(spillover)}"),
                )

    # Per-symbol path for crypto/fx/index/anything that missed the batch
    for sym in per_symbol:
        if sym in out:
            continue
        try:
            out[sym] = get_quote(sym, max_age_s=max_age_s)
        except DataRouterError:
            logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    return out


def prewarm(symbols: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    batch = get_quotes_batch(symbols)
    for sym in symbols:
        results[sym] = "ok" if sym in batch else "miss"
    for sym in symbols:
        try:
            get_fundamentals(sym, max_age_s=21600)
            results[sym] += "+fundamentals"
        except DataRouterError:
            logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    return results


def configured_sources() -> dict[str, bool]:
    return {
        "wealthsimple": _ws_src.is_configured(),
        "massive": _massive.is_configured(),
        "yfinance": _yf.is_configured(),
        "stooq": _stooq.is_configured(),
        "finnhub": _finnhub.is_configured(),
        "alpha_vantage": _alpha.is_configured(),
        "tiingo": _tiingo.is_configured(),
        "twelve_data": _twelve.is_configured(),
        "eodhd": _eodhd.is_configured(),
        "frankfurter": _frankfurter.is_configured(),
        "binance": _binance.is_configured(),
        "coingecko": _coingecko.is_configured(),
    }


def breaker_state() -> dict:
    return _breaker.state()
