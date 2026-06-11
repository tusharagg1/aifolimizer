"""Intraday technical indicators on 5-minute bars.

Separate module from technicals.py - different bar interval, different cache
TTL, and a strictly intraday indicator set (VWAP, opening range, RSI(2)
Connors-style, ATR for stops, volume spike vs 20-bar avg).

Use cases:
- pre-trade-check on day-trade entries
- daily-briefing intraday addendum
- catalyst-day momentum scans

NOT for swing/position - those stay on daily bars (technicals.py).
"""

from __future__ import annotations

import time
from datetime import time as dtime

import pandas as pd
import ta
import yfinance as yf
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.technicals_intraday")

_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 60  # 1 minute - intraday bars stale fast

# Regular-hours start in US/Eastern for opening-range calc (first 30 min = 9:30-10:00)
_RTH_OPEN = dtime(9, 30)
_OPENING_RANGE_END = dtime(10, 0)


def _safe(series: pd.Series | None) -> float | None:
    try:
        if series is None or series.empty:
            return None
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


def _today_session(df: pd.DataFrame) -> pd.DataFrame:
    """Slice to today's regular trading hours only (US/Eastern)."""
    if df.empty:
        return df
    try:
        idx = df.index
        if idx.tz is None:
            idx_et = idx.tz_localize("UTC").tz_convert("US/Eastern")
        else:
            idx_et = idx.tz_convert("US/Eastern")
        today = idx_et[-1].date()
        mask = (idx_et.date == today) & (idx_et.time >= _RTH_OPEN)
        return df[mask]
    except Exception:
        return df


def _opening_range(today_df: pd.DataFrame) -> tuple[float | None, float | None]:
    """High / low of first 30 minutes of regular hours."""
    if today_df.empty:
        return None, None
    try:
        idx = today_df.index
        if idx.tz is None:
            idx_et = idx.tz_localize("UTC").tz_convert("US/Eastern")
        else:
            idx_et = idx.tz_convert("US/Eastern")
        or_mask = (idx_et.time >= _RTH_OPEN) & (idx_et.time < _OPENING_RANGE_END)
        or_df = today_df[or_mask]
        if or_df.empty:
            return None, None
        return float(or_df["High"].max()), float(or_df["Low"].min())
    except Exception:
        return None, None


def _vwap(today_df: pd.DataFrame) -> float | None:
    """Session VWAP from today's regular-hours bars."""
    if today_df.empty:
        return None
    try:
        typical = (today_df["High"] + today_df["Low"] + today_df["Close"]) / 3
        vol = today_df["Volume"].astype(float)
        cum_vol = vol.cumsum()
        if cum_vol.iloc[-1] == 0:
            return None
        vwap = (typical * vol).cumsum() / cum_vol
        return float(vwap.iloc[-1])
    except Exception:
        return None


