# Signal-Policy Rewrite Plan (Scope 3) - staged for review

*Drafted 2026-06-07. Informed by 353 scored forward signals + decay curve. NOT yet implemented - touches scoring logic, highest blast radius. Review before executing.*

## Why this is staged, not done
Calibration loop is now wired (`_evidence_context` reads `signal_history.calibrate_confidence`)
and the trust report can no longer hide negative EV (`SEED-NEGATIVE-EV` tier). Those were
reversible. The fixes below change what the engine *recommends* - gating them on the current
353-signal sample without calibrated probabilities risks overfitting to noise. Execute only
after a diff review.

## Evidence (live track record, 353 signals)

| Cut | n | Win% | Avg ret% | Alpha vs XEQT |
|---|---|---|---|---|
| BUY | 197 | 17.8 | -7.03 | -5.33 ❌ ENTIRE LOSS |
| ADD | 2 | 0.0 | -15.95 | - |
| TRIM | 137 | 76.6 | +7.65 | +6.10 ✅ |
| SELL | 17 | 94.1 | +9.58 | +7.56 ✅ |
| HIGH conviction | 208 | 23.6 | -5.79 | INVERTED vs MED |
| MED conviction | 143 | 74.1 | +7.06 | ✅ |
| top-trades-today | 11 | 0.0 | -9.78 | stop-hit 82% ❌ |

Decay curve (directional): h1 win 48.9% → h3 40.4% → h5 34.2%. Edge is ultra-short and
erodes within days; current +15% targets / -8% stops / multi-week implied horizon are
mismatched to signals that decay by h5.

## Root cause: `win_prob` heuristic is inverted vs reality
`recommendations.py:575-578` - `analyst_rec in (buy, strong_buy) → win_prob 0.62`;
`(sell, underperform) → 0.35`. Realized: analyst-buy names become BUY/HIGH and win 17.8%;
sell/underperform names become SELL and win 94%. The heuristic is anti-correlated with
outcomes. Sizing on it (Kelly) would lever into the losers.

## Fixes (priority order)

1. **Quarantine the BUY leg.** Raise the BUY score gate hard so marginal longs route to
   `NO_EDGE` instead of BUY. Target: cut BUY count sharply; only keep BUYs that clear a
   benchmark-relative bar (expected return > XEQT drift after 5bps×2 cost), not raw positive.
   - File: `_decide_action` in `recommendations.py` (~line 802+).

2. **Retire / invert the `analyst_rec → win_prob` map** until calibrated. Either (a) set a
   flat neutral `win_prob = 0.5` for all so Kelly→~0 and sizing stops amplifying losers, or
   (b) drive `win_prob` from the empirical `signal_history.calibrate_confidence` buckets once
   they populate at h≤5 (where data exists). Prefer (a) now, (b) after h5 buckets fill.
   - File: `recommendations.py:573-590`.

3. **Decouple HIGH conviction from analyst optimism.** HIGH currently tracks analyst_rec →
   tracks BUY → loses. Conviction must derive from realized-edge-bearing features (the working
   skills: momentum-scanner, position-review), not analyst sentiment. Until calibration says
   HIGH>MED>LOW holds, cap emitted conviction at MED for BUY actions.

4. **Kill / demote `top-trades-today` BUY output** (0/11, 82% stop-hit) until reworked.

5. **Re-match horizons to decay.** Signal peaks h1, gone by h5 → shorten target/stop/expected
   hold for technical/momentum entries (tight, fast). Wide +15%/-8% only for thesis-grade
   longs with fundamental support. Add a per-signal-type horizon field.
   - Files: `recommendations.py` stop/target block (520-564), exit-ladder logic.

6. **Benchmark-relative gate.** No signal labeled ≥ `reasonable_thesis` unless expected return
   beats XEQT/SPY over its horizon after costs. "Why this beats doing nothing?" baked into tier.

