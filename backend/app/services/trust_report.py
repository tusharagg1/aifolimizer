"""Trust signal report generator.

Produces two artefacts:
1. TRACK_RECORD.md (repo root) - public summary suitable for GitHub.
   Contains: methodology, backtest KPIs, live rec stats, data reliability.
   No PII, no individual trade detail.

2. .claude/context/track_record_full.jsonl (gitignored) - raw evidence.
   Contains: every scored recommendation with ticker + return.

Call generate_report() to refresh both. Designed to be called
once per week via scheduler or manually via MCP tool.

The report shows up to two labeled backtest blocks:
- "holdings"  - personalized run on live portfolio (decision-relevant headline)
- "broad"     - unbiased 40+ symbol basket (strategy mechanics, no single-name luck)
Each is picked up independently from the latest persisted run of that label.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services import skill_backtest as skill_bt_svc
from app.services import paper_trade as pt_svc
from app.services import data_cache as cache_svc

_ROOT = Path(__file__).resolve().parents[3]
_CTX = _ROOT / ".claude" / "context"
_PUBLIC_OUT = _ROOT / "TRACK_RECORD.md"
_PRIVATE_OUT = _CTX / "track_record_full.jsonl"


def generate_report() -> dict:
    """Build TRACK_RECORD.md and private detail jsonl. Return summary."""
    ts = time.time()
    dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # --- Backtest blocks: prefer labeled runs (holdings headline + broad
    # mechanics); fall back to any legacy unlabeled run. ---
    holdings = skill_bt_svc.latest_results("holdings")
    broad = skill_bt_svc.latest_results("broad")
    blocks: list[tuple[str, str, dict]] = []
    if holdings:
        blocks.append(
            (
                "Headline - Your Holdings",
                "Run on your live portfolio top holdings - decision-relevant, but a small single-account universe.",
                holdings,
            )
        )
    if broad:
        blocks.append(
            (
                "Mechanics - Unbiased Broad Basket",
                "40+ symbols across 11 GICS sectors including laggards and drawdown "
                "names - tests strategy mechanics, not single-name luck.",
                broad,
            )
        )
    if not blocks:
        legacy = skill_bt_svc.latest_results()
        blocks.append(("Backtest Results", "", legacy or {}))

    # --- Live track record ---
    live = pt_svc.get_track_record(windows=[7, 30, 90])
    live_windows = live.get("windows", {})

    # --- Source reliability ---
    reliability = cache_svc.source_stats_summary(since_s=86400 * 30)

    # --- Write private jsonl ---
    evidence = {
        "generated_at": ts,
        "backtest_holdings": holdings,
        "backtest_broad": broad,
        "live_track_record": live,
        "source_reliability": reliability,
    }
    _CTX.mkdir(parents=True, exist_ok=True)
    with _PRIVATE_OUT.open("w", encoding="utf-8") as f:
        f.write(json.dumps(evidence) + "\n")

    # --- Write public markdown ---
    md = _render_markdown(dt_str, blocks, live_windows, reliability)
    _PUBLIC_OUT.write_text(md, encoding="utf-8")

    total_skills = sum(len(b[2].get("results", [])) for b in blocks)
    return {
        "generated_at": ts,
        "public": str(_PUBLIC_OUT),
        "private": str(_PRIVATE_OUT),
        "backtest_skills": total_skills,
        "live_recs_logged": live.get("total_logged", 0),
    }


def _evidence_tier(live_windows: dict) -> tuple[str, int, str]:
    """Honest trust tier driven by forward (out-of-sample) sample size AND
    realized net expectancy. Closed forward signals are the only OOS evidence
    we have. A large sample with negative expectancy is evidence of a LOSING
    policy, not of edge - it must never read as a positive milestone, so the
    sign of realized return gates the label regardless of how many signals
    closed."""
    forward_n = max((w.get("count", 0) for w in live_windows.values()), default=0)
    widest = max(live_windows.values(), key=lambda w: w.get("count", 0), default={})
    avg_ret = widest.get("avg_return_pct")
    win_rate = widest.get("win_rate_pct")
    neg_ev = avg_ret is not None and avg_ret <= 0
    ev_str = (
        f"avg realized return {avg_ret:+.2f}% (win rate {win_rate}%)"
        if avg_ret is not None
        else "realized expectancy unknown"
    )

    if forward_n < 30:
        return (
            "EXPERIMENTAL",
            forward_n,
            f"Only {forward_n} closed forward signals (<30). Recommendations are "
            "unproven. Backtests below are in-sample proxies, NOT evidence of edge.",
        )
    if forward_n < 100:
        if neg_ev:
            return (
                "DEVELOPING-NEGATIVE-EV",
                forward_n,
                f"⚠️ NEGATIVE EXPECTANCY - {ev_str} over {forward_n} closed forward "
                "signals (30-99). The live signal policy is currently losing; this "
                "is not emerging edge. Treat every recommendation as experimental.",
            )
        return (
            "DEVELOPING",
            forward_n,
            f"{forward_n} closed forward signals (30-99), {ev_str}. Trend forming "
            "but below the ~100-signal bar for trusting confidence labels.",
        )
    if neg_ev:
        return (
            "SEED-NEGATIVE-EV",
            forward_n,
            f"⚠️ NEGATIVE EXPECTANCY - {forward_n} closed forward signals (≥100) but "
            f"{ev_str}. Sample size is met; net-of-cost edge is NOT. This is positive "
            "evidence that the live signal policy LOSES money. Every recommendation "
            "remains experimental until expectancy turns positive AND calibrates.",
        )
    return (
        "SEED-ESTABLISHED",
        forward_n,
        f"{forward_n} closed forward signals (≥100), {ev_str}. Sample met AND "
        "expectancy positive - judge by calibration and net-of-cost expectancy.",
    )


def _universe_str(universe: list[str]) -> str:
    if not universe:
        return "-"
    if len(universe) <= 12:
        return ", ".join(universe)
    return ", ".join(universe[:12]) + f", … ({len(universe)} total)"


def _render_backtest_block(a, title: str, subtitle: str, bt_meta: dict) -> None:
    a(f"### {title}")
    a("")
    if subtitle:
        a(subtitle)
        a("")
    a(f"- Universe: {_universe_str(bt_meta.get('universe', []))}")
    a(f"- Lookback: {bt_meta.get('lookback_days', 0)} days")
    a(f"- Transaction cost: {bt_meta.get('tx_cost_bps', 0)} bps/leg")
    a("")
    a("| Skill | Rule Proxy | CAGR % | Sharpe | Sortino | Max DD % | Hit Rate % | Alpha vs SPY % |")
    a("|---|---|---|---|---|---|---|---|")
    for r in bt_meta.get("results", []):
        if "error" in r:
            a(f"| {r['skill']} | - | error | | | | | |")
            continue
        alpha_spy = r.get("alpha_vs_spy_pct")
        a_str = f"{alpha_spy:+.1f}" if alpha_spy is not None else "-"
        a(
            f"| {r['skill']} "
            f"| {r.get('strategy_spec', '')} "
            f"| {r.get('cagr_pct', '')} "
            f"| {r.get('sharpe', '')} "
            f"| {r.get('sortino', '')} "
            f"| {r.get('max_drawdown_pct', '')} "
            f"| {r.get('hit_rate_pct', '')} "
            f"| {a_str} |"
        )
    a("")
    a("*SPY and XEQT.TO returns over same period used as benchmark.*")
    a("")


def _render_markdown(
    dt_str: str,
    blocks: list[tuple[str, str, dict]],
    live_windows: dict,
    reliability: list[dict],
) -> str:
    lines: list[str] = []
    a = lines.append

    a("# aifolimizer - Track Record & Methodology")
    a("")
    a(f"*Last generated: {dt_str}*  ")
    a("*Auto-generated by `trust_report.generate_report()`. Refresh: `mcp__aifolimizer__generate_trust_report`.*")
    a("")
    a("---")
    a("")
    a("## Disclaimer")
    a("")
    a("> This is a personal DIY investment tool, not financial advice. Past results are")
    a("> not predictive of future performance. The author is not a registered advisor.")
    a("> All figures are self-reported and unaudited by a third party.")
    a("")
    a("---")
    a("")

    # --- Evidence tier banner (data-driven, forward-sample gated) ---
    tier, forward_n, why = _evidence_tier(live_windows)
    a("## Evidence Tier")
    a("")
    a(f"> **{tier}** - {why}")
    a(">")
    a("> Evidence hierarchy used here: `proven edge` (large forward sample + calibrated +")
    a("> positive net expectancy) > `reasonable thesis` > `experimental`. Until the forward")
    a("> sample is large enough, treat every recommendation as **experimental**.")
    a(">")
    a("> **Survivorship-bias caveat:** the broad backtest universe is a fixed list of names")
    a("> that still trade today. Free data lacks delisted/merged tickers, so backtest returns")
    a("> are biased upward versus a true point-in-time universe. Treat them as a lower-bound")
    a("> sanity check on mechanics, not a return forecast.")
    a("")
    a("---")
    a("")

    a("## Methodology")
    a("")
    a("### Data Sources")
    a("| Source | Type | Tier |")
    a("|---|---|---|")
    a("| yfinance | prices, fundamentals, news | free, no key |")
    a("| FRED | macro (Fed funds, CPI, CAD/USD) | free, no key |")
    a("| CoinGecko v3 | crypto prices | free, no key |")
    a("| CNN Fear & Greed | sentiment | free, no key |")
    a("| Google News RSS | news headlines | free, no key |")
    a("| Finnhub | quotes + fundamentals fallback | free, key required |")
    a("| Alpha Vantage | fundamentals fallback | free, key required |")
    a("| Tiingo | EOD history fallback | free, key required |")
    a("")
    a("### Skills Backtested")
    a("")
    a("Each skill is approximated by a deterministic Python rule applied to OHLC bars.")
    a("LLM thesis, news sentiment, and qualitative overlay are NOT replayed.")
    a("Treat rule-based results as a **lower bound** on actual skill quality.")
    a("Signals are shifted one bar before execution (no same-bar look-ahead).")
    a("")
    a("---")
    a("")

    a("## Historical Backtest Results")
    a("")
    for title, subtitle, bt_meta in blocks:
        _render_backtest_block(a, title, subtitle, bt_meta)
    a("---")
    a("")

    a("## Forward Paper-Trade (Live)")
    a("")
    a("Every recommendation logged via `log_recommendation` MCP tool.")
    a("Scored daily via `score_recommendations`.")
    a(f"Closed forward signals so far: **{forward_n}** (out-of-sample evidence).")
    a("")
    a("| Window | Count | Win Rate % | Avg Return % |")
    a("|---|---|---|---|")
    for wkey, wdata in live_windows.items():
        count = wdata.get("count", 0)
        wr = wdata.get("win_rate_pct", "-")
        ar = wdata.get("avg_return_pct", "-")
        a(f"| {wkey} | {count} | {wr} | {ar} |")
    a("")
    a("---")
    a("")
    a("## Data Source Reliability (30 days)")
    a("")
    a("| Source | Calls | Success % | Avg Latency ms |")
    a("|---|---|---|---|")
    if reliability:
        for r in reliability:
            a(f"| {r['source']} | {r['calls']} | {r.get('success_rate_pct', '-')} | {r.get('avg_latency_ms', '-')} |")
    else:
        a("| *No calls recorded yet* | - | - | - |")
    a("")
    a("---")
    a("")
    a("## Wealthsimple Managed Comparison")
    a("")
    a("Published approximate annualized returns (CA, gross, 2025 disclosure):")
    a("")
    a("| Profile | 1Y % | 3Y % | 5Y % |")
    a("|---|---|---|---|")
    from app.services.alpha_attribution import _WS_MANAGED

    for profile, perf in _WS_MANAGED.items():
        a(f"| {profile} | {perf['1y']} | {perf['3y']} | {perf['5y']} |")
    a("")
    a("*Compare your annualized return from `get_alpha_attribution` to the profile matching your risk level.*")
    a("")
    a("---")
    a("")
    a("## Audit Trail")
    a("")
    a("- Recommendations: `.claude/context/recommendations.jsonl` (gitignored, local)")
    a("- Scored history: `.claude/context/scored_recommendations.jsonl` (gitignored, local)")
    a("- Equity curve: `.claude/context/portfolio_history.jsonl` (gitignored, local)")
    a("- Backtest runs: `backend/.cache/backtests/` (gitignored, local)")
    a("- This file: `TRACK_RECORD.md` - committed to repo, git-timestamped")
    a("")
    a("Git history of this file provides tamper-evident timestamps for each report version.")
    a("")

    return "\n".join(lines)
