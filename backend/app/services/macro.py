"""Macro data fetcher: FRED economic series + yfinance market breadth signals.

FRED CSV endpoints are public, no API key needed.
Market breadth uses yfinance (VIX, SPY regime).
"""

from __future__ import annotations

import csv
import io
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pandas as pd
import yfinance as yf


_TTL_SECONDS = 12 * 3600
_cache: dict[str, tuple[float, Any]] = {}


_SERIES = {
    # US rates & macro
    "fed_funds": "FEDFUNDS",
    "ten_year_yield": "DGS10",
    "two_year_yield": "DGS2",
    "unemployment_us": "UNRATE",
    "cpi_us": "CPIAUCSL",
    # FX
    "cad_usd": "DEXCAUS",
    "usd_index": "DTWEXBGS",  # Trade-weighted USD broad index
    # Canada
    "boc_overnight": "IRSTCB01CAM156N",
    "canada_cpi": "CPALCY01CAM659N",
    "canada_unemployment": "LRUNTTTTCAM156S",  # Canada unemployment (OECD, monthly)
    "canada_housing_prices": "QCAR628BIS",  # Canada residential property prices (BIS, quarterly)
    # Global rates
    "ecb_rate": "ECBDFR",  # ECB deposit facility rate
    # Commodities (FRED - slight lag; supplemented by yfinance real-time below)
    "gold_usd": "GOLDAMGBD228NLBM",  # Gold London AM fix USD/troy oz
    "wti_crude_usd": "DCOILWTICO",  # WTI crude USD/barrel
    "copper_usd_lb": "PCOPPUSDM",  # Copper USD/metric ton (World Bank, monthly)
}


def _fred_csv(series_id: str) -> tuple[str, float] | None:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    cached = _cache.get(url)
    if cached and time.time() - cached[0] < _TTL_SECONDS:
        return cached[1]

    try:
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return None

    rows = list(csv.reader(io.StringIO(text)))
    for row in reversed(rows[1:]):
        if len(row) != 2:
            continue
        date, value = row
        try:
            val = float(str(value).replace(",", ""))
            if not math.isnan(val):
                _cache[url] = (time.time(), (date, val))
                return date, val
        except Exception:
            continue
    return None


_COMMODITY_TTL = 300  # 5 min - real-time prices
_commodity_cache: tuple[float, dict] | None = None

_COMMODITY_TICKERS = {
    "gold_spot_usd": "GC=F",  # Gold futures (front month)
    "wti_spot_usd": "CL=F",  # WTI crude futures
    "silver_spot_usd": "SI=F",  # Silver futures
    "natural_gas_usd": "NG=F",  # Natural gas futures
    "dxy": "DX-Y.NYB",  # US Dollar Index
    "tsx_composite": "^GSPTSE",  # Toronto Stock Exchange Composite
}


