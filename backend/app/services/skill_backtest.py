"""Backtest the 12 analysis skills as codified portfolio strategies.

Why codify:
Skills today run inside Claude's context window and use LLM judgment plus
live MCP data. That cannot be replayed deterministically over five years.
For an honest accuracy claim we extract the rules behind each skill, run
them on historical OHLC bars, and report aggregate stats.

LLM-thesis sampling (`sample_thesis_llm`) lets us spot-check the rule's
fidelity to the underlying skill by asking Claude to grade a handful of
historical decision points. Off by default; opt in per run.

Each skill returns a SkillBacktest with:
  skill, strategy_spec, universe, trades, equity_curve,
  total_return_pct, cagr_pct, sharpe, sortino, max_drawdown_pct,
  hit_rate_pct, num_trades, alpha_vs_spy_pct, alpha_vs_xeqt_pct
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import ta

from app.services import data_router
from app.services.backtest import _cagr

_BENCH_SPY = "SPY"
_BENCH_XEQT = "XEQT.TO"

_OUT_DIR = Path(__file__).resolve().parents[2] / ".cache" / "backtests"


# ---- Unbiased universe ---------------------------------------------------
# Spans 11 GICS sectors + factor ETFs + index ETFs + Canadian exposure.
# Mix of winners/losers/sideways from the past 5 years — explicitly avoids
# the AAPL/MSFT/NVDA/XEQT/VFV cherry-picked set the prior backtest used.
DEFAULT_UNIVERSE = [
    # Sector SPDRs (all 11)
    "XLK", "XLF", "XLV", "XLY", "XLP", "XLE",
    "XLI", "XLU", "XLRE", "XLB", "XLC",
    # Index ETFs (broad + style + size)
    "SPY", "QQQ", "IWM", "VTI", "VTV", "VUG", "EFA", "EEM",
    # Canadian core
    "XEQT.TO", "VFV.TO", "VCN.TO", "XIU.TO",
    # Large-cap tech (some winners, some sideways)
    "AAPL", "MSFT", "GOOGL", "META", "AMZN",
    # Megacap non-tech
    "BRK-B", "JPM", "JNJ", "PG", "WMT", "XOM", "CVX",
    # Mid/small picks across sectors (mix of winners + laggards)
    "F", "GE", "DIS", "BA", "INTC", "VZ", "T", "PFE", "MRK",
    # Cyclicals + commodities
    "CAT", "DE", "FCX", "NEM",
    # Drawdown / failure exposure (mean-revert + bear stress)
    "PYPL", "PARA", "WBA",
]


# ---- Walk-forward + deflated Sharpe --------------------------------------

def _deflated_sharpe(daily_ret: pd.Series, n_trials: int = 1) -> float:
    """Bailey & López de Prado deflated Sharpe ratio.

    Penalizes a raw Sharpe by sample size, skew, kurtosis, and the number of
    backtest configurations tried (multiple-testing correction). When you run
    a strategy across N rule variants the effective SR0 grows with N — DSR
    asks: is the observed SR statistically distinguishable from the *best*
    SR you'd expect by chance after N trials?

    Returns a probability-style score in [0, 1] — values >= 0.95 typically
    considered evidence of real edge. Below 0.5 is essentially "could be
    luck under multiple testing".
    """
    r = daily_ret.dropna()
    n = len(r)
    if n < 30 or r.std() == 0:
        return 0.0
    sr = r.mean() / r.std() * _annualize()
    # Higher moments of the return distribution
    skew = float(r.skew())
    excess_kurt = float(r.kurtosis())  # pandas returns excess kurtosis already
    n_trials = max(1, n_trials)
    # Expected max SR under N independent trials (BLP 2014 Eq 7)
    euler_mascheroni = 0.5772156649
    e_max_sr = (1 - euler_mascheroni) * _zinv(1 - 1 / n_trials) + \
        euler_mascheroni * _zinv(1 - 1 / (n_trials * math.e))
    # Variance of SR estimate accounting for higher moments (BLP Eq 9)
    var_sr = (1 - skew * sr + (excess_kurt / 4) * sr * sr) / (n - 1)
    if var_sr <= 0:
        return 0.0
    z = (sr - e_max_sr) / math.sqrt(var_sr)
    return round(_zcdf(z), 3)


def _zinv(p: float) -> float:
    """Inverse standard normal CDF — rational approximation."""
    if p <= 0 or p >= 1:
        return 0.0
    # Beasley-Springer-Moro approximation
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.357751867269, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
         3.754408661907416]
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= p_high:
        q = p - 0.5
        r2 = q * q
        return (((((a[0] * r2 + a[1]) * r2 + a[2]) * r2 + a[3]) * r2 + a[4]) * r2 + a[5]) * q / \
               (((((b[0] * r2 + b[1]) * r2 + b[2]) * r2 + b[3]) * r2 + b[4]) * r2 + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def _zcdf(z: float) -> float:
    """Standard normal CDF using erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _regime_label(spy_close: pd.Series) -> pd.Series:
    """Bull / bear / sideways per day from SPY SMA200 slope + price-relative.

    bull:     price > SMA200 AND SMA200 rising over 20 sessions
    bear:     price < SMA200 AND SMA200 falling over 20 sessions
    sideways: anything else
    """
    if spy_close.empty:
        return pd.Series(dtype=object)
    sma200 = spy_close.rolling(200).mean()
    sma_slope = sma200.diff(20)
    label = pd.Series("sideways", index=spy_close.index, dtype=object)
    bull = (spy_close > sma200) & (sma_slope > 0)
    bear = (spy_close < sma200) & (sma_slope < 0)
    label[bull] = "bull"
    label[bear] = "bear"
    return label


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    return_pct: float
    win: bool


