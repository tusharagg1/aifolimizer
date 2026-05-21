"""Per-position backtesting against simple rule-based strategies.

Strategies:
- buy_hold       baseline; buy at start, sell at end
- rsi_swing      buy when RSI(14) < 30, sell when RSI > 70
- sma_cross      buy when close > SMA50, sell when close < SMA50
- crowd_fade     sma_cross long-only, but skip entirely if current crowding
                 label == "consensus" (Goldman/BlackRock 2025: late entries on
                 consensus names have negative expected alpha)
- crowd_buy      sma_cross long-only, but ONLY when current crowding label ==
                 "contrarian" (positive-edge mirror of crowd_fade)

Metrics (per symbol per strategy):
- total_return_pct, cagr_pct, sharpe (rf=0), max_drawdown_pct, num_trades

Output also shows delta_vs_buy_hold so user sees if active rules added value.
Cached 1h per (symbol, strategy, lookback_days, tx_cost_bps).

Transaction cost: `tx_cost_bps` deducted per trade leg (one buy + one sell =
2 * tx_cost_bps). Default 5 bps/leg — conservative for commission-free retail
with bid-ask spread. Pass tx_cost_bps=0 for old behavior.

Honest caveat on crowd_fade / crowd_buy: positioning data is point-in-time
(today's snapshot). Strategies effectively answer "what if I had used today's
crowding label as a filter over the entire lookback?". They are NOT a true
walk-forward backtest because there is no historical crowding-score series.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pandas as pd
import ta
import yfinance as yf

from app.services import positioning as positioning_svc
from app.services.quant import deterministic_split_idx
from app.services.backtest_validation import run_validation
from app.services.run_card import generate_run_card, save_run_card
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.backtest")


_CACHE: dict[tuple, tuple[dict, float]] = {}
_CACHE_TTL = 3600

# Close-price cache shared across strategies for the same symbol+period.
# Lets backtest_portfolio batch-fetch all uncached symbols in one yf.download
# round-trip instead of N serial fetches inside backtest_symbol.
_CLOSE_CACHE: dict[tuple[str, str], tuple[pd.Series, float]] = {}
_CLOSE_TTL = 3600

_STRATEGIES = ("buy_hold", "rsi_swing", "sma_cross", "crowd_fade", "crowd_buy")
_DEFAULT_TX_BPS = 5.0  # per-leg basis points (5 bps = 0.05%)

# Drawdown-penalized scoring (adapted from AI-Trader challenge_scoring.py, MIT)
# First _DD_FREE_PCT of drawdown is "free"; excess penalised 1:1 vs return.
_DD_FREE_PCT = 10.0
_DD_PENALTY = 1.0


def _risk_adjusted(total_return_pct: float, max_drawdown_pct: float) -> float:
    """Return minus drawdown penalty on excess beyond the free threshold."""
    excess = max(0.0, abs(max_drawdown_pct) - _DD_FREE_PCT)
    return round(total_return_pct - excess * _DD_PENALTY, 2)


def _annualize_factor(periods_per_year: int = 252) -> float:
    return math.sqrt(periods_per_year)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min() * 100)


def _sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * _annualize_factor())


def _cagr(start: float, end: float, days: int) -> float:
    if start <= 0 or days <= 0:
        return 0.0
    years = days / 365.25
    try:
        return float(((end / start) ** (1 / years) - 1) * 100)
    except Exception:
        return 0.0


def _run_buy_hold(close: pd.Series, tx_cost_bps: float = 0.0) -> dict:
    """Buy at first bar, hold to last bar. Equity reflects entry + exit fee
    so Sharpe / CAGR / drawdown share the same fee-adjusted basis used by
    signal strategies — keeps cross-strategy comparison apples-to-apples.
    """
    if close.empty:
        return _empty_result()
    daily_ret = close.pct_change().dropna()
    fee_leg = tx_cost_bps / 10000.0
    # Entry fee scales the entire equity series; exit fee applied at final bar.
    equity = (1 + daily_ret).cumprod() * (1 - fee_leg)
    if not equity.empty:
        equity.iloc[-1] = float(equity.iloc[-1]) * (1 - fee_leg)
    eq_daily_ret = equity.pct_change().dropna()
    days = (close.index[-1] - close.index[0]).days
    final = float(equity.iloc[-1]) if not equity.empty else 1.0
    total_ret = round((final - 1) * 100, 2)
    max_dd = round(_max_drawdown(equity), 2)
    return {
        "total_return_pct": total_ret,
        "cagr_pct": round(_cagr(1.0, final, days), 2),
        "sharpe": round(_sharpe(eq_daily_ret), 2),
        "max_drawdown_pct": max_dd,
        "risk_adjusted_score": _risk_adjusted(total_ret, max_dd),
        "num_trades": 1,
        "days": days,
        "tx_cost_bps_per_leg": tx_cost_bps,
        "_daily_ret": eq_daily_ret.values,
    }


def _run_signal(
    close: pd.Series,
    signal: pd.Series,
    lookback_days: int,
    tx_cost_bps: float = 0.0,
) -> dict:
    """Long-only walk: enter on signal=1, exit on signal=0.
    Each entry and each exit deducts tx_cost_bps from equity (one leg).
    """
    if close.empty or signal.empty:
        return _empty_result()
    aligned = pd.concat([close, signal], axis=1).dropna()
    aligned.columns = ["close", "sig"]
    if len(aligned) < 2:
        return _empty_result()

    fee_leg = tx_cost_bps / 10000.0
    in_pos = False
    entry_price = 0.0
    trades: list[tuple[float, float]] = []
    equity = [1.0]

    for i in range(1, len(aligned)):
        price_prev = aligned["close"].iloc[i - 1]
        price_now = aligned["close"].iloc[i]
        sig_now = aligned["sig"].iloc[i]

        if in_pos:
            equity.append(equity[-1] * (price_now / price_prev))
        else:
            equity.append(equity[-1])

        # Execute on transition — deduct one leg of fee from equity
        if not in_pos and sig_now == 1:
            in_pos = True
            entry_price = float(price_now)
            equity[-1] *= (1 - fee_leg)
        elif in_pos and sig_now == 0:
            trades.append((entry_price, float(price_now)))
            in_pos = False
            equity[-1] *= (1 - fee_leg)

    if in_pos:
        # Close-out at last bar — synthetic exit, charge final leg
        trades.append((entry_price, float(aligned["close"].iloc[-1])))
        equity[-1] *= (1 - fee_leg)

    eq_series = pd.Series(equity, index=aligned.index)
    daily_ret = eq_series.pct_change().dropna()
    days = (aligned.index[-1] - aligned.index[0]).days
    final = equity[-1]
    total_ret = round((final - 1) * 100, 2)
    max_dd = round(_max_drawdown(eq_series), 2)
    return {
        "total_return_pct": total_ret,
        "cagr_pct": round(_cagr(1.0, final, days), 2),
        "sharpe": round(_sharpe(daily_ret), 2),
        "max_drawdown_pct": max_dd,
        "risk_adjusted_score": _risk_adjusted(total_ret, max_dd),
        "num_trades": len(trades),
        "days": days,
        "tx_cost_bps_per_leg": tx_cost_bps,
        "_daily_ret": daily_ret.values,
    }


def _empty_result() -> dict:
    return {
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "sharpe": 0.0,
        "max_drawdown_pct": 0.0,
        "risk_adjusted_score": 0.0,
        "num_trades": 0,
        "days": 0,
        "tx_cost_bps_per_leg": 0.0,
        "_daily_ret": np.array([], dtype=float),
    }


def _rsi_signal(close: pd.Series) -> pd.Series:
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    sig = pd.Series(np.nan, index=close.index)
    sig[rsi < 30] = 1
    sig[rsi > 70] = 0
    return sig.ffill().fillna(0)


def _sma_cross_signal(close: pd.Series) -> pd.Series:
    sma = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    return (close > sma).astype(int)


def _fetch_close(symbol: str, period: str) -> pd.Series:
    """Close series for one symbol. Hits _CLOSE_CACHE first to share data
    across strategies and across backtest_portfolio batch prefetch.
    """
    key = (symbol.upper(), period)
    entry = _CLOSE_CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CLOSE_TTL:
        return entry[0]
    try:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        _LOG.warning(f"[backtest] fetch {symbol} failed: {e}")
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df.columns:
        return pd.Series(dtype=float)
    series = df["Close"].dropna()
    _CLOSE_CACHE[key] = (series, time.time())
    return series


def _prefetch_closes(symbols: list[str], period: str) -> None:
    """Batch-fetch close series for all uncached symbols in one yf.download.
    Populates _CLOSE_CACHE so subsequent _fetch_close calls hit the cache.
    """
    now = time.time()
    to_fetch = [
        s for s in symbols
        if (entry := _CLOSE_CACHE.get((s.upper(), period))) is None
        or (now - entry[1]) >= _CLOSE_TTL
    ]
    if len(to_fetch) <= 1:
        return  # single-symbol falls through to _fetch_close anyway
    try:
        data = yf.download(
            to_fetch,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        _LOG.warning(f"[backtest] batch prefetch failed: {e}")
        return
    if data is None or data.empty:
        return
    for sym in to_fetch:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if sym in data.columns.get_level_values(0):
                    sub = data[sym]
                else:
                    continue
            else:
                sub = data
            if "Close" not in sub.columns:
                continue
            series = sub["Close"].dropna()
            if not series.empty:
                _CLOSE_CACHE[(sym.upper(), period)] = (series, time.time())
        except Exception:
            continue


def _crowding_filter(symbol: str) -> str:
    """Return current crowding label for the symbol ('consensus', 'neutral',
    'contrarian', or 'unknown'). Reuses positioning cache (6h TTL).
    """
    try:
        data = positioning_svc.get_positioning([symbol])
        rec = data.get(symbol) or {}
        return str(rec.get("crowding_label") or "unknown")
    except Exception:
        return "unknown"


def _run_strategy_on_window(
    strategy: str,
    close: pd.Series,
    lookback_days: int,
    tx_cost_bps: float,
    crowding_label: str | None,
) -> dict:
    """Helper: run any strategy on a pre-trimmed close series."""
    if strategy == "buy_hold":
        return _run_buy_hold(close, tx_cost_bps)
    if strategy == "rsi_swing":
        return _run_signal(
            close, _rsi_signal(close), lookback_days, tx_cost_bps
        )
    if strategy == "sma_cross":
        return _run_signal(
            close, _sma_cross_signal(close), lookback_days, tx_cost_bps
        )
    if strategy == "crowd_fade":
        if crowding_label == "consensus":
            r = _empty_result()
            r["skipped_due_to_crowding"] = True
            return r
        return _run_signal(
            close, _sma_cross_signal(close), lookback_days, tx_cost_bps
        )
    if strategy == "crowd_buy":
        if crowding_label != "contrarian":
            r = _empty_result()
            r["skipped_due_to_crowding"] = True
            return r
        return _run_signal(
            close, _sma_cross_signal(close), lookback_days, tx_cost_bps
        )
    return _empty_result()


def backtest_symbol(
    symbol: str,
    strategy: str = "buy_hold",
    lookback_days: int = 365,
    tx_cost_bps: float = _DEFAULT_TX_BPS,
    walk_forward: bool = False,
    train_frac: float = 0.7,
) -> dict[str, Any]:
    """Backtest one symbol on one strategy. Cached 1h.

    walk_forward=True splits the window into in-sample (first train_frac) and
    out-of-sample (remainder) and reports both. RSI / SMA params are fixed so
    no parameter overfit risk — purpose is to surface return decay between
    regimes (e.g. uptrend → chop).
    """
    if strategy not in _STRATEGIES:
        return {"error": f"unknown strategy: {strategy}"}
    train_frac = max(0.3, min(float(train_frac), 0.9))
    cache_key = (
        symbol.upper(), strategy, int(lookback_days),
        float(tx_cost_bps), bool(walk_forward), round(train_frac, 2),
    )
    entry = _CACHE.get(cache_key)
    now = time.time()
    if entry and (now - entry[1]) < _CACHE_TTL:
        return entry[0]

    period = "2y" if lookback_days > 365 else "1y"
    close = _fetch_close(symbol, period)
    if close.empty or len(close) < 30:
        result = {"symbol": symbol, "strategy": strategy, **_empty_result(),
                  "error": "insufficient_data"}
        _CACHE[cache_key] = (result, now)
        return result

    # Trim to lookback window
    cutoff = close.index[-1] - pd.Timedelta(days=lookback_days)
    close = close[close.index >= cutoff]

    crowding_label: str | None = None
    if strategy in ("crowd_fade", "crowd_buy"):
        crowding_label = _crowding_filter(symbol)

    if walk_forward and len(close) >= 60:
        split_idx = deterministic_split_idx(symbol, len(close), train_frac)
        in_sample = close.iloc[:split_idx]
        out_sample = close.iloc[split_idx:]
        is_metrics = _run_strategy_on_window(
            strategy, in_sample, lookback_days, tx_cost_bps, crowding_label,
        )
        oos_metrics = _run_strategy_on_window(
            strategy, out_sample, lookback_days, tx_cost_bps, crowding_label,
        )
        full_metrics = _run_strategy_on_window(
            strategy, close, lookback_days, tx_cost_bps, crowding_label,
        )
        is_ret = is_metrics.pop("_daily_ret", np.array([]))
        oos_ret = oos_metrics.pop("_daily_ret", np.array([]))
        full_ret = full_metrics.pop("_daily_ret", np.array([]))
        is_metrics["validation"] = run_validation(is_ret)
        oos_metrics["validation"] = run_validation(oos_ret)
        result = {
            "symbol": symbol,
            "strategy": strategy,
            "walk_forward": True,
            "train_frac": train_frac,
            "in_sample": is_metrics,
            "out_of_sample": oos_metrics,
            # Top-level fields mirror full-window — keeps shape compatible
            **full_metrics,
            "validation": run_validation(full_ret),
            # Decay = how much OOS underperformed IS (negative = worse OOS)
            "oos_minus_is_pct": round(
                oos_metrics.get("total_return_pct", 0.0)
                - is_metrics.get("total_return_pct", 0.0), 2,
            ),
        }
    else:
        metrics = _run_strategy_on_window(
            strategy, close, lookback_days, tx_cost_bps, crowding_label,
        )
        daily_ret = metrics.pop("_daily_ret", np.array([]))
        result = {
            "symbol": symbol,
            "strategy": strategy,
            **metrics,
            "validation": run_validation(daily_ret),
        }

    if crowding_label is not None:
        result["crowding_label_at_run"] = crowding_label
    _CACHE[cache_key] = (result, now)
    return result


def backtest_portfolio(
    symbols: list[str],
    weights: dict[str, float] | None = None,
    lookback_days: int = 365,
    strategies: list[str] | None = None,
    tx_cost_bps: float = _DEFAULT_TX_BPS,
    walk_forward: bool = False,
    train_frac: float = 0.7,
) -> dict[str, Any]:
    """Backtest each symbol across listed strategies.
    Returns per-symbol results + weighted-portfolio totals + delta-vs-buy-hold.

    tx_cost_bps deducted per trade leg (entry and exit each). Default 5 bps.
    walk_forward=True splits each window into IS/OOS; per-symbol results then
    include in_sample / out_of_sample / oos_minus_is_pct fields.
    """
    if not symbols:
        return {"error": "no symbols"}
    strategies = strategies or list(_STRATEGIES)
    bad = [s for s in strategies if s not in _STRATEGIES]
    if bad:
        return {"error": f"unknown strategies: {bad}"}

    # Prefetch all uncached close series in one batch HTTP call before strategy
    # loop — avoids N serial yfinance fetches across the symbol×strategy matrix.
    period = "2y" if lookback_days > 365 else "1y"
    _prefetch_closes(symbols, period)

    per_symbol: dict[str, dict[str, dict]] = {}
    for sym in symbols:
        per_symbol[sym] = {}
        for strat in strategies:
            per_symbol[sym][strat] = backtest_symbol(
                sym, strat, lookback_days, tx_cost_bps,
                walk_forward, train_frac,
            )

    # Weighted portfolio totals per strategy
    w = weights or {s: 1.0 / len(symbols) for s in symbols}
    w_sum = sum(w.get(s, 0.0) for s in symbols) or 1.0
    norm_w = {s: w.get(s, 0.0) / w_sum for s in symbols}

    portfolio_totals: dict[str, dict] = {}
    for strat in strategies:
        agg_return = 0.0
        agg_cagr = 0.0
        agg_ras = 0.0
        worst_dd = 0.0
        n_valid = 0
        for sym in symbols:
            r = per_symbol[sym][strat]
            if r.get("error"):
                continue
            agg_return += r.get("total_return_pct", 0.0) * norm_w[sym]
            agg_cagr += r.get("cagr_pct", 0.0) * norm_w[sym]
            agg_ras += r.get("risk_adjusted_score", 0.0) * norm_w[sym]
            worst_dd = min(worst_dd, r.get("max_drawdown_pct", 0.0))
            n_valid += 1
        portfolio_totals[strat] = {
            "weighted_total_return_pct": round(agg_return, 2),
            "weighted_cagr_pct": round(agg_cagr, 2),
            "weighted_risk_adjusted_score": round(agg_ras, 2),
            "worst_position_drawdown_pct": round(worst_dd, 2),
            "symbols_evaluated": n_valid,
        }

    # Delta vs buy_hold per strategy
    deltas: dict[str, float] = {}
    if "buy_hold" in portfolio_totals:
        base = portfolio_totals["buy_hold"]["weighted_total_return_pct"]
        for strat, totals in portfolio_totals.items():
            if strat == "buy_hold":
                continue
            deltas[strat] = round(
                totals["weighted_total_return_pct"] - base, 2
            )

    config = {
        "lookback_days": lookback_days,
        "strategies": strategies,
        "tx_cost_bps": tx_cost_bps,
        "walk_forward": walk_forward,
        "train_frac": train_frac if walk_forward else None,
    }
    card = generate_run_card(
        strategy=",".join(strategies),
        config=config,
        metrics=portfolio_totals,
        symbols=symbols,
    )
    try:
        save_run_card(card)
    except Exception:
        pass

    return {
        "lookback_days": lookback_days,
        "strategies": strategies,
        "tx_cost_bps_per_leg": tx_cost_bps,
        "walk_forward": walk_forward,
        "train_frac": train_frac if walk_forward else None,
        "per_symbol": per_symbol,
        "portfolio_totals": portfolio_totals,
        "delta_vs_buy_hold_pct": deltas,
        "run_card": {
            "run_id": card["run_id"],
            "timestamp_utc": card["timestamp_utc"],
            "config_hash": card["config_hash"],
        },
    }
