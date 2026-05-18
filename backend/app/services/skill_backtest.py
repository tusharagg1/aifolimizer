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

_BENCH_SPY = "SPY"
_BENCH_XEQT = "XEQT.TO"

_OUT_DIR = Path(__file__).resolve().parents[2] / ".cache" / "backtests"


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


def _bars_to_close(symbol: str, lookback_days: int) -> pd.Series:
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


def _cagr(start: float, end: float, days: int) -> float:
    if start <= 0 or days <= 0:
        return 0.0
    years = days / 365.25
    try:
        return float(((end / start) ** (1 / years) - 1) * 100)
    except Exception:
        return 0.0


def _simulate(
    close: pd.Series, signal: pd.Series, tx_cost_bps: float
) -> tuple[pd.Series, list[Trade], list[float]]:
    """Long-only walk. Returns (equity_curve, trades, daily_returns_list)."""
    aligned = pd.concat([close, signal], axis=1).dropna()
    aligned.columns = ["close", "sig"]
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
) -> dict:
    out = {
        "as_of": time.time(),
        "lookback_days": lookback_days,
        "tx_cost_bps": tx_cost_bps,
        "universe": universe,
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
        path = _OUT_DIR / f"skill_backtests_{int(time.time())}.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        out["persisted_to"] = str(path)
    return out


def latest_results() -> dict | None:
    if not _OUT_DIR.exists():
        return None
    files = sorted(_OUT_DIR.glob("skill_backtests_*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))