def _commodity_snapshot() -> dict[str, Any]:
    """Real-time commodity + DXY + TSX via yfinance. Cached 5 min."""
    global _commodity_cache
    now = time.time()
    if _commodity_cache and now - _commodity_cache[0] < _COMMODITY_TTL:
        return _commodity_cache[1]

    out: dict[str, Any] = {}
    try:
        tickers = list(_COMMODITY_TICKERS.values())
        df = yf.download(
            tickers,
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                close = df["Close"]
            else:
                close = df
            for name, ticker in _COMMODITY_TICKERS.items():
                try:
                    col = close[ticker] if ticker in close.columns else None
                    if col is not None and not col.dropna().empty:
                        val = float(col.dropna().iloc[-1])
                        out[name] = round(val, 4)
                except Exception:
                    logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    _commodity_cache = (now, out)
    return out


def macro_snapshot() -> dict[str, Any]:
    """FRED macro series + real-time commodity/FX spot prices.

    Series fetched in parallel - _fred_csv() is HTTP-bound and each call has a
    10s timeout, so 15 serial reads can take ~15s on a cold L1 cache.
    """
    out: dict[str, Any] = {}
    items = list(_SERIES.items())
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        results = list(ex.map(lambda kv: (kv[0], kv[1], _fred_csv(kv[1])), items))
    for name, sid, result in results:
        if result:
            date, value = result
            out[name] = {"date": date, "value": value, "series_id": sid}
        else:
            out[name] = None

    # Overlay real-time commodity/index prices (yfinance, 5-min cache)
    try:
        commodities = _commodity_snapshot()
        out["commodities"] = commodities
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    return out


_BREADTH_TTL = 3600  # 1h - market data changes more than FRED
_breadth_cache: tuple[float, dict] | None = None

_FG_TTL = 3600  # 1h
_fg_cache: tuple[float, dict] | None = None


def fear_and_greed() -> dict[str, Any]:
    """CNN Fear & Greed Index. Score 0-100, rating string. Cached 1h."""
    global _fg_cache
    now = time.time()
    if _fg_cache and now - _fg_cache[0] < _FG_TTL:
        return _fg_cache[1]

    try:
        resp = httpx.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        fg = data.get("fear_and_greed", {})
        score = fg.get("score")
        rating = fg.get("rating", "")
        if score is not None:
            result = {
                "fear_greed_score": round(float(score), 1),
                "fear_greed_rating": rating,
            }
            _fg_cache = (now, result)
            return result
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    return {}


def market_breadth() -> dict[str, Any]:
    """VIX, SPY market regime (vs SMA200), and composite signal. Cached 1h."""
    global _breadth_cache
    now = time.time()
    if _breadth_cache and now - _breadth_cache[0] < _BREADTH_TTL:
        return _breadth_cache[1]

    out: dict[str, Any] = {}

    # VIX - fear gauge
    try:
        vix_info = yf.Ticker("^VIX").info
        vix = vix_info.get("regularMarketPrice") or vix_info.get("previousClose")
        if vix:
            out["vix"] = round(float(vix), 2)
            if vix > 25:
                out["vix_signal"] = "fear"
                out["vix_regime"] = "elevated"
            elif vix < 15:
                out["vix_signal"] = "complacency"
                out["vix_regime"] = "low"
            else:
                out["vix_signal"] = "neutral"
                out["vix_regime"] = "normal"
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    # SPY regime vs 200-day SMA
    try:
        spy_df = yf.download(
            "SPY",
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if spy_df is not None and not spy_df.empty:
            if isinstance(spy_df.columns, pd.MultiIndex):
                spy_df.columns = spy_df.columns.get_level_values(0)
            close = spy_df["Close"].squeeze()
            sma200 = close.rolling(200).mean()
            current = float(close.iloc[-1])
            last = sma200.iloc[-1]
            sma200_val = float(last) if pd.notna(last) else None
            if sma200_val:
                pct = round((current - sma200_val) / sma200_val * 100, 2)
                out["spy_price"] = round(current, 2)
                out["spy_sma200"] = round(sma200_val, 2)
                out["spy_vs_sma200_pct"] = pct
                out["spy_regime"] = "bull" if current > sma200_val else "bear"
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    # Composite regime signal. SPY direction is the dominant input; if its fetch
    # failed (key absent), do NOT assume bull - that fail-open silently stamped
    # bull_low_fear on every rec and let BUYs through unguarded. Emit "unknown"
    # so downstream gates can treat an unconfirmed regime as risk-off.
    vix_regime = out.get("vix_regime", "normal")
    spy_regime = out.get("spy_regime")
    if spy_regime is None:
        out["market_regime"] = "unknown"
        out["regime_signal"] = "Market regime unavailable (SPY/VIX fetch failed) - treat as risk-off for new entries."
    elif spy_regime == "bull" and vix_regime in ("normal", "low"):
        out["market_regime"] = "bull_low_fear"
        out["regime_signal"] = "Risk-on. Momentum and growth strategies favored."
    elif spy_regime == "bull" and vix_regime == "elevated":
        out["market_regime"] = "bull_high_fear"
        out["regime_signal"] = "Bull trend intact but fear elevated - potential pullback or buying opportunity."
    elif spy_regime == "bear" and vix_regime == "elevated":
        out["market_regime"] = "bear_high_fear"
        out["regime_signal"] = "Bear market with elevated fear - defensive positioning recommended."
    else:
        out["market_regime"] = "bear_low_fear"
        out["regime_signal"] = "Complacent bear - caution, volatility expansion risk."

    # Yield curve: 2Y/10Y spread - strongest free recession predictor
    try:
        r10 = _fred_csv("DGS10")
        r2 = _fred_csv("DGS2")
        if r10 and r2:
            spread = round(r10[1] - r2[1], 3)
            out["yield_curve_spread"] = spread  # positive = normal, negative = inverted
            out["yield_curve_inverted"] = spread < 0
            if spread < -0.5:
                out["yield_curve_signal"] = "deeply_inverted"
            elif spread < 0:
                out["yield_curve_signal"] = "inverted"
            elif spread < 0.5:
                out["yield_curve_signal"] = "flat"
            else:
                out["yield_curve_signal"] = "normal"
            out["ten_year_yield"] = r10[1]
            out["two_year_yield"] = r2[1]
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    # Merge Fear & Greed (non-fatal)
    try:
        out.update(fear_and_greed())
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    # ── Portfolio-level defensive signal ──────────────────────────────────────
    # Fires when multiple macro risk factors align - tells you to raise cash
    # before individual position signals can update.
    risk_count = 0
    risk_reasons = []

    regime = out.get("market_regime", "bull_low_fear")
    vix_val = out.get("vix")
    yc = out.get("yield_curve_signal")
    fg = out.get("fear_greed_score")

    if regime in ("bear_high_fear", "bear_low_fear"):
        risk_count += 2
        risk_reasons.append(f"Market in {regime.replace('_', ' ')} regime")
    if vix_val and vix_val > 25:
        risk_count += 1
        risk_reasons.append(f"VIX {vix_val:.0f} - elevated volatility")
    if yc in ("deeply_inverted", "inverted"):
        risk_count += 1
        risk_reasons.append(f"Yield curve {yc.replace('_', ' ')} - recession signal active")
    if fg is not None and fg >= 80:
        risk_count += 1
        risk_reasons.append(f"Fear & Greed {fg:.0f} - extreme greed, euphoria risk")

    if risk_count >= 3:
        out["portfolio_signal"] = "raise_cash"
        out["portfolio_signal_strength"] = "strong"
        out["portfolio_target_cash_pct"] = 40
        out["portfolio_signal_reasons"] = risk_reasons
    elif risk_count == 2:
        out["portfolio_signal"] = "reduce_risk"
        out["portfolio_signal_strength"] = "moderate"
        out["portfolio_target_cash_pct"] = 20
        out["portfolio_signal_reasons"] = risk_reasons
    else:
        out["portfolio_signal"] = "stay_invested"
        out["portfolio_signal_strength"] = "none"
        out["portfolio_target_cash_pct"] = None
        out["portfolio_signal_reasons"] = []

    _breadth_cache = (now, out)
    return out
