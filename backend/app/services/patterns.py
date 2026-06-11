"""
Chart pattern detection: double top/bottom, head & shoulders, inverse H&S.
Uses local pivot points on daily close price - no external deps beyond numpy/pandas.
"""

import time
import pandas as pd
import yfinance as yf
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.patterns")


_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 3600


def _find_pivots(high: pd.Series, low: pd.Series, order: int = 5) -> tuple[list[int], list[int]]:
    """Indices of local highs (from High series) and local lows (from Low series).

    Using intraday High for peak detection and Low for trough detection so spike
    bars that closed off their extremes are not missed.
    """
    highs, lows = [], []
    n = len(high)
    for i in range(order, n - order):
        h_win = high.iloc[i - order : i + order + 1]
        l_win = low.iloc[i - order : i + order + 1]
        if high.iloc[i] == h_win.max():
            highs.append(i)
        if low.iloc[i] == l_win.min():
            lows.append(i)
    return highs, lows


def _pct_diff(a: float, b: float) -> float:
    return abs(a - b) / ((a + b) / 2) * 100


def _detect_double_top(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    highs: list[int],
    tol: float = 3.0,
) -> dict | None:
    """Two peaks within `tol`% of each other with a valley between them.

    Peak prices come from intraday High; valley/neckline from intraday Low;
    breakout confirmation uses Close (close-below-neckline avoids wick noise).
    """
    if len(highs) < 2:
        return None
    for i in range(len(highs) - 1):
        for j in range(i + 1, len(highs)):
            p1, p2 = highs[i], highs[j]
            if p2 - p1 < 10:
                continue
            h1, h2 = float(high.iloc[p1]), float(high.iloc[p2])
            if _pct_diff(h1, h2) > tol:
                continue
            valley = float(low.iloc[p1:p2].min())
            neck = valley
            cur = float(close.iloc[-1])
            confirmed = cur < neck * 0.99
            return {
                "pattern": "double_top",
                "peak1_idx": p1,
                "peak2_idx": p2,
                "peak1_price": round(h1, 4),
                "peak2_price": round(h2, 4),
                "neckline": round(neck, 4),
                "confirmed": confirmed,
                "bearish": True,
                "description": f"Double top ~${h1:.2f}/{h2:.2f}, neckline ${neck:.2f}",
            }
    return None


def _detect_double_bottom(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lows: list[int],
    tol: float = 3.0,
) -> dict | None:
    """Two troughs within `tol`% of each other with a peak between them.

    Trough prices come from intraday Low; intervening peak/neckline from
    intraday High; breakout confirmation uses Close.
    """
    if len(lows) < 2:
        return None
    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            t1, t2 = lows[i], lows[j]
            if t2 - t1 < 10:
                continue
            l1, l2 = float(low.iloc[t1]), float(low.iloc[t2])
            if _pct_diff(l1, l2) > tol:
                continue
            peak = float(high.iloc[t1:t2].max())
            neck = peak
            cur = float(close.iloc[-1])
            confirmed = cur > neck * 1.01
            return {
                "pattern": "double_bottom",
                "trough1_idx": t1,
                "trough2_idx": t2,
                "trough1_price": round(l1, 4),
                "trough2_price": round(l2, 4),
                "neckline": round(neck, 4),
                "confirmed": confirmed,
                "bearish": False,
                "description": f"Double bottom ~${l1:.2f}/{l2:.2f}, neckline ${neck:.2f}",
            }
    return None


def _detect_head_and_shoulders(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    highs: list[int],
    tol: float = 5.0,
) -> dict | None:
    """Three peaks: middle (head) clearly higher than shoulders.

    Shoulder + head prices from intraday High; neckline from intraday Low
    between shoulders; breakout confirmation uses Close.
    """
    if len(highs) < 3:
        return None
    for i in range(len(highs) - 2):
        ls_idx, h_idx, rs_idx = highs[i], highs[i + 1], highs[i + 2]
        if h_idx - ls_idx < 5 or rs_idx - h_idx < 5:
            continue
        ls = float(high.iloc[ls_idx])
        head = float(high.iloc[h_idx])
        rs = float(high.iloc[rs_idx])
        if head <= max(ls, rs):
            continue
        if _pct_diff(ls, rs) > tol:
            continue
        neck_l = float(low.iloc[ls_idx:h_idx].min())
        neck_r = float(low.iloc[h_idx:rs_idx].min())
        neckline = (neck_l + neck_r) / 2
        cur = float(close.iloc[-1])
        confirmed = cur < neckline * 0.99
        return {
            "pattern": "head_and_shoulders",
            "left_shoulder_idx": ls_idx,
            "head_idx": h_idx,
            "right_shoulder_idx": rs_idx,
            "left_shoulder_price": round(ls, 4),
            "head_price": round(head, 4),
            "right_shoulder_price": round(rs, 4),
            "neckline": round(neckline, 4),
            "confirmed": confirmed,
            "bearish": True,
            "description": f"H&S: shoulders ~${ls:.2f}/${rs:.2f}, head ${head:.2f}, neck ${neckline:.2f}",
        }
    return None


