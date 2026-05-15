import time
import pandas as pd
import ta
import yfinance as yf

_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 3600  # 1 hour


def _cached(symbol: str, fetch_fn) -> dict:
    entry = _cache.get(symbol)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    result = fetch_fn(symbol)
    _cache[symbol] = (result, time.time())
    return result


def _safe(series: pd.Series | None) -> float | None:
    try:
        if series is None or series.empty:
            return None
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


def _fetch_one(symbol: str) -> dict:
    try:
        df = yf.download(
            symbol, period="1y", interval="1d", progress=False, auto_adjust=True
        )
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

        # 52-week high/low (use full download period)
        high_col = df["High"].squeeze() if "High" in df.columns else None
        low_col = df["Low"].squeeze() if "Low" in df.columns else None
        week52_high = float(high_col.max()) if high_col is not None and not high_col.empty else None
        week52_low = float(low_col.min()) if low_col is not None and not low_col.empty else None
        pct_from_52w_high = (
            round((current_price - week52_high) / week52_high * 100, 2)
            if current_price and week52_high else None
        )
        pct_from_52w_low = (
            round((current_price - week52_low) / week52_low * 100, 2)
            if current_price and week52_low else None
        )

        # SMA200 slope: % change over last 21 trading days
        sma200_slope = None
        if len(sma200) >= 22:
            s_now = sma200.iloc[-1]
            s_21ago = sma200.iloc[-22]
            if pd.notna(s_now) and pd.notna(s_21ago) and float(s_21ago) != 0:
                sma200_slope = round((float(s_now) - float(s_21ago)) / float(s_21ago) * 100, 3)

        # Minervini trend template (7 criteria)
        sma200_rising = (sma200_slope or 0) > 0.5
        price_above_sma200 = bool(current_price and sma200_val and current_price > sma200_val)
        checks = [
            bool(current_price and sma150_val and current_price > sma150_val),
            price_above_sma200,
            bool(sma150_val and sma200_val and sma150_val > sma200_val),
            sma200_rising,
            bool(sma50_val and sma150_val and sma50_val > sma150_val),
            bool(current_price and week52_low and current_price >= week52_low * 1.30),
            bool(current_price and week52_high and current_price >= week52_high * 0.75),
        ]
        minervini_score = sum(checks)

        # Wyckoff stage (1=basing, 2=uptrend, 3=distribution, 4=decline)
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
        print(f"[technicals] {symbol}: {e}")
        return {}


def get_technicals(symbols: list[str]) -> dict[str, dict]:
    return {sym: _cached(sym, _fetch_one) for sym in symbols}