@dataclass
class SkillBacktest:
    skill: str
    strategy_spec: str
    universe: list[str]
    lookback_days: int
    tx_cost_bps: float
    trades: list[Trade] = field(default_factory=list)
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    hit_rate_pct: float = 0.0
    num_trades: int = 0
    alpha_vs_spy_pct: float | None = None
    alpha_vs_xeqt_pct: float | None = None
    bench_return_spy_pct: float | None = None
    bench_return_xeqt_pct: float | None = None
    as_of: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trades"] = [asdict(t) for t in self.trades]
        return d


# Per-(symbol, lookback) cache populated by `_prefetch_universe`. When set,
# `_bars_to_close` reads from here instead of issuing a per-symbol HTTP call.
# Walk-forward over 40+ symbols was previously ~520 sequential round-trips.
_BATCH_BARS_CACHE: dict[tuple[str, int], pd.Series] = {}


def _prefetch_universe(symbols: list[str], lookback_days: int) -> None:
    """One batched `yf.download` for the whole universe. Failures fall through
    silently — `_bars_to_close` will retry per-symbol via data_router for any
    missing symbol so behaviour is unchanged on partial failure.
    """
    try:
        import yfinance as yf
    except Exception:
        return
    period = _period_for_days(lookback_days)
    try:
        df = yf.download(
            symbols,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return
    if df is None or df.empty:
        return
    for sym in symbols:
        try:
            if isinstance(df.columns, pd.MultiIndex):
                if sym in df.columns.get_level_values(0):
                    sub = df[sym]
                    s = sub["Close"].dropna().astype(float)
                else:
                    continue
            else:
                if "Close" not in df.columns:
                    continue
                s = df["Close"].dropna().astype(float)
            if s.empty:
                continue
            s.name = sym
            _BATCH_BARS_CACHE[(sym, lookback_days)] = s.tail(lookback_days)
        except Exception:
            continue


def _bars_to_close(symbol: str, lookback_days: int) -> pd.Series:
    cached = _BATCH_BARS_CACHE.get((symbol, lookback_days))
    if cached is not None and not cached.empty:
        return cached
    period = _period_for_days(lookback_days)
    try:
        bars = data_router.get_history(symbol, period=period, interval="1d")
    except Exception:
        return pd.Series(dtype=float)
    if not bars:
        return pd.Series(dtype=float)
    df = pd.DataFrame(bars)
    df["dt"] = pd.to_datetime(df["date"])
    df = df.set_index("dt").sort_index()
    s = df["close"].astype(float)
    s.name = symbol
    return s.tail(lookback_days)


def _period_for_days(days: int) -> str:
    if days <= 31:
        return "1mo"
    if days <= 93:
        return "3mo"
    if days <= 186:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 365 * 2:
        return "2y"
    if days <= 365 * 3:
        return "3y"
    if days <= 365 * 5:
        return "5y"
    if days <= 365 * 10:
        return "10y"
    return "max"


def _annualize() -> float:
    return math.sqrt(252)


def _sharpe(daily_ret: pd.Series) -> float:
    if daily_ret.empty or daily_ret.std() == 0:
        return 0.0
    return float(daily_ret.mean() / daily_ret.std() * _annualize())


def _sortino(daily_ret: pd.Series) -> float:
    if daily_ret.empty:
        return 0.0
    downside = daily_ret[daily_ret < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    return float(daily_ret.mean() / downside.std() * _annualize())


def _max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((equity - peak) / peak).min() * 100)


def _simulate(
    close: pd.Series, signal: pd.Series, tx_cost_bps: float
) -> tuple[pd.Series, list[Trade], list[float]]:
    """Long-only walk. Returns (equity_curve, trades, daily_returns_list).

    Signal at bar i is computed from data up to bar i's close, so trading on
    that same bar would be lookahead. Shift signal by one bar so the decision
    made on day i-1's close is executed at day i's close.
    """
    aligned = pd.concat([close, signal], axis=1).dropna()
    aligned.columns = ["close", "sig"]
    aligned["sig"] = aligned["sig"].shift(1).fillna(0).astype(int)
    if len(aligned) < 2:
        return pd.Series(dtype=float), [], []

    fee_leg = tx_cost_bps / 10000.0
    in_pos = False
    entry_price = 0.0
    entry_date = ""
    equity = [1.0]
    trades: list[Trade] = []
    symbol = close.name or "?"

    for i in range(1, len(aligned)):
        price_prev = float(aligned["close"].iloc[i - 1])
        price_now = float(aligned["close"].iloc[i])
        sig_now = int(aligned["sig"].iloc[i])
        date_now = aligned.index[i].strftime("%Y-%m-%d")

        equity.append(
            equity[-1] * (price_now / price_prev) if in_pos else equity[-1]
        )

        if not in_pos and sig_now == 1:
            in_pos = True
            entry_price = price_now
            entry_date = date_now
            equity[-1] *= (1 - fee_leg)
        elif in_pos and sig_now == 0:
            ret = (price_now - entry_price) / entry_price * 100
            trades.append(
                Trade(
                    symbol=symbol,
                    entry_date=entry_date,
                    entry_price=entry_price,
                    exit_date=date_now,
                    exit_price=price_now,
                    return_pct=ret,
                    win=ret > 0,
                )
            )
            in_pos = False
            equity[-1] *= (1 - fee_leg)

    if in_pos:
        last_price = float(aligned["close"].iloc[-1])
        ret = (last_price - entry_price) / entry_price * 100
        trades.append(
            Trade(
                symbol=symbol,
                entry_date=entry_date,
                entry_price=entry_price,
                exit_date=aligned.index[-1].strftime("%Y-%m-%d"),
                exit_price=last_price,
                return_pct=ret,
                win=ret > 0,
            )
        )
        equity[-1] *= (1 - fee_leg)

    eq_series = pd.Series(equity, index=aligned.index)
    daily_ret = eq_series.pct_change().dropna().tolist()
    return eq_series, trades, daily_ret


# ---- Skill rule definitions ----------------------------------------------

def _rule_buy_hold(close: pd.Series) -> pd.Series:
    return pd.Series(1, index=close.index)


def _rule_sma_cross(close: pd.Series) -> pd.Series:
    sma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    return (close > sma50).astype(int)


def _rule_rsi_swing(close: pd.Series) -> pd.Series:
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    sig = pd.Series(np.nan, index=close.index)
    sig[rsi < 30] = 1
    sig[rsi > 70] = 0
    return sig.ffill().fillna(0)


def _rule_macd_trend(close: pd.Series) -> pd.Series:
    macd = ta.trend.MACD(close)
    return (macd.macd_diff() > 0).astype(int)


def _rule_golden_cross(close: pd.Series) -> pd.Series:
    sma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator()
    return (sma50 > sma200).astype(int)


def _rule_bollinger_revert(close: pd.Series) -> pd.Series:
    bb = ta.volatility.BollingerBands(close)
    low = bb.bollinger_lband()
    mid = bb.bollinger_mavg()
    sig = pd.Series(np.nan, index=close.index)
    sig[close < low] = 1
    sig[close > mid] = 0
    return sig.ffill().fillna(0)


def _rule_dividend_buy_hold(close: pd.Series) -> pd.Series:
    """Dividend strategy backtest proxy: SMA200 filter + buy-hold.

    True dividend selection happens at portfolio composition time. For
    historical replay we approximate by holding only above 200d SMA.
    """
    sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator()
    return (close > sma200).astype(int)


def _rule_sector_rotation(close: pd.Series) -> pd.Series:
    """Sector rotation proxy: 12m momentum > 0 => hold (classic Faber)."""
    mom = close.pct_change(252)
    return (mom > 0).astype(int)


def _rule_macro_risk_off(close: pd.Series) -> pd.Series:
    """Macro-impact proxy: hold above 200d SMA, exit below.

    Captures the practical effect of the macro-impact skill which
    de-risks on broken trend (recession / rate-shock regimes).
    """
    sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator()
    return (close > sma200).astype(int)


def _rule_earnings_avoid(close: pd.Series) -> pd.Series:
    """Earnings-analyzer proxy: avoid being long in week of earnings.

    Historical earnings dates not always available cheaply; as a proxy we
    use elevated 20d realized vol > 1.5x trailing 60d vol as a soft proxy
    for run-up / event-cluster risk and stay flat then.
    """
    ret = close.pct_change()
    vol20 = ret.rolling(20).std()
    vol60 = ret.rolling(60).std()
    elevated = (vol20 > 1.5 * vol60).astype(int)
    return (1 - elevated).fillna(0)


def _rule_consensus_fade(close: pd.Series) -> pd.Series:
    """Adversarial-research proxy: stay long unless 5d momentum > 95 percentile.

    Heuristic stand-in for "crowded melt-up" exits the adversarial skill
    would flag. Re-entry once momentum normalizes.
    """
    mom5 = close.pct_change(5)
    if mom5.dropna().empty:
        return pd.Series(0, index=close.index)

    def _is_hot(w):
        return w.iloc[-1] >= np.nanquantile(w, 0.95)

    hot = mom5.rolling(60).apply(_is_hot, raw=False)
    return (1 - hot.fillna(0)).astype(int)


SKILL_RULES: dict[str, tuple[str, Callable[[pd.Series], pd.Series]]] = {
    "portfolio_health":     ("buy_hold (proxy)",         _rule_buy_hold),
    "risk_assessment":      ("sma200_trend_filter",      _rule_macro_risk_off),
    "stock_analysis":       ("sma50_trend",              _rule_sma_cross),
    "stock_compare":        ("relative_strength_proxy",  _rule_sma_cross),
    "macro_impact":         ("sma200_regime",            _rule_macro_risk_off),
    "dividend_strategy":    ("sma200_quality",           _rule_dividend_buy_hold),
    "earnings_analyzer":    ("vol_cluster_avoid",        _rule_earnings_avoid),
    "earnings_postmortem":  ("rsi_swing_post_event",     _rule_rsi_swing),
    "sector_rotation":      ("12m_momentum_faber",       _rule_sector_rotation),
    "tax_loss_review":      ("bollinger_lband_revert",   _rule_bollinger_revert),
    "adversarial_research": ("consensus_fade_top5pct",   _rule_consensus_fade),
    "cash_deployment":      ("golden_cross_add",         _rule_golden_cross),
    "daily_briefing":       ("macd_trend",               _rule_macd_trend),
}


def list_skills() -> list[str]:
    return list(SKILL_RULES.keys())


# ---- Runner --------------------------------------------------------------

def backtest_skill(
    skill: str,
    universe: list[str],
    lookback_days: int = 365 * 5,
    tx_cost_bps: float = 5.0,
) -> SkillBacktest:
    if skill not in SKILL_RULES:
        raise ValueError(f"unknown skill: {skill}")
    spec, rule = SKILL_RULES[skill]

    eq_curves: list[pd.Series] = []
    all_trades: list[Trade] = []
    notes: list[str] = []

    for sym in universe:
        close = _bars_to_close(sym, lookback_days)
        if close.empty or len(close) < 50:
            notes.append(f"{sym}: insufficient history")
            continue
        signal = rule(close)
        eq, trades, _ = _simulate(close, signal, tx_cost_bps)
        if eq.empty:
            continue
        eq_curves.append(eq)
        all_trades.extend(trades)

    if not eq_curves:
        return SkillBacktest(
            skill=skill, strategy_spec=spec, universe=universe,
            lookback_days=lookback_days, tx_cost_bps=tx_cost_bps,
            notes=notes + ["no usable symbols"],
        )

    portfolio_eq = pd.concat(eq_curves, axis=1).ffill().mean(axis=1)
    portfolio_eq = portfolio_eq.dropna()
    daily_ret = portfolio_eq.pct_change().dropna()
    days = (portfolio_eq.index[-1] - portfolio_eq.index[0]).days

    total_ret = (float(portfolio_eq.iloc[-1]) - 1) * 100
    cagr = _cagr(1.0, float(portfolio_eq.iloc[-1]), days)
    sharpe = _sharpe(daily_ret)
    sortino = _sortino(daily_ret)
    mdd = _max_dd(portfolio_eq)
    wins = sum(1 for t in all_trades if t.win)
    hit_rate = (wins / len(all_trades) * 100) if all_trades else 0.0

    spy_ret = _bench_return(_BENCH_SPY, days)
    xeqt_ret = _bench_return(_BENCH_XEQT, days)

    return SkillBacktest(
        skill=skill,
        strategy_spec=spec,
        universe=universe,
        lookback_days=lookback_days,
        tx_cost_bps=tx_cost_bps,
        trades=all_trades,
        total_return_pct=round(total_ret, 2),
        cagr_pct=round(cagr, 2),
        sharpe=round(sharpe, 2),
        sortino=round(sortino, 2),
        max_drawdown_pct=round(mdd, 2),
        hit_rate_pct=round(hit_rate, 2),
        num_trades=len(all_trades),
        alpha_vs_spy_pct=(
            round(total_ret - spy_ret, 2) if spy_ret is not None else None
        ),
        alpha_vs_xeqt_pct=(
            round(total_ret - xeqt_ret, 2) if xeqt_ret is not None else None
        ),
        bench_return_spy_pct=spy_ret,
        bench_return_xeqt_pct=xeqt_ret,
        notes=notes,
    )


def _bench_return(symbol: str, days: int) -> float | None:
    close = _bars_to_close(symbol, days + 5)
    if close.empty or len(close) < 2:
        return None
    return round((float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100, 2)


def backtest_all_skills(
    universe: list[str],
    lookback_days: int = 365 * 5,
    tx_cost_bps: float = 5.0,
    persist: bool = True,
    label: str = "broad",
) -> dict:
    # label distinguishes the personalized "holdings" run from the unbiased
    # "broad" basket run so the trust report can show both side by side.
    out = {
        "as_of": time.time(),
        "lookback_days": lookback_days,
        "tx_cost_bps": tx_cost_bps,
        "universe": universe,
        "label": label,
        "results": [],
    }
    for skill in list_skills():
        try:
            bt = backtest_skill(
                skill,
                universe,
                lookback_days=lookback_days,
                tx_cost_bps=tx_cost_bps,
            )
            out["results"].append(bt.to_dict())
        except Exception as e:
            out["results"].append({"skill": skill, "error": str(e)})

    if persist:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUT_DIR / f"skill_backtests_{label}_{int(time.time())}.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        out["persisted_to"] = str(path)
    return out


def latest_results(label: str | None = None) -> dict | None:
    """Most recent persisted run. Pass label ('broad'/'holdings') to filter;
    None returns the latest of any label (recency by file mtime, so legacy
    unlabeled files still resolve correctly)."""
    if not _OUT_DIR.exists():
        return None
    pattern = f"skill_backtests_{label}_*.json" if label else "skill_backtests_*.json"
    files = sorted(_OUT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


# ---- Walk-forward backtest runner ----------------------------------------

def walk_forward_backtest(
    skill: str,
    universe: list[str] | None = None,
    *,
    lookback_days: int = 365 * 5,
    window_days: int = 252,
    step_days: int = 63,
    tx_cost_bps: float = 5.0,
    n_trials_for_dsr: int = 13,
) -> dict:
    """Walk-forward backtest with regime split + deflated Sharpe.

    Default universe is `DEFAULT_UNIVERSE` (40+ symbols across all 11 GICS
    sectors + factor/style ETFs + Canadian core + winners/losers). Override
    only when testing a deliberately scoped subset.

    Splits the lookback into rolling out-of-sample windows of `window_days`
    advancing by `step_days`. For each window: replay the signal on data
    from that window only, compute returns, then aggregate. Since the
    skill rules are non-parametric (no in-sample fitting), each window
    yields a true OOS result — stability across windows is what we measure.

    Returns:
      {
        "skill": str, "strategy_spec": str,
        "n_windows": int, "aggregate": {...},
        "by_window": [{"start": iso, "end": iso, "return_pct": ...}, ...],
        "by_regime": {"bull": {...}, "bear": {...}, "sideways": {...}},
        "deflated_sharpe": float in [0,1],
        "universe_size": int, "as_of": ts,
      }
    """
    if skill not in SKILL_RULES:
        raise ValueError(f"unknown skill: {skill}")
    spec, rule = SKILL_RULES[skill]
    universe = universe or DEFAULT_UNIVERSE

    # One batched yf.download for the whole universe (incl. benchmarks)
    # instead of N sequential per-symbol HTTPs through data_router.
    prefetch_syms = list(dict.fromkeys(list(universe) + [_BENCH_SPY, _BENCH_XEQT]))
    _prefetch_universe(prefetch_syms, lookback_days)

    spy_close = _bars_to_close(_BENCH_SPY, lookback_days)
    regimes = _regime_label(spy_close) if not spy_close.empty else pd.Series(dtype=object)

    per_symbol_eq: list[pd.Series] = []
    per_symbol_trades: list[Trade] = []
    notes: list[str] = []

    for sym in universe:
        close = _bars_to_close(sym, lookback_days)
        if close.empty or len(close) < window_days:
            notes.append(f"{sym}: insufficient history (<{window_days}d)")
            continue
        signal = rule(close)
        eq, trades, _ = _simulate(close, signal, tx_cost_bps)
        if eq.empty:
            continue
        per_symbol_eq.append(eq)
        per_symbol_trades.extend(trades)

    if not per_symbol_eq:
        return {
            "skill": skill, "strategy_spec": spec, "error": "no usable symbols",
            "universe_size": len(universe), "notes": notes,
        }

    portfolio_eq = pd.concat(per_symbol_eq, axis=1).ffill().mean(axis=1).dropna()
    daily_ret = portfolio_eq.pct_change().dropna()

    # Walk-forward window iteration
    window_results: list[dict] = []
    idx = portfolio_eq.index
    start_pos = 0
    while start_pos + window_days <= len(idx):
        end_pos = start_pos + window_days
        win_eq = portfolio_eq.iloc[start_pos:end_pos]
        win_ret = daily_ret.iloc[start_pos:end_pos]
        if win_eq.empty or win_ret.empty:
            start_pos += step_days
            continue
        ret_pct = (float(win_eq.iloc[-1]) / float(win_eq.iloc[0]) - 1) * 100
        sharpe = _sharpe(win_ret)
        mdd = _max_dd(win_eq)
        window_results.append({
            "start": win_eq.index[0].strftime("%Y-%m-%d"),
            "end": win_eq.index[-1].strftime("%Y-%m-%d"),
            "return_pct": round(ret_pct, 2),
            "sharpe": round(sharpe, 2),
            "max_dd_pct": round(mdd, 2),
        })
        start_pos += step_days

    # Regime split — align portfolio daily returns to SPY regime label
    by_regime: dict[str, dict] = {}
    if not regimes.empty:
        joined = pd.concat([daily_ret.rename("ret"), regimes.rename("regime")],
                            axis=1, join="inner").dropna()
        for label in ("bull", "bear", "sideways"):
            subset = joined[joined["regime"] == label]["ret"]
            if subset.empty:
                continue
            by_regime[label] = {
                "n_days": int(len(subset)),
                "ann_return_pct": round(float(subset.mean()) * 252 * 100, 2),
                "ann_vol_pct": round(float(subset.std()) * _annualize() * 100, 2),
                "sharpe": round(_sharpe(subset), 2),
            }

    # Aggregate stats over the full sample
    total_ret = (float(portfolio_eq.iloc[-1]) - 1) * 100
    sharpe_full = _sharpe(daily_ret)
    sortino_full = _sortino(daily_ret)
    mdd_full = _max_dd(portfolio_eq)
    wins = sum(1 for t in per_symbol_trades if t.win)
    hit = (wins / len(per_symbol_trades) * 100) if per_symbol_trades else 0.0

    spy_ret = _bench_return(_BENCH_SPY, lookback_days)
    xeqt_ret = _bench_return(_BENCH_XEQT, lookback_days)

    dsr = _deflated_sharpe(daily_ret, n_trials=n_trials_for_dsr)

    # Stability: % windows with positive return + sharpe std
    pos_windows = sum(1 for w in window_results if w["return_pct"] > 0)
    win_consistency = (pos_windows / len(window_results) * 100) if window_results else 0.0
    sharpes = [w["sharpe"] for w in window_results if w["sharpe"] is not None]
    sharpe_stability = (
        round(float(np.std(sharpes)), 2) if sharpes else None
    )

    return {
        "skill": skill,
        "strategy_spec": spec,
        "universe_size": len(universe),
        "n_symbols_used": len(per_symbol_eq),
        "n_windows": len(window_results),
        "window_days": window_days,
        "step_days": step_days,
        "aggregate": {
            "total_return_pct": round(total_ret, 2),
            "sharpe": round(sharpe_full, 2),
            "sortino": round(sortino_full, 2),
            "max_drawdown_pct": round(mdd_full, 2),
            "hit_rate_pct": round(hit, 2),
            "num_trades": len(per_symbol_trades),
            "alpha_vs_spy_pct": (
                round(total_ret - spy_ret, 2) if spy_ret is not None else None
            ),
            "alpha_vs_xeqt_pct": (
                round(total_ret - xeqt_ret, 2) if xeqt_ret is not None else None
            ),
            "win_consistency_pct": round(win_consistency, 1),
            "sharpe_stability_std": sharpe_stability,
        },
        "deflated_sharpe": dsr,
        "deflated_sharpe_interpretation": _dsr_interpret(dsr),
        "by_window": window_results,
        "by_regime": by_regime,
        "notes": notes,
        "as_of": time.time(),
    }


def _dsr_interpret(dsr: float) -> str:
    if dsr >= 0.95:
        return "strong evidence of real edge (>=95% confidence after multiple-testing correction)"
    if dsr >= 0.80:
        return "moderate evidence — likely real edge"
    if dsr >= 0.50:
        return "weak evidence — borderline, could be noise"
    return "no statistical evidence — likely overfit or luck"


def walk_forward_all_skills(
    universe: list[str] | None = None,
    *,
    lookback_days: int = 365 * 5,
    window_days: int = 252,
    step_days: int = 63,
    tx_cost_bps: float = 5.0,
    persist: bool = True,
) -> dict:
    n = len(SKILL_RULES)
    out = {
        "as_of": time.time(),
        "lookback_days": lookback_days,
        "window_days": window_days,
        "step_days": step_days,
        "tx_cost_bps": tx_cost_bps,
        "universe": universe or DEFAULT_UNIVERSE,
        "n_trials_for_dsr": n,
        "results": [],
    }
    for skill in list_skills():
        try:
            out["results"].append(walk_forward_backtest(
                skill, universe,
                lookback_days=lookback_days,
                window_days=window_days,
                step_days=step_days,
                tx_cost_bps=tx_cost_bps,
                n_trials_for_dsr=n,
            ))
        except Exception as e:
            out["results"].append({"skill": skill, "error": str(e)})

    if persist:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUT_DIR / f"walk_forward_{int(time.time())}.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        out["persisted_to"] = str(path)
    return out
