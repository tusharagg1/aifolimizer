"""Fama-French factor data via the Ken French Data Library (free, no key).

Daily factor returns (Mkt-RF, SMB, HML, RMW, CMA, RF) + momentum (Mom), plus
an OLS factor-exposure regression for any ticker → betas to market, size,
value, profitability, investment, momentum, with annualized alpha + R^2.
Upgrades risk-assessment from single-beta to multi-factor attribution.

Factors are US-market based — loadings for non-US tickers are measured against
US factors and should be read as directional, not precise.

Source (zipped latin-1 CSVs):
  https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/
"""

from __future__ import annotations

import io
import time
import zipfile
from typing import Any

import httpx
import numpy as np
import pandas as pd

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.fama_french")

_FTP = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
_FF5 = f"{_FTP}/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
_MOM = f"{_FTP}/F-F_Momentum_Factor_daily_CSV.zip"
_HDR = {"User-Agent": "aifolimizer/1.0 (open-source portfolio analytics)"}
_TIMEOUT = 30.0
_TTL = 24 * 3600  # factor data updates at most daily
_cache: tuple[pd.DataFrame, float] | None = None

_FACTOR_LABELS = {
    "Mkt-RF": "market",
    "SMB": "size",
    "HML": "value",
    "RMW": "profitability",
    "CMA": "investment",
    "Mom": "momentum",
}


def _parse_zip(url: str) -> pd.DataFrame:
    r = httpx.get(url, headers=_HDR, timeout=_TIMEOUT)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode("latin-1").splitlines()
    rows: list[list[str]] = []
    cols: list[str] | None = None
    for line in raw:
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0].isdigit() and len(parts[0]) == 8:
            rows.append(parts)
        elif "Mkt-RF" in line or line.strip().endswith("Mom"):
            cols = [c for c in parts if c]  # drop empty leading column
    if not rows or not cols:
        raise ValueError(f"no parseable data in {url}")
    df = pd.DataFrame(rows).set_index(0)
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.apply(pd.to_numeric, errors="coerce") / 100.0  # percent → decimal
    df.columns = cols[: df.shape[1]]
    return df


def _load() -> pd.DataFrame:
    global _cache
    now = time.time()
    if _cache and now - _cache[1] < _TTL:
        return _cache[0]
    ff5 = _parse_zip(_FF5)
    mom = _parse_zip(_MOM)
    df = ff5.join(mom, how="inner").dropna()
    _cache = (df, now)
    return df


def factor_snapshot() -> dict[str, Any]:
    """Latest daily + trailing 21d/252d factor returns. Cached 24h."""
    try:
        df = _load()
    except Exception as e:
        _LOG.warning(f"[fama_french] load failed: {e}")
        return {"error": "fetch_failed", "data_source": "Ken French Data Library"}

    latest = df.iloc[-1]

    def trail(n: int) -> dict[str, float]:
        return (df.tail(n).sum() * 100).round(2).to_dict()

    return {
        "as_of": df.index[-1].date().isoformat(),
        "latest_daily_pct": (latest * 100).round(3).to_dict(),
        "trailing_21d_pct": trail(21),
        "trailing_252d_pct": trail(252),
        "factor_legend": _FACTOR_LABELS,
        "data_source": "Ken French Data Library (free, no key)",
    }


def factor_exposure(ticker: str, lookback_days: int = 252) -> dict[str, Any]:
    """Regress ticker excess returns on FF5+Mom → factor betas. Cached via _load."""
    lookback_days = max(60, min(int(lookback_days), 1260))
    sym = ticker.strip().upper()
    try:
        df = _load()
    except Exception as e:
        _LOG.warning(f"[fama_french] load failed: {e}")
        return {"error": "fetch_failed", "data_source": "Ken French Data Library"}

    try:
        import yfinance as yf

        hist = yf.Ticker(sym).history(period="3y", auto_adjust=True)
    except Exception as e:
        _LOG.warning(f"[fama_french] yfinance {sym}: {e}")
        return {"error": "price_fetch_failed", "ticker": sym}

    if hist is None or hist.empty or "Close" not in hist:
        return {"error": "no_price_data", "ticker": sym}

    ret = hist["Close"].pct_change().dropna()
    ret.index = ret.index.tz_localize(None).normalize()
    ret.name = "ret"

    merged = df.join(ret, how="inner").dropna().tail(lookback_days)
    if len(merged) < 60:
        return {"error": "insufficient_overlap", "ticker": sym, "n_obs": len(merged)}

    factors = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
    y = (merged["ret"] - merged["RF"]).to_numpy()
    x = merged[factors].to_numpy()
    xc = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(xc, y, rcond=None)
    resid = y - xc @ beta
    ss_res = float((resid**2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = round(1 - ss_res / ss_tot, 3) if ss_tot else None

    loadings = {
        _FACTOR_LABELS[f]: round(float(b), 3) for f, b in zip(factors, beta[1:])
    }
    return {
        "ticker": sym,
        "n_obs": len(merged),
        "window_end": merged.index[-1].date().isoformat(),
        "alpha_annual_pct": round(float(beta[0]) * 252 * 100, 2),
        "loadings": loadings,  # beta to each factor
        "r_squared": r2,
        "note": "US factors; non-US tickers directional only.",
        "data_source": "Ken French Data Library (free, no key)",
    }
