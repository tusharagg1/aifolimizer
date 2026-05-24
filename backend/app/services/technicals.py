import time

import pandas as pd
import ta
import yfinance as yf
from app.security import get_logger
from app.services.data_sources.massive_src import is_tsx

_LOG = get_logger("aifolimizer.services.technicals")


_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 3600  # 1 hour


def _safe(series: pd.Series | None) -> float | None:
    try:
        if series is None or series.empty:
            return None
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


def _compute_from_df(df: pd.DataFrame) -> dict:
    """Indicators from 1y daily OHLCV. Returns {} if insufficient data."""
    try:
        if df is None or df.empty or len(df) < 21:
            return {}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"].squeeze()
        volume = df["Volume"].squeeze()

        sma20 = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        sma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        sma150 = ta.trend.SMAIndicator(close, window=150).sma_indicator()
        sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator()

        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()

        macd_obj = ta.trend.MACD(close)
        macd_val = _safe(macd_obj.macd())
        macd_signal = _safe(macd_obj.macd_signal())
        macd_hist = _safe(macd_obj.macd_diff())

        bb = ta.volatility.BollingerBands(close, window=20)
        bb_upper = _safe(bb.bollinger_hband())
        bb_mid = _safe(bb.bollinger_mavg())
        bb_lower = _safe(bb.bollinger_lband())

        vol_sma = ta.trend.SMAIndicator(
            volume.astype(float), window=20
        ).sma_indicator()

        current_price = _safe(close)
        sma50_val = _safe(sma50)
        sma150_val = _safe(sma150)
        sma200_val = _safe(sma200)
        rsi_val = _safe(rsi)

        high_col = df["High"].squeeze() if "High" in df.columns else None
        low_col = df["Low"].squeeze() if "Low" in df.columns else None
        week52_high = (
            float(high_col.max())
            if high_col is not None and not high_col.empty else None
        )
        week52_low = (
            float(low_col.min())
            if low_col is not None and not low_col.empty else None
        )
        pct_from_52w_high = (
            round((current_price - week52_high) / week52_high * 100, 2)
            if current_price and week52_high else None
        )
        pct_from_52w_low = (
            round((current_price - week52_low) / week52_low * 100, 2)
            if current_price and week52_low else None
        )

        sma200_slope = None
        if len(sma200) >= 22:
            s_now = sma200.iloc[-1]
            s_21ago = sma200.iloc[-22]
            if pd.notna(s_now) and pd.notna(s_21ago) and float(s_21ago) != 0:
                sma200_slope = round(
                    (float(s_now) - float(s_21ago)) / float(s_21ago) * 100, 3
                )

        sma200_rising = (sma200_slope or 0) > 0.5
        price_above_sma200 = bool(
            current_price and sma200_val and current_price > sma200_val
        )
        checks = [
            bool(current_price and sma150_val and current_price > sma150_val),
            price_above_sma200,
            bool(sma150_val and sma200_val and sma150_val > sma200_val),
            sma200_rising,
            bool(sma50_val and sma150_val and sma50_val > sma150_val),
            bool(
                current_price and week52_low
                and current_price >= week52_low * 1.30
            ),
            bool(
                current_price and week52_high
                and current_price >= week52_high * 0.75
            ),
        ]
        minervini_score = sum(checks)

        if price_above_sma200 and sma200_rising:
            stage = 2
        elif price_above_sma200 and not sma200_rising:
            stage = 3
        elif not price_above_sma200 and sma200_rising:
            stage = 1
        else:
            stage = 4

        if current_price and sma200_val:
            trend = "uptrend" if current_price > sma200_val else "downtrend"
        else:
            trend = "sideways"

        if rsi_val is not None:
            if rsi_val > 70:
                rsi_signal = "overbought"
            elif rsi_val < 30:
                rsi_signal = "oversold"
            else:
                rsi_signal = "neutral"
        else:
            rsi_signal = "neutral"

        # Classic floor pivot from last closed bar ([-2] avoids partial intraday bar)
        pivot_levels: dict | None = None
        if high_col is not None and low_col is not None and len(df) >= 2:
            try:
                ph = float(high_col.iloc[-2])
                pl = float(low_col.iloc[-2])
                pc = float(close.iloc[-2])
                if all(pd.notna(v) for v in (ph, pl, pc)):
                    piv = (ph + pl + pc) / 3
                    pivot_levels = {
                        "pivot": round(piv, 4),
                        "r1": round(2 * piv - pl, 4),
                        "s1": round(2 * piv - ph, 4),
                        "r2": round(piv + (ph - pl), 4),
                        "s2": round(piv - (ph - pl), 4),
                    }
            except Exception:
                pass

        # Current volume vs 20-day average
        cur_vol = df["Volume"].squeeze().iloc[-1] if "Volume" in df.columns else None
        vol_sma_val = _safe(vol_sma)
        volume_score: float | None = None
        if cur_vol is not None and vol_sma_val and vol_sma_val > 0:
            try:
                volume_score = round(float(cur_vol) / vol_sma_val, 2)
            except Exception:
                pass

        # ATR(14) — average true range for volatility/stop sizing
        atr_series = ta.volatility.AverageTrueRange(
            high_col, low_col, close, window=14
        ).average_true_range() if high_col is not None and low_col is not None else None
        atr_val = _safe(atr_series)
        atr_pct = round(atr_val / current_price * 100, 3) if atr_val and current_price else None

        # ADX(14) — trend strength; >25 = strong trend
        adx_obj = ta.trend.ADXIndicator(high_col, low_col, close, window=14) \
            if high_col is not None and low_col is not None else None
        adx_val = _safe(adx_obj.adx()) if adx_obj else None
        adx_signal = (
            "strong" if adx_val and adx_val > 25
            else "weak" if adx_val and adx_val < 20
            else "moderate"
        ) if adx_val is not None else None

        # Stochastic(14,3) — K and D lines
        stoch_obj = ta.momentum.StochasticOscillator(
            high_col, low_col, close, window=14, smooth_window=3
        ) if high_col is not None and low_col is not None else None
        stoch_k = _safe(stoch_obj.stoch()) if stoch_obj else None
        stoch_d = _safe(stoch_obj.stoch_signal()) if stoch_obj else None
        stoch_signal = (
            "overbought" if stoch_k and stoch_k > 80
            else "oversold" if stoch_k and stoch_k < 20
            else "neutral"
        ) if stoch_k is not None else None

        # OBV — on-balance volume trend
        obv_series = ta.volume.OnBalanceVolumeIndicator(close, volume.astype(float)).on_balance_volume()
        obv_val = _safe(obv_series)
        obv_trend: str | None = None
        if obv_series is not None and len(obv_series) >= 20:
            obv_sma = obv_series.rolling(20).mean()
            obv_now = obv_series.iloc[-1]
            obv_avg = obv_sma.iloc[-1]
            if pd.notna(obv_now) and pd.notna(obv_avg):
                obv_trend = "rising" if float(obv_now) > float(obv_avg) else "falling"

        # Composite technical score 0-1: minervini(35%) + trend(20%) + RSI(15%) + MACD(10%) + ADX(10%) + stoch(5%) + volume(5%)
        mnv = (minervini_score / 7)
        trn = 1.0 if trend == "uptrend" else (0.5 if trend == "sideways" else 0.0)
        rsi_pos = (
            1.0 if (rsi_val is not None and 40 <= rsi_val <= 65)
            else 0.5 if (rsi_val is not None and (30 <= rsi_val < 40 or 65 < rsi_val <= 70))
            else 0.0
        )
        macd_pos = 1.0 if (macd_hist is not None and macd_hist > 0) else 0.0
        adx_pos = min((adx_val or 0) / 50, 1.0)
        stoch_pos = (
            1.0 if (stoch_k is not None and 20 <= stoch_k <= 80)
            else 0.5 if stoch_k is not None
            else 0.0
        )
        vol_pos = min(volume_score or 0.0, 2.0) / 2.0
        technical_score = round(
            mnv * 0.35 + trn * 0.20 + rsi_pos * 0.15 + macd_pos * 0.10
            + adx_pos * 0.10 + stoch_pos * 0.05 + vol_pos * 0.05, 3
        )

        return {
            "sma_20": _safe(sma20),
            "sma_50": sma50_val,
            "sma_150": sma150_val,
            "sma_200": sma200_val,
            "sma_200_slope_pct": sma200_slope,
            "rsi_14": rsi_val,
            "macd": macd_val,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "atr_14": atr_val,
            "atr_pct": atr_pct,
            "adx_14": adx_val,
            "adx_signal": adx_signal,
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "stoch_signal": stoch_signal,
            "obv": obv_val,
            "obv_trend": obv_trend,
            "volume_sma_20": _safe(vol_sma),
            "current_price": current_price,
            "week52_high": week52_high,
            "week52_low": week52_low,
            "pct_from_52w_high": pct_from_52w_high,
            "pct_from_52w_low": pct_from_52w_low,
            "stage": stage,
            "minervini_score": minervini_score,
            "trend": trend,
            "rsi_signal": rsi_signal,
            "pivot_levels": pivot_levels,
            "volume_score": volume_score,
            "technical_score": technical_score,
        }
    except Exception as e:
        _LOG.warning(f"[technicals] compute error: {e}")
        return {}


