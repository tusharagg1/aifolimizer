"""Portfolio alpha attribution and AUM benchmarking.

Two capabilities:
1. snapshot_equity(total_value_cad) - append daily equity point to
   .claude/context/portfolio_history.jsonl. Call once per day (idempotent).

2. get_alpha_attribution(positions) - load snapshot history, fetch
   benchmark OHLC via data_router, compute:
     annualized return, annualized alpha vs SPY/XEQT/TSX,
     beta, R², information ratio, tracking error, Sharpe, max DD.
   Also compare to Wealthsimple Managed published profile returns.

Wealthsimple Managed returns embedded from publicly-reported figures
(updated manually - these are approximate 2022-2025 1yr/3yr/5yr).
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.services import data_router

_CTX = Path(__file__).resolve().parents[2] / ".claude" / "context"
_HIST_FILE = _CTX / "portfolio_history.jsonl"

_BENCHMARKS = {
    "SPY": "S&P 500 (USD)",
    "XEQT.TO": "XEQT Global Equity",
    "^GSPTSE": "TSX Composite",
    "QQQ": "Nasdaq-100",
}

# Wealthsimple Managed published approximate annualized returns (CA, gross).
# Source: Wealthsimple Performance Disclosure 2025.
# Update annually - these are best-effort public figures.
_WS_MANAGED = {
    "conservative": {"1y": 9.2, "3y": 4.1, "5y": 5.6},
    "balanced": {"1y": 14.8, "3y": 7.3, "5y": 9.1},
    "growth": {"1y": 20.3, "3y": 10.1, "5y": 12.2},
    "aggressive": {"1y": 25.7, "3y": 12.8, "5y": 14.6},
    "halal_growth": {"1y": 18.9, "3y": 9.2, "5y": 11.4},
}


def snapshot_equity(total_value_cad: float) -> dict:
    """Append today's portfolio NAV to portfolio_history.jsonl.

    Idempotent per day - overwrites today's entry if called again.
    """
    today = date.today().isoformat()
    _CTX.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    if _HIST_FILE.exists():
        for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("date") != today:
                rows.append(r)

    entry = {"date": today, "ts": time.time(), "total_cad": round(total_value_cad, 2)}
    rows.append(entry)

    with _HIST_FILE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    return entry


def get_alpha_attribution(
    positions: list[dict] | None = None,
    lookback_days: int = 365,
) -> dict:
    """Full alpha attribution report.

    positions: [{symbol, weight}] - used to compute weighted returns for
    periods not yet in snapshot history. If None, uses snapshot history only.
    lookback_days: how many history days to include.
    """
    hist = _load_history(lookback_days)
    bench_data = _fetch_benchmarks(lookback_days)
    port_series = _hist_to_series(hist)

    result: dict = {
        "as_of": time.time(),
        "lookback_days": lookback_days,
        "snapshot_days": len(hist),
        "portfolio_curve": {},
        "benchmarks": {},
        "alpha": {},
        "regression": {},
        "ws_managed_comparison": _WS_MANAGED,
    }

    if len(port_series) < 5:
        result["warning"] = (
            "fewer than 5 portfolio snapshots - run snapshot_equity daily "
            "to build a meaningful curve. Showing benchmark returns only."
        )
        for bsym, bname in _BENCHMARKS.items():
            bd = bench_data.get(bsym)
            if bd is not None and len(bd) >= 2:
                b_ret = _total_return(bd)
                b_ann = _annualize(b_ret, bd)
                result["benchmarks"][bsym] = {
                    "name": bname,
                    "total_return_pct": b_ret,
                    "annualized_return_pct": b_ann,
                }
        return result

    port_ret_total = _total_return(port_series)
    port_ret_ann = _annualize(port_ret_total, port_series)
    daily_ret = port_series.pct_change().dropna()
    port_sharpe = _sharpe(daily_ret)
    port_mdd = _max_dd(port_series)

    result["portfolio_curve"] = {
        "first_date": port_series.index[0].strftime("%Y-%m-%d")
        if hasattr(port_series.index[0], "strftime")
        else str(port_series.index[0]),
        "last_date": port_series.index[-1].strftime("%Y-%m-%d")
        if hasattr(port_series.index[-1], "strftime")
        else str(port_series.index[-1]),
        "total_return_pct": port_ret_total,
        "annualized_return_pct": port_ret_ann,
        "sharpe": port_sharpe,
        "max_drawdown_pct": port_mdd,
    }

    for bsym, bname in _BENCHMARKS.items():
        bd = bench_data.get(bsym)
        if bd is None or len(bd) < 5:
            result["benchmarks"][bsym] = {"name": bname, "error": "insufficient data"}
            continue

        b_ret = _total_return(bd)
        b_ann = _annualize(b_ret, bd)
        b_daily = bd.pct_change().dropna()

        # Align series to shared dates
        merged = pd.concat([daily_ret, b_daily], axis=1, join="inner").dropna()
        merged.columns = ["port", "bench"]

        alpha_total = round(port_ret_total - b_ret, 2) if b_ret is not None else None
        alpha_ann = round(port_ret_ann - b_ann, 2) if (b_ann is not None and port_ret_ann is not None) else None

        beta, r2, info_ratio, tracking_err = None, None, None, None
        if len(merged) >= 20:
            cov = np.cov(merged["port"], merged["bench"])
            bench_var = cov[1, 1]
            if bench_var > 0:
                beta = round(float(cov[0, 1] / bench_var), 3)
            corr = merged.corr().iloc[0, 1]
            r2 = round(float(corr**2), 3)
            active = merged["port"] - merged["bench"]
            tracking_err = round(float(active.std() * math.sqrt(252) * 100), 2)
            if active.std() > 0:
                info_ratio = round(float(active.mean() / active.std() * math.sqrt(252)), 3)

        result["benchmarks"][bsym] = {
            "name": bname,
            "total_return_pct": b_ret,
            "annualized_return_pct": b_ann,
            "sharpe": _sharpe(b_daily),
        }
        result["alpha"][bsym] = {
            "alpha_total_pct": alpha_total,
            "alpha_annualized_pct": alpha_ann,
        }
        result["regression"][bsym] = {
            "beta": beta,
            "r_squared": r2,
            "information_ratio": info_ratio,
            "tracking_error_pct_ann": tracking_err,
        }

    return result


def _load_history(lookback_days: int) -> list[dict]:
    if not _HIST_FILE.exists():
        return []
    cutoff_ts = time.time() - lookback_days * 86400
    rows = []
    for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("ts", 0) >= cutoff_ts:
            rows.append(r)
    return sorted(rows, key=lambda x: x["date"])


def _hist_to_series(hist: list[dict]) -> pd.Series:
    if not hist:
        return pd.Series(dtype=float)
    df = pd.DataFrame(hist)
    df["dt"] = pd.to_datetime(df["date"])
    df = df.set_index("dt").sort_index()
    return df["total_cad"].astype(float)


def _fetch_benchmarks(lookback_days: int) -> dict[str, pd.Series]:
    period = _period_str(lookback_days)
    out: dict[str, pd.Series] = {}
    for sym in _BENCHMARKS:
        try:
            bars = data_router.get_history(sym, period=period, interval="1d")
            if bars:
                df = pd.DataFrame(bars)
                df["dt"] = pd.to_datetime(df["date"])
                df = df.set_index("dt").sort_index()
                out[sym] = df["close"].astype(float)
        except Exception:
            logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    return out


def _period_str(days: int) -> str:
    if days <= 31:
        return "1mo"
    if days <= 93:
        return "3mo"
    if days <= 186:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 365 * 3:
        return "3y"
    return "5y"


def _total_return(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 2:
        return None
    return round((float(s.iloc[-1]) / float(s.iloc[0]) - 1) * 100, 2)


def _annualize(total_ret_pct: float | None, series: pd.Series) -> float | None:
    """Annualize a total return over the calendar span of `series`.

    Earlier impl divided len(series) by 365.25, which conflated trading bars
    (~252/yr) with calendar days; a 1y benchmark with 252 bars came out
    annualized at ~14.9% for a true 10% return. Use the calendar delta of
    the index instead.
    """
    if total_ret_pct is None or series is None or len(series) < 2:
        return None
    try:
        days = (series.index[-1] - series.index[0]).days
    except Exception:
        days = len(series)
    if days <= 0:
        return None
    years = days / 365.25
    try:
        return round(((1 + total_ret_pct / 100) ** (1 / years) - 1) * 100, 2)
    except Exception:
        return None


def _sharpe(daily_ret: pd.Series) -> float | None:
    if daily_ret.empty or daily_ret.std() == 0:
        return None
    return round(float(daily_ret.mean() / daily_ret.std() * math.sqrt(252)), 3)


def _max_dd(equity: pd.Series) -> float | None:
    if equity.empty:
        return None
    peak = equity.cummax()
    return round(float(((equity - peak) / peak).min() * 100), 2)
