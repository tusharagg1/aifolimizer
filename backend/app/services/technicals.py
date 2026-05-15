import time
import pandas as pd
import ta
import yfinance as yf

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
        }
    except Exception as e:
        print(f"[technicals] compute error: {e}")
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


def get_technicals(symbols: list[str]) -> dict[str, dict]:
    """Fetch indicators for symbols. Batches uncached symbols into one
    yf.download call (one HTTP round-trip vs N).
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
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        print(f"[technicals] batch download error: {e}")
        data = None

    for sym in to_fetch:
        df = _slice_symbol(data, sym) if data is not None else None
        result = _compute_from_df(df) if df is not None else {}
        _cache[sym] = (result, time.time())
        out[sym] = result
    return out