def _slice_symbol(data, symbol: str) -> pd.DataFrame | None:
    """Extract single-symbol OHLCV from yf.download multi-ticker result.
    Handles flat columns (single sym, no group_by) and MultiIndex (group_by=ticker).
    """
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
            # Single symbol + group_by=ticker still wraps; if "Close" is on
            # any level, reuse data as-is (flatten happens in _compute).
            if "Close" in lvl1 and len(top) == 1:
                return data[top[0]]
            return None
        return data
    except Exception:
        return None


def _fetch_massive_ohlcv(symbol: str) -> pd.DataFrame | None:
    """1y daily OHLCV from Massive, returns yfinance-compatible DataFrame."""
    import os
    from datetime import datetime, timedelta
    key = os.environ.get("MASSIVE_API_KEY", "")
    if not key:
        return None
    try:
        from massive import RESTClient
        client = RESTClient(api_key=key)
        to_dt = datetime.now()
        from_dt = to_dt - timedelta(days=370)
        records = []
        for agg in client.list_aggs(
            ticker=symbol,
            multiplier=1,
            timespan="day",
            from_=from_dt.strftime("%Y-%m-%d"),
            to=to_dt.strftime("%Y-%m-%d"),
            limit=500,
            adjusted=True,
        ):
            try:
                records.append({
                    "Date": pd.Timestamp(agg.timestamp, unit="ms"),
                    "Open": float(agg.open),
                    "High": float(agg.high),
                    "Low": float(agg.low),
                    "Close": float(agg.close),
                    "Volume": float(agg.volume or 0),
                })
            except Exception:
                continue
        if len(records) < 21:
            return None
        df = pd.DataFrame(records).set_index("Date").sort_index()
        df.index = df.index.tz_localize(None)
        return df
    except Exception as e:
        _LOG.warning(f"[technicals] massive ohlcv {symbol}: {e}")
        return None


def get_technicals(symbols: list[str]) -> dict[str, dict]:
    """Fetch indicators for symbols.

    US symbols: Massive OHLCV (falls back to yfinance).
    TSX symbols: yfinance batch download.
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

    needs_yfinance: list[str] = []
    for sym in to_fetch:
        if not is_tsx(sym):
            df = _fetch_massive_ohlcv(sym)
            if df is not None:
                result = _compute_from_df(df)
                _cache[sym] = (result, time.time())
                out[sym] = result
                continue
        needs_yfinance.append(sym)

    if needs_yfinance:
        try:
            data = yf.download(
                needs_yfinance,
                period="1y",
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            _LOG.warning(f"[technicals] yfinance batch error: {e}")
            data = None
        for sym in needs_yfinance:
            df = _slice_symbol(data, sym) if data is not None else None
            result = _compute_from_df(df) if df is not None else {}
            _cache[sym] = (result, time.time())
            out[sym] = result
    return out