7. **Reframe generator → filter + AI-as-critic gate.** The engine should issue *fewer,
   better* calls. Before any BUY emits ≥ `reasonable_thesis`, route it through an LLM critic
   prompt ("why might this be wrong? what bucket does it resemble? what invalidates it? is
   this FOMO/chasing?") - AI as skeptic, not predictor. A critic veto downgrades to NO_EDGE.
   The `adversarial-research` skill already holds this logic; wire it as a pre-emit gate
   rather than an on-demand skill.

## Keep / promote
Defensive side has genuine edge - TRIM/SELL 77-94% win, +5-7% alpha. Working skills:
momentum-scanner (91.7%), position-review (84.6%), master-synthesis (75%), pead-tracker (71%).
Route more weight to these; do NOT touch them.

## Do NOT do yet
- Train ML model - wait for clean feature/outcome history + h21 buckets to populate.
- Point-in-time historical data ingest - separate workstream, prerequisite for training.
- Kelly sizing on `win_prob` - disabled/neutralized until calibration verdict ≠ uncalibrated.

## Verification gate before calling any fix "done"
- Re-run `score_signal_horizons` → `calibrate_confidence(h≤5)` after ~N new closed signals.
- Require: BUY-leg avg return ≥ 0 net-of-cost, OR BUY count materially cut.
- `get_signal_accuracy` HIGH bucket must beat MED before HIGH is re-enabled.

---

## Addendum (2026-06-08): two risk-brake gaps NOT covered above

Independent review of the same 353-signal run surfaced two holes the fixes 1-7
above do not touch. Both are about *risk braking*, orthogonal to the EV/calibration work.

### A. Regime fail-open - FIXED (committed)
`macro.market_breadth()` defaulted `spy_regime` to `"bull"` on a failed yfinance
fetch → composite `bull_low_fear` (most permissive) → headless fetch failures
silently green-lit BUYs. 100% of the 353 recs carried `bull_low_fear` (zero
regime variation across 6 weeks) - consistent with frequent fail-open in the
scheduled/headless rec-cycle.
- Fix (committed `80259cd` + working-tree recs lines): `market_breadth()` emits
  `"unknown"` when SPY direction is unconfirmed; `recommendations.py` adds
  `"unknown"` to `_REGIME_BUY_HOSTILE` and defaults `market_regime` to
  `"unknown"` (was `bull_low_fear`). Unknown regime → BUY downgraded to WATCH.
- NOTE: the two `recommendations.py` lines (`_REGIME_BUY_HOSTILE` + line ~372
  default) are in the working tree alongside the Scope-3 edits; they commit
  together when this file lands.

### B. Portfolio risk-gate not wired to the engine - DEFERRED (this is "Scope-4 / C")
The raw engine path logs its own recs (`recommendations.py:1054`
`batch_log_recommendations(skill="recommendations_engine")`) inside the **sync,
no-DB, no-tenant** scorer. It never consults `risk_gate` (async, Postgres/Redis,
tenant-scoped). So portfolio-level halts (drawdown / loss-streak / VIX / ECE)
do NOT brake engine BUYs. The LLM-skill path *is* gate-aware
(`skill_llm_runner` carries `risk_gate_status`); the bulk engine path is not.
- Correct design (respects sync/async boundary): async callers
  (`ws.py`, `jobs/scheduler.py`, `mcp_server.py` get_trade_ideas) fetch
  `risk_gate` state and pass a primitive `buys_halted: bool` (+ `size_multiplier`)
  into the sync `get_recommendations`; engine downgrades BUY→WATCH on halt.
  Alternative low-touch placement: gate at `trade_ideas.py` (surfacing layer,
  already async) so halted BUYs are not *shown*, without editing the engine.
- Deferred because it is a 3-4 file change into this contended file; apply after
  Scope-3 commits. Inversion is already substantially braked by fixes 1-2 + (A);
  this is circuit-breaker hardening, not an active bleed.
