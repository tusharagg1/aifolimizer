"""Offline tests for the trust-report renderer + labeled-run lookup.

Pure-function coverage — no network, no live backtest. Verifies the
evidence-tier banner, survivorship caveat, two labeled blocks, and that
`latest_results(label)` filters correctly.
"""

import json
import os

from app.services import trust_report
from app.services import skill_backtest as skill_bt


def _block(universe, results):
    return {
        "universe": universe,
        "lookback_days": 1825,
        "tx_cost_bps": 5.0,
        "results": results,
    }


def test_evidence_tier_experimental_below_30():
    tier, n, why = trust_report._evidence_tier(
        {"7d": {"count": 2}, "30d": {"count": 2}, "90d": {"count": 2}}
    )
    assert tier == "EXPERIMENTAL"
    assert n == 2
    assert "<30" in why


def test_evidence_tier_developing_and_established():
    tier, n, _ = trust_report._evidence_tier({"90d": {"count": 45}})
    assert tier == "DEVELOPING" and n == 45
    tier2, n2, _ = trust_report._evidence_tier({"90d": {"count": 120}})
    assert tier2 == "SEED-ESTABLISHED" and n2 == 120


def test_evidence_tier_empty_windows():
    tier, n, _ = trust_report._evidence_tier({})
    assert tier == "EXPERIMENTAL" and n == 0


def test_universe_str_truncates_long_lists():
    short = trust_report._universe_str(["AAPL", "MSFT"])
    assert short == "AAPL, MSFT"
    long_list = [f"SYM{i}" for i in range(40)]
    out = trust_report._universe_str(long_list)
    assert "(40 total)" in out
    assert out.count(",") == 12  # 12 shown + the trailing "…" segment


def test_render_markdown_has_banner_caveat_and_two_blocks():
    holdings_rows = [
        {
            "skill": "portfolio_health", "strategy_spec": "buy_hold",
            "cagr_pct": 10.0, "sharpe": 1.0, "sortino": 1.2,
            "max_drawdown_pct": -12.0, "hit_rate_pct": 60.0,
            "alpha_vs_spy_pct": 2.0,
        }
    ]
    broad_universe = [
        "XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLU",
        "XLRE", "XLB", "XLC", "SPY", "QQQ",
    ]
    blocks = [
        ("Headline — Your Holdings", "sub a", _block(["AAPL", "MSFT"], holdings_rows)),
        ("Mechanics — Unbiased Broad Basket", "sub b", _block(broad_universe, [])),
    ]
    md = trust_report._render_markdown(
        "2026-01-01 00:00 UTC",
        blocks,
        {"7d": {"count": 2, "win_rate_pct": 50.0, "avg_return_pct": 0.0}},
        [],
    )
    assert "## Evidence Tier" in md
    assert "EXPERIMENTAL" in md
    assert "Survivorship-bias caveat" in md
    assert "Headline — Your Holdings" in md
    assert "Mechanics — Unbiased Broad Basket" in md
    assert "(13 total)" in md            # broad universe truncated w/ count
    assert "shifted one bar" in md       # look-ahead-free disclosure
    assert "portfolio_health" in md      # row rendered


def test_latest_results_label_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_bt, "_OUT_DIR", tmp_path)
    b = tmp_path / "skill_backtests_broad_100.json"
    b.write_text(json.dumps({"label": "broad"}), encoding="utf-8")
    h = tmp_path / "skill_backtests_holdings_200.json"
    h.write_text(json.dumps({"label": "holdings"}), encoding="utf-8")
    os.utime(b, (1000, 1000))
    os.utime(h, (2000, 2000))  # holdings is most-recent by mtime

    assert skill_bt.latest_results("broad")["label"] == "broad"
    assert skill_bt.latest_results("holdings")["label"] == "holdings"
    assert skill_bt.latest_results()["label"] == "holdings"


def test_latest_results_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_bt, "_OUT_DIR", tmp_path)
    assert skill_bt.latest_results("broad") is None
    assert skill_bt.latest_results() is None