def _detect_inverse_head_and_shoulders(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lows: list[int],
    tol: float = 5.0,
) -> dict | None:
    """Three troughs: middle (head) clearly lower than shoulders.

    Shoulder + head prices from intraday Low; neckline from intraday High
    between shoulders; breakout confirmation uses Close.
    """
    if len(lows) < 3:
        return None
    for i in range(len(lows) - 2):
        ls_idx, h_idx, rs_idx = lows[i], lows[i + 1], lows[i + 2]
        if h_idx - ls_idx < 5 or rs_idx - h_idx < 5:
            continue
        ls = float(low.iloc[ls_idx])
        head = float(low.iloc[h_idx])
        rs = float(low.iloc[rs_idx])
        if head >= min(ls, rs):
            continue
        if _pct_diff(ls, rs) > tol:
            continue
        neck_l = float(high.iloc[ls_idx:h_idx].max())
        neck_r = float(high.iloc[h_idx:rs_idx].max())
        neckline = (neck_l + neck_r) / 2
        cur = float(close.iloc[-1])
        confirmed = cur > neckline * 1.01
        return {
            "pattern": "inverse_head_and_shoulders",
            "left_shoulder_idx": ls_idx,
            "head_idx": h_idx,
            "right_shoulder_idx": rs_idx,
            "left_shoulder_price": round(ls, 4),
            "head_price": round(head, 4),
            "right_shoulder_price": round(rs, 4),
            "neckline": round(neckline, 4),
            "confirmed": confirmed,
            "bearish": False,
            "description": f"Inv H&S: shoulders ~${ls:.2f}/${rs:.2f}, head ${head:.2f}, neck ${neckline:.2f}",
        }
    return None


def detect_patterns(symbol: str, period: str = "1y") -> dict:
    now = time.time()
    key = f"{symbol}:{period}"
    cached = _cache.get(key)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return {"symbol": symbol, "patterns": [], "dates": [], "close": []}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Align High/Low/Close on shared non-null index - pivot indices then
        # reference the same row across all three series.
        ohlc = df[["High", "Low", "Close"]].dropna()
        if len(ohlc) < 30:
            return {"symbol": symbol, "patterns": [], "dates": [], "close": []}
        high = ohlc["High"].squeeze()
        low = ohlc["Low"].squeeze()
        close = ohlc["Close"].squeeze()
        dates = [str(d)[:10] for d in close.index.tolist()]
        prices = [round(float(v), 4) for v in close.tolist()]

        highs, lows = _find_pivots(high, low, order=5)

        patterns: list[dict] = []
        for fn, args in [
            (_detect_double_top, (high, low, close, highs)),
            (_detect_double_bottom, (high, low, close, lows)),
            (_detect_head_and_shoulders, (high, low, close, highs)),
            (_detect_inverse_head_and_shoulders, (high, low, close, lows)),
        ]:
            result = fn(*args)
            if result:
                # convert idx → date for frontend
                for key_name in (
                    "peak1_idx",
                    "peak2_idx",
                    "trough1_idx",
                    "trough2_idx",
                    "left_shoulder_idx",
                    "head_idx",
                    "right_shoulder_idx",
                ):
                    if key_name in result:
                        idx = result.pop(key_name)
                        result[key_name.replace("_idx", "_date")] = dates[idx] if idx < len(dates) else None
                patterns.append(result)

        result_data = {"symbol": symbol, "patterns": patterns, "dates": dates, "close": prices}
        _cache[key] = (result_data, time.time())
        return result_data

    except Exception as e:
        _LOG.warning(f"[patterns] {symbol}: {e}")
        return {"symbol": symbol, "patterns": [], "dates": [], "close": []}
