"""Backtest confidence intervals + lookahead-bias sentinel.

Turns a point-estimate backtest into a distribution of outcomes:
- moving-block bootstrap of daily returns -> CI bands on total return, CAGR,
  and max drawdown (preserves short-run autocorrelation).
- order-shuffle Monte-Carlo -> max-drawdown distribution + P(drawdown worse
  than a threshold).

And a lookahead sentinel: runs a perfect-foresight signal through the same
engine; if the engine lags signals correctly it must NOT earn abnormal
returns. If it does, lookahead has leaked into the backtest.

Reuses the existing backtest engine (no duplicated trade logic).
"""

from __future__ import annotations

import numpy as np

from app.services import backtest as bt

_PERIODS_PER_YEAR = 252


def _equity_from_returns(returns: np.ndarray) -> np.ndarray:
    return np.cumprod(1.0 + returns)


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(((equity - peak) / peak).min() * 100)


def _daily_returns(symbol: str, strategy: str, period: str, tx_cost_bps: float) -> np.ndarray:
    close = bt._fetch_close(symbol, period)
    if close is None or close.empty:
        return np.array([], dtype=float)
    if strategy == "rsi_swing":
        sig = bt._rsi_signal(close)
    else:
        sig = bt._sma_cross_signal(close)
    res = bt._run_signal(close, sig, len(close), tx_cost_bps)
    return np.asarray(res.get("_daily_ret", []), dtype=float)


def _pctl(arr: list[float]) -> dict:
    a = np.asarray(arr, dtype=float)
    return {
        "p5": round(float(np.percentile(a, 5)), 2),
        "p25": round(float(np.percentile(a, 25)), 2),
        "median": round(float(np.percentile(a, 50)), 2),
        "p75": round(float(np.percentile(a, 75)), 2),
        "p95": round(float(np.percentile(a, 95)), 2),
    }


def confidence_intervals(
    symbol: str,
    strategy: str = "sma_cross",
    period: str = "2y",
    tx_cost_bps: float = 5.0,
    n_boot: int = 1000,
    drawdown_threshold_pct: float = -25.0,
) -> dict:
    """Bootstrap + Monte-Carlo CI bands for one symbol/strategy backtest."""
    rets = _daily_returns(symbol, strategy, period, tx_cost_bps)
    n = rets.size
    if n < 30:
        return {"error": "insufficient_data", "symbol": symbol, "observations": int(n)}

    rng = np.random.default_rng(42)
    block = max(2, int(round(np.sqrt(n))))
    n_blocks = int(np.ceil(n / block))

    boot_total, boot_cagr, boot_maxdd, shuffle_maxdd = [], [], [], []
    for _ in range(n_boot):
        # Moving-block bootstrap (preserves autocorrelation).
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        sampled = np.concatenate([rets[s : s + block] for s in starts])[:n]
        eq = _equity_from_returns(sampled)
        total = float(eq[-1] - 1) * 100
        boot_total.append(total)
        boot_cagr.append(((eq[-1]) ** (_PERIODS_PER_YEAR / n) - 1) * 100)
        boot_maxdd.append(_max_drawdown(eq))
        # Order-shuffle MC (same returns, random sequence) for drawdown risk.
        shuffled = rng.permutation(rets)
        shuffle_maxdd.append(_max_drawdown(_equity_from_returns(shuffled)))

    point_total = round(float(_equity_from_returns(rets)[-1] - 1) * 100, 2)
    prob_breach = round(
        float(np.mean(np.asarray(shuffle_maxdd) <= drawdown_threshold_pct)) * 100, 1
    )

    return {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "period": period,
        "observations": int(n),
        "n_bootstrap": n_boot,
        "block_size": block,
        "point_estimate": {"total_return_pct": point_total},
        "total_return_pct_ci": _pctl(boot_total),
        "cagr_pct_ci": _pctl(boot_cagr),
        "max_drawdown_pct_ci": _pctl(boot_maxdd),
        "drawdown_risk": {
            "threshold_pct": drawdown_threshold_pct,
            "prob_worse_than_threshold_pct": prob_breach,
            "median_shuffled_max_drawdown_pct": round(float(np.median(shuffle_maxdd)), 2),
        },
        "interpretation": (
            f"Median CAGR {round(float(np.percentile(boot_cagr,50)),1)}% "
            f"[5-95th: {round(float(np.percentile(boot_cagr,5)),1)}% to "
            f"{round(float(np.percentile(boot_cagr,95)),1)}%]; "
            f"{prob_breach}% chance of a drawdown worse than {drawdown_threshold_pct}%."
        ),
    }


def lookahead_sentinel(
    symbol: str, period: str = "2y", tx_cost_bps: float = 5.0
) -> dict:
    """Inject a perfect-foresight signal; a correctly-lagged engine can't exploit it.

    peek_signal knows tomorrow's direction. The engine shifts signals one bar,
    so after the shift this becomes "act on yesterday's foresight" — it must NOT
    earn abnormal returns. If peek CAGR dwarfs buy-and-hold, lookahead leaked.
    """
    close = bt._fetch_close(symbol, period)
    if close is None or close.empty or len(close) < 30:
        return {"error": "insufficient_data", "symbol": symbol}

    peek = (close.shift(-1) > close).astype(int)
    res_peek = bt._run_signal(close, peek, len(close), tx_cost_bps)
    res_bh = bt._run_buy_hold(close, tx_cost_bps)

    peek_cagr = res_peek.get("cagr_pct", 0.0)
    bh_cagr = res_bh.get("cagr_pct", 0.0)
    ratio = round(peek_cagr / bh_cagr, 2) if bh_cagr > 0 else None
    # Foresight signal earning >3x buy-hold AND >80% CAGR ⇒ lookahead leaked.
    leaked = bool(peek_cagr > 80 and (ratio is None or ratio > 3))

    return {
        "symbol": symbol.upper(),
        "period": period,
        "passed": not leaked,
        "verdict": (
            "LOOKAHEAD LEAK — foresight signal earned abnormal returns; the "
            "backtest engine is peeking at future bars."
            if leaked
            else "PASS — perfect-foresight signal earned no abnormal return; "
            "signals are correctly lagged, no lookahead detected."
        ),
        "peek_signal_cagr_pct": peek_cagr,
        "buy_hold_cagr_pct": bh_cagr,
        "peek_to_buyhold_ratio": ratio,
    }