def _compute_from_df(df: pd.DataFrame) -> dict:
    """Indicators from 5-min OHLCV (5 days fetched, today's session sliced)."""
    try:
        if df is None or df.empty or len(df) < 30:
            return {}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        today = _today_session(df)
        if today.empty or len(today) < 3:
            # Pre-market or first bar - fall back to full df for indicators
            today = df.tail(78)  # ~1 day of 5-min bars in RTH

        close = today["Close"].squeeze()
        high = today["High"].squeeze()
        low = today["Low"].squeeze()
        volume = today["Volume"].squeeze().astype(float)

        current_price = _safe(close)

        # VWAP - institutional magnet
        vwap_val = _vwap(today)
        vwap_dist_pct = round((current_price - vwap_val) / vwap_val * 100, 3) if current_price and vwap_val else None

        # Opening range (first 30 min)
        or_high, or_low = _opening_range(today)
        or_break = None
        if or_high and or_low and current_price:
            if current_price > or_high:
                or_break = "above"
            elif current_price < or_low:
                or_break = "below"
            else:
                or_break = "inside"

        # RSI(2) - Connors short-term mean-reversion
        rsi2 = ta.momentum.RSIIndicator(close, window=2).rsi() if len(close) >= 3 else None
        rsi2_val = _safe(rsi2)
        rsi2_signal = (
            (
                "oversold"
                if rsi2_val is not None and rsi2_val < 10
                else "overbought"
                if rsi2_val is not None and rsi2_val > 90
                else "neutral"
            )
            if rsi2_val is not None
            else None
        )

        # RSI(14) on 5-min - context
        rsi14 = ta.momentum.RSIIndicator(close, window=14).rsi() if len(close) >= 15 else None
        rsi14_val = _safe(rsi14)

        # ATR(14) on 5-min - intraday stop sizing
        atr_series = (
            ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
            if len(close) >= 15
            else None
        )
        atr_val = _safe(atr_series)
        atr_pct = round(atr_val / current_price * 100, 4) if atr_val and current_price else None

        # EMA(9/20) - intraday trend
        ema9 = ta.trend.EMAIndicator(close, window=9).ema_indicator() if len(close) >= 10 else None
        ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator() if len(close) >= 21 else None
        ema9_val = _safe(ema9)
        ema20_val = _safe(ema20)
        ema_trend = None
        if ema9_val and ema20_val:
            ema_trend = "uptrend" if ema9_val > ema20_val else "downtrend"

        # Volume spike - current bar vs 20-bar avg
        vol_sma = volume.rolling(20).mean() if len(volume) >= 20 else None
        vol_avg = _safe(vol_sma)
        cur_vol = float(volume.iloc[-1]) if not volume.empty else None
        vol_spike = round(cur_vol / vol_avg, 2) if cur_vol is not None and vol_avg and vol_avg > 0 else None

        # Cumulative volume today vs 5-day avg session volume
        session_vol = float(volume.sum()) if not volume.empty else None
        full_5d_daily_vol = None
        try:
            # Last 5 days' totals from the full df (not today slice)
            full = df["Volume"].astype(float)
            if isinstance(full.index, pd.DatetimeIndex):
                daily_totals = full.groupby(full.index.date).sum()
                if len(daily_totals) >= 2:
                    full_5d_daily_vol = float(daily_totals.iloc[:-1].mean())
        except Exception:
            _LOG.debug("suppressed exception", exc_info=True)
        rel_session_vol = (
            round(session_vol / full_5d_daily_vol, 2)
            if session_vol and full_5d_daily_vol and full_5d_daily_vol > 0
            else None
        )

        # Pre-market / overnight gap from yesterday's close
        gap_pct = None
        try:
            full_close = df["Close"]
            if isinstance(full_close.index, pd.DatetimeIndex):
                daily_last = full_close.groupby(full_close.index.date).last()
                if len(daily_last) >= 2:
                    prev_close = float(daily_last.iloc[-2])
                    today_open = float(today["Open"].iloc[0]) if not today.empty else None
                    if today_open and prev_close:
                        gap_pct = round((today_open - prev_close) / prev_close * 100, 3)
        except Exception:
            _LOG.debug("suppressed exception", exc_info=True)

        # Composite intraday score 0-1
        # VWAP-position (above = bullish): 25%
        # OR break: 20%
        # EMA9>EMA20 trend: 15%
        # RSI(2) mean-revert opportunity: 10% (high if extreme)
        # Volume spike (>1.5x): 20%
        # Gap-with-trend confluence: 10%
        vwap_pos = 1.0 if (vwap_dist_pct is not None and vwap_dist_pct > 0) else 0.0
        or_pos = 1.0 if or_break == "above" else 0.5 if or_break == "inside" else 0.0 if or_break == "below" else 0.5
        ema_pos = 1.0 if ema_trend == "uptrend" else 0.0 if ema_trend == "downtrend" else 0.5
        rsi2_pos = 1.0 if rsi2_val is not None and (rsi2_val < 10 or rsi2_val > 90) else 0.5
        vol_pos = min((vol_spike or 0) / 2.0, 1.0)
        gap_pos = (
            1.0
            if (gap_pct or 0) > 1.0 and ema_trend == "uptrend"
            else 1.0
            if (gap_pct or 0) < -1.0 and ema_trend == "downtrend"
            else 0.5
        )
        intraday_score = round(
            vwap_pos * 0.25 + or_pos * 0.20 + ema_pos * 0.15 + rsi2_pos * 0.10 + vol_pos * 0.20 + gap_pos * 0.10, 3
        )

        return {
            "current_price": current_price,
            "vwap": round(vwap_val, 4) if vwap_val else None,
            "vwap_dist_pct": vwap_dist_pct,
            "opening_range_high": round(or_high, 4) if or_high else None,
            "opening_range_low": round(or_low, 4) if or_low else None,
            "opening_range_break": or_break,
            "rsi_2": rsi2_val,
            "rsi_2_signal": rsi2_signal,
            "rsi_14": rsi14_val,
            "atr_14": atr_val,
            "atr_pct": atr_pct,
            "ema_9": ema9_val,
            "ema_20": ema20_val,
            "ema_trend": ema_trend,
            "volume_current_bar": cur_vol,
            "volume_avg_20bar": vol_avg,
            "volume_spike": vol_spike,
            "session_volume": session_vol,
            "rel_session_volume_vs_5d": rel_session_vol,
            "gap_pct": gap_pct,
            "intraday_score": intraday_score,
            "bars_analyzed": len(today),
            "session_start_et": (today.index[0].isoformat() if not today.empty else None),
        }
    except Exception as e:
        _LOG.warning(f"[technicals_intraday] compute error: {e}")
        return {}


def _slice_symbol(data, symbol: str) -> pd.DataFrame | None:
    if data is None or data.empty:
        return None
    try:
        if isinstance(data.columns, pd.MultiIndex):
            top = data.columns.get_level_values(0).unique()
            if symbol in top:
                return data[symbol]
            lvl1 = data.columns.get_level_values(-1).unique()
            if symbol in lvl1:
                return data.xs(symbol, axis=1, level=-1)
            if "Close" in lvl1 and len(top) == 1:
                return data[top[0]]
            return None
        return data
    except Exception:
        return None


def get_technicals_intraday(symbols: list[str]) -> dict[str, dict]:
    """Fetch 5-min bar indicators for symbols.

    Yahoo intraday is free, 60-day window for 5m bars. Cached 60s - bars stale fast.
    """
    now = time.time()
    out: dict[str, dict] = {}
    to_fetch: list[str] = []
    for sym in symbols:
        entry = _cache.get(sym)
        if entry and (now - entry[1]) < _CACHE_TTL:
            out[sym] = entry[0]
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return out

    try:
        data = yf.download(
            to_fetch,
            period="5d",
            interval="5m",
            progress=False,
            auto_adjust=False,  # keep raw intraday for VWAP integrity
            prepost=True,  # include extended hours for gap calc
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        _LOG.warning(f"[technicals_intraday] yfinance batch error: {e}")
        data = None

    for sym in to_fetch:
        df = _slice_symbol(data, sym) if data is not None else None
        result = _compute_from_df(df) if df is not None else {}
        _cache[sym] = (result, time.time())
        out[sym] = result

    return out
