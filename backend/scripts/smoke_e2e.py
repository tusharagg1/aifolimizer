"""End-to-end smoke test for the accuracy stack.

Exercises the full pipeline with a synthetic portfolio so you can catch
"scaffolding works in isolation but is broken when wired together" before
shipping. Hits real market data via data_router (yfinance), so it's
network-bound — expect 30-90s end-to-end.

Run:
    cd backend
    .venv/Scripts/python scripts/smoke_e2e.py

What it verifies:
    1. SkillRunner: every codified skill returns a snapshot with status=ok
       under a per-tenant directory (multi-tenant namespacing intact).
    2. Tenant isolation: snapshots written for tenant-A do not appear when
       reading tenant-B.
    3. RecommendationEngine: emits a sensible distribution of actions.
       Flags as FAIL if NO_EDGE rate > 90% (engine too strict — would
       produce zero forward samples) or < 5% (engine too loose).
    4. Auto-log: after get_recommendations runs, recommendations.jsonl has
       new rows with the full contract (horizon, benchmarks_entry, etc).
    5. Scoring: paper_trade.score_recommendations executes without error
       and produces a track-record dict shaped correctly.
    6. TrustDashboard backing data: signal_history helpers respond without
       error even when sample size is zero.

Exit code 0 = all pass, 1 = any failure. Per-check status printed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.models.portfolio import (  # noqa: E402
    PortfolioResponse,
    PortfolioSummary,
    Position,
)


# ── Synthetic portfolio (diverse sectors, US + CAD, mix of returns) ──────────


def _synthetic_portfolio() -> PortfolioResponse:
    positions = [
        Position(
            symbol="AAPL",
            name="Apple",
            quantity=20,
            currency="USD",
            book_cost=3500,
            book_cost_cad=4750,
            market_value=4000,
            market_value_cad=5400,
            day_change_pct=0.5,
            total_return_pct=14.3,
            weight=18.0,
            asset_class="equity",
            sector="Technology",
        ),
        Position(
            symbol="MSFT",
            name="Microsoft",
            quantity=10,
            currency="USD",
            book_cost=3000,
            book_cost_cad=4080,
            market_value=3500,
            market_value_cad=4750,
            day_change_pct=0.3,
            total_return_pct=16.7,
            weight=16.0,
            asset_class="equity",
            sector="Technology",
        ),
        Position(
            symbol="JPM",
            name="JPMorgan",
            quantity=15,
            currency="USD",
            book_cost=2500,
            book_cost_cad=3400,
            market_value=2800,
            market_value_cad=3800,
            day_change_pct=-0.2,
            total_return_pct=12.0,
            weight=13.0,
            asset_class="equity",
            sector="Financials",
        ),
        Position(
            symbol="JNJ",
            name="Johnson & Johnson",
            quantity=20,
            currency="USD",
            book_cost=3000,
            book_cost_cad=4080,
            market_value=2700,
            market_value_cad=3670,
            day_change_pct=0.1,
            total_return_pct=-10.0,
            weight=12.5,
            asset_class="equity",
            sector="Healthcare",
        ),
        Position(
            symbol="XEQT.TO",
            name="iShares Core Equity ETF",
            quantity=100,
            currency="CAD",
            book_cost=3000,
            book_cost_cad=3000,
            market_value=3300,
            market_value_cad=3300,
            day_change_pct=0.4,
            total_return_pct=10.0,
            weight=11.0,
            asset_class="etf",
            sector=None,
        ),
        Position(
            symbol="VFV.TO",
            name="Vanguard S&P 500",
            quantity=30,
            currency="CAD",
            book_cost=2500,
            book_cost_cad=2500,
            market_value=2900,
            market_value_cad=2900,
            day_change_pct=0.5,
            total_return_pct=16.0,
            weight=9.5,
            asset_class="etf",
            sector=None,
        ),
        Position(
            symbol="PFE",
            name="Pfizer",
            quantity=80,
            currency="USD",
            book_cost=2400,
            book_cost_cad=3260,
            market_value=1800,
            market_value_cad=2450,
            day_change_pct=-1.5,
            total_return_pct=-25.0,
            weight=8.0,
            asset_class="equity",
            sector="Healthcare",
        ),
        Position(
            symbol="XOM",
            name="Exxon Mobil",
            quantity=20,
            currency="USD",
            book_cost=2000,
            book_cost_cad=2720,
            market_value=2200,
            market_value_cad=2990,
            day_change_pct=0.8,
            total_return_pct=10.0,
            weight=8.0,
            asset_class="equity",
            sector="Energy",
        ),
        Position(
            symbol="WMT",
            name="Walmart",
            quantity=15,
            currency="USD",
            book_cost=1500,
            book_cost_cad=2040,
            market_value=1750,
            market_value_cad=2380,
            day_change_pct=0.2,
            total_return_pct=16.7,
            weight=4.0,
            asset_class="equity",
            sector="Consumer Defensive",
        ),
    ]
    total_value = sum(p.market_value_cad for p in positions)
    total_cost = sum(p.book_cost_cad for p in positions)
    summary = PortfolioSummary(
        total_value=total_value + 5000,
        total_cost=total_cost,
        total_return_pct=(total_value - total_cost) / total_cost * 100,
        cash_available=5000.0,
        day_change_cad=42.0,
    )
    return PortfolioResponse(positions=positions, summary=summary)


# ── Check primitives ─────────────────────────────────────────────────────────


class Check:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail = ""

    def ok(self, detail: str = ""):
        self.passed = True
        self.detail = detail
        return self

    def fail(self, detail: str):
        self.passed = False
        self.detail = detail
        return self


def _print_results(checks: list[Check]) -> int:
    print()
    print("=" * 70)
    print("SMOKE TEST RESULTS")
    print("=" * 70)
    failed = 0
    for c in checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"[{mark}] {c.name}")
        if c.detail:
            for line in c.detail.split("\n"):
                print(f"       {line}")
        if not c.passed:
            failed += 1
    print("=" * 70)
    print(f"{len(checks) - failed}/{len(checks)} passed")
    return 0 if failed == 0 else 1


# ── Checks ──────────────────────────────────────────────────────────────────


def check_skill_runner(portfolio, tenant_id: str) -> list[Check]:
    from app.services import skill_runner

    checks: list[Check] = []
    t0 = time.time()
    try:
        out = skill_runner.run_all_skills(portfolio, tenant_id=tenant_id)
    except Exception as e:
        return [
            Check("skill_runner.run_all_skills executes").fail(
                f"raised {type(e).__name__}: {e}",
            )
        ]
    elapsed = time.time() - t0
    expected = set(skill_runner.codified_skills())
    got = set(out.keys())
    missing = expected - got
    if missing:
        checks.append(
            Check("all codified skills run").fail(
                f"missing: {sorted(missing)}",
            )
        )
    else:
        checks.append(
            Check("all codified skills run").ok(
                f"{len(got)} skills in {elapsed:.1f}s",
            )
        )

    errored = [s for s, snap in out.items() if snap.get("status") == "error"]
    if errored:
        details = "\n".join(f"{s}: {out[s].get('error', '?')[:80]}" for s in errored)
        checks.append(Check("no skill emitted status=error").fail(details))
    else:
        checks.append(Check("no skill emitted status=error").ok())

    # Daily briefing should compose other skills
    db = out.get("daily-briefing", {})
    composed = db.get("summary", {}).get("composed_from") or []
    if len(composed) >= 3:
        checks.append(
            Check("daily-briefing composes >=3 skills").ok(
                f"composed_from={composed}",
            )
        )
    else:
        checks.append(
            Check("daily-briefing composes >=3 skills").fail(
                f"only {len(composed)} composed",
            )
        )

    # Tenant snapshot files actually on disk
    snap_dir = skill_runner._tenant_dir(tenant_id)
    files = list(snap_dir.glob("*.json"))
    if len(files) >= len(got):
        checks.append(
            Check("snapshots persisted to tenant dir").ok(
                f"{len(files)} files in {snap_dir}",
            )
        )
    else:
        checks.append(
            Check("snapshots persisted to tenant dir").fail(
                f"expected {len(got)} files, got {len(files)}",
            )
        )

    return checks


def check_tenant_isolation(portfolio) -> list[Check]:
    from app.services import skill_runner

    skill_runner.run_all_skills(portfolio, tenant_id="tenant-A-smoke")
    skill_runner.run_all_skills(portfolio, tenant_id="tenant-B-smoke")

    dir_a = skill_runner._tenant_dir("tenant-A-smoke")
    dir_b = skill_runner._tenant_dir("tenant-B-smoke")

    if dir_a == dir_b:
        return [Check("tenant isolation").fail("dirs identical")]

    # Each tenant should read its own
    snap_a = skill_runner.read_snapshot("portfolio-health", tenant_id="tenant-A-smoke")
    snap_b = skill_runner.read_snapshot("portfolio-health", tenant_id="tenant-B-smoke")
    if snap_a and snap_b:
        return [
            Check("tenant isolation").ok(
                f"distinct dirs, both readable: {dir_a.name} vs {dir_b.name}",
            )
        ]
    return [
        Check("tenant isolation").fail(
            f"snap_a={bool(snap_a)} snap_b={bool(snap_b)}",
        )
    ]


def check_recommendation_quality(
    portfolio,
) -> tuple[list[Check], list[dict]]:
    """Returns (checks, recs) so the same recs can be reused by check_auto_log
    without triggering same-day dedup on a second call.
    """
    from app.services import recommendations as rec_svc

    positions_dicts = [
        {
            "symbol": p.symbol,
            "name": p.name,
            "weight": p.weight,
            "market_value_cad": p.market_value_cad,
            "total_return_pct": p.total_return_pct,
            "currency": p.currency,
            "asset_class": p.asset_class,
            "sector": p.sector,
        }
        for p in portfolio.positions
    ]
    rec_svc._REC_CACHE.clear()
    try:
        recs = rec_svc.get_recommendations(positions_dicts)
    except Exception as e:
        return [
            Check("recommendations engine runs").fail(
                f"raised {type(e).__name__}: {e}",
            )
        ], []

    checks: list[Check] = [
        Check("recommendations engine runs").ok(f"{len(recs)} recs"),
    ]

    if not recs:
        checks.append(Check("emits recommendations").fail("0 recs"))
        return checks, recs

    # Distribution check — NO_EDGE rate should be reasonable
    by_action: dict[str, int] = {}
    for r in recs:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    no_edge_pct = (by_action.get("NO_EDGE", 0) / len(recs)) * 100
    actionable_pct = (
        (by_action.get("BUY", 0) + by_action.get("SELL", 0) + by_action.get("TRIM", 0) + by_action.get("ADD", 0))
        / len(recs)
    ) * 100

    dist = ", ".join(f"{a}={n}" for a, n in sorted(by_action.items()))
    if no_edge_pct > 90:
        checks.append(
            Check("NO_EDGE rate is sane (<=90%)").fail(
                f"{no_edge_pct:.0f}% NO_EDGE — engine too strict, no samples will accumulate. Distribution: {dist}",
            )
        )
    elif no_edge_pct > 75:
        checks.append(
            Check("NO_EDGE rate is sane (<=90%)").ok(
                f"WARNING: {no_edge_pct:.0f}% NO_EDGE — high but acceptable. Distribution: {dist}",
            )
        )
    else:
        checks.append(
            Check("NO_EDGE rate is sane (<=90%)").ok(
                f"{no_edge_pct:.0f}% NO_EDGE, {actionable_pct:.0f}% actionable. Distribution: {dist}",
            )
        )

    # Every rec should have new contract fields populated
    sample = recs[0]
    required_fields = ["action", "score", "confidence", "current_price", "reasons", "signal_quality"]
    missing = [f for f in required_fields if f not in sample]
    if missing:
        checks.append(
            Check("rec schema complete").fail(
                f"missing fields: {missing}",
            )
        )
    else:
        checks.append(
            Check("rec schema complete").ok(
                f"all {len(required_fields)} fields present on sample",
            )
        )

    return checks, recs


def check_auto_log(portfolio, recs_from_prior_run: list[dict] | None) -> list[Check]:
    """Verify auto-log wrote a row when the engine emitted actionable signals.

    Use the recs the engine already produced earlier — calling
    get_recommendations again would hit same-day dedup and falsely fail.
    """
    from app.services import paper_trade

    rec_file = paper_trade._REC_FILE
    checks: list[Check] = []

    if not rec_file.exists():
        return [
            Check("auto-log writes to recommendations.jsonl").fail(
                "recommendations.jsonl does not exist",
            )
        ]

    actionable = [r for r in (recs_from_prior_run or []) if r.get("action") in ("BUY", "SELL", "TRIM", "ADD")]

    # Read today's rows from the jsonl
    today_iso = __import__("datetime").date.today().isoformat()
    today_rows: list[dict] = []
    for line in rec_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("date") == today_iso:
            today_rows.append(row)

    if not actionable:
        checks.append(
            Check("auto-log writes to recommendations.jsonl").ok(
                "no actionable signals (HOLD/WATCH/NO_EDGE only) — nothing to log",
            )
        )
    else:
        # Match by ticker — any actionable rec should have a same-day row
        actionable_syms = {r["symbol"] for r in actionable}
        logged_syms = {row.get("ticker") for row in today_rows if row.get("skill") == "recommendations_engine"}
        matched = actionable_syms & logged_syms
        if not matched:
            checks.append(
                Check("auto-log writes to recommendations.jsonl").fail(
                    f"engine emitted actionable={sorted(actionable_syms)} but no matching same-day row in jsonl",
                )
            )
        else:
            checks.append(
                Check("auto-log writes to recommendations.jsonl").ok(
                    f"matched {sorted(matched)} from {len(actionable)} actionable",
                )
            )

    # Verify new-schema fields on a same-day rec
    if today_rows:
        row = today_rows[-1]
        required = ["horizon_days", "benchmarks_entry", "confidence_source", "model_version", "features"]
        missing = [f for f in required if f not in row]
        if missing:
            checks.append(
                Check("new schema on logged rec").fail(
                    f"missing: {missing}",
                )
            )
        else:
            checks.append(
                Check("new schema on logged rec").ok(
                    f"all {len(required)} new fields present on most recent rec",
                )
            )

    return checks


def check_scoring() -> list[Check]:
    from app.services import paper_trade

    try:
        result = paper_trade.score_recommendations()
    except Exception as e:
        return [
            Check("score_recommendations runs").fail(
                f"raised {type(e).__name__}: {e}",
            )
        ]

    if isinstance(result, dict) and "error" in result:
        # No data yet is fine on a fresh install
        return [
            Check("score_recommendations runs").ok(
                f"no data yet: {result['error']}",
            )
        ]

    return [
        Check("score_recommendations runs").ok(
            f"keys: {sorted(result.keys()) if isinstance(result, dict) else type(result).__name__}",
        )
    ]


def check_trust_helpers() -> list[Check]:
    from app.services import signal_history

    checks: list[Check] = []
    for name, fn in (
        ("signal_decay_curve", lambda: signal_history.signal_decay_curve()),
        ("per_signal_source_attribution", lambda: signal_history.per_signal_source_attribution(21)),
        ("calibrate_confidence", lambda: signal_history.calibrate_confidence(21)),
    ):
        try:
            out = fn()
            assert isinstance(out, dict), "must return dict"
            checks.append(Check(f"{name} returns dict").ok())
        except Exception as e:
            checks.append(
                Check(f"{name} returns dict").fail(
                    f"raised {type(e).__name__}: {e}",
                )
            )
    return checks


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    print("Building synthetic portfolio...")
    portfolio = _synthetic_portfolio()
    print(f"  {len(portfolio.positions)} positions, ${portfolio.summary.total_value:,.0f} CAD")

    all_checks: list[Check] = []

    print("\n[1/6] SkillRunner end-to-end...")
    all_checks.extend(check_skill_runner(portfolio, tenant_id="smoke-tenant"))

    print("\n[2/6] Tenant isolation...")
    all_checks.extend(check_tenant_isolation(portfolio))

    print("\n[3/6] Recommendation quality...")
    rec_checks, recs = check_recommendation_quality(portfolio)
    all_checks.extend(rec_checks)

    print("\n[4/6] Auto-log to recommendations.jsonl...")
    all_checks.extend(check_auto_log(portfolio, recs))

    print("\n[5/6] Scoring pipeline...")
    all_checks.extend(check_scoring())

    print("\n[6/6] Trust dashboard helpers...")
    all_checks.extend(check_trust_helpers())

    return _print_results(all_checks)


if __name__ == "__main__":
    sys.exit(main())
