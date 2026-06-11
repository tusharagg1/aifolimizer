---
name: position-review
description: Review a holding (or sweep top holdings) and return a HOLD / TRIM / SELL verdict with entry/stop/target. Routes each name to the right analysis - earnings-analyzer if earnings are imminent, earnings-postmortem if it just reported, the adversarial bull/bear pipeline for high-conviction names, lighter stock-analysis otherwise. Use when the user asks "should I hold or sell X", "review my NVDA position", "review my top holdings", "position review", or for an automated nightly top-holdings sweep (the unqualified whole-book "review my holdings" belongs to portfolio-review). Logs each verdict for forward tracking.
requires_profile: true
---

# Position Review (routing orchestrator → hold/sell verdict)

## What this is
A router, not a new analysis. It picks the cheapest sufficient analysis per holding and emits one decision. Claude is the orchestrator - it calls shared MCP tools and, for deep names, runs the adversarial pipeline inline. Skills are not self-invoked.

## Modes
- **Single ticker** ("review my NVDA position"): route + verdict for that one name.
- **Sweep** ("review my holdings" / automated nightly): take top-N holdings by weight (default 6) and route each. Keep total output tight.

## Stage 0 - Decision Memory (load FIRST)

Before routing, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` - portfolio-level win/loss patterns
- For each name reviewed, load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`).

Reconciliation rule: if a prior decision exists and your new read flips it, state explicitly WHY it changed (new data / catalyst / price move). Never silently contradict a logged decision - that drift is exactly what this prevents.

## How to run
Call `get_profile` FIRST. Then gather routing signals (parallel):

1. `mcp__aifolimizer__get_portfolio` - holdings, weights, cost basis, return
2. `mcp__aifolimizer__get_personal_context` - province / marginal_tax_rate_pct / account_waterfall to ground per-name tax + account framing. If `present=false`, note the framing is generic and suggest the profile-setup skill.
3. `mcp__aifolimizer__get_earnings_calendar` - earnings proximity per name (pass watchlist `symbols=` only if reviewing a non-held name)
4. `mcp__aifolimizer__get_triggered_alerts` (since_hours=48) - recent price/RSI/concentration flags
5. `mcp__aifolimizer__get_earnings_results` for names that may have just reported

## Routing table (apply per ticker, first match wins)
| Condition | Route to | Why |
|---|---|---|
| Earnings within ~7 days | **earnings-analyzer** flow (`get_fundamentals` + `get_technicals` + expected move) | Pre-earnings risk dominates the decision |
| Reported in last ~5 days OR surprise flagged | **earnings-postmortem** flow (`get_earnings_results` + `get_news_headlines`) | Beat/miss reaction sets the near-term path |
| Weight ≥ 8% OR a triggered alert OR user asked for depth | **adversarial-research** pipeline ([adversarial-research](../adversarial-research/SKILL.md)) run INLINE | High stakes → full bull/bear/risk debate |
| Everything else | **stock-analysis** flow (`get_fundamentals` + `get_technicals` + `get_positioning_signals`) | Cheap single-pass read is enough |

After the routed analysis yields a lean, cross-check exits:
- Verdict SELL/TRIM on a **Non-Reg loss** → call `get_tax_loss_candidates`; note harvest opportunity + 30-day superficial-loss caution.
- Any BUY/ADD lean → `get_positioning_signals` crowding guard (consensus ≥70 → downgrade, don't add into the crowd).

Once the verdict is set, get concrete levels from `mcp__aifolimizer__get_trade_ticket` (`ticker`, `action=<verdict: HOLD|TRIM|SELL>`, `conviction`). It pulls cost basis + held quantity from the live session, so:
- **HOLD** → `exit_ladder` (T1/T2/T3 profit-taking from current price) + `stop_loss_price` + `position.stop_below_cost`. This is the "where do I take profit / cut loss" plan.
- **TRIM/SELL** → market-exit ticket (no ladder by design - show `stop_loss_price` only).
Render these instead of hand-computing stop/target.

## Subagent-nesting constraint (important for the sweep)
Subagents cannot spawn subagents. The adversarial pipeline already spawns 6 agents, so in a multi-holding sweep you CANNOT nest it per ticker. Pattern:
- Run the heavy adversarial pipeline **inline (main context)** for at most the 1-2 highest-stakes flagged names.
- For the rest, run the flat single-pass `stock-analysis`/earnings flow inline.
- Bound cost: never run more than ~2 deep pipelines per sweep; downgrade the rest to flat.

## Output (per ticker)
```
TICKER · weight X% · VERDICT: HOLD / TRIM / SELL · conviction
  route: <earnings / postmortem / adversarial / stock-analysis>
  held:  avg $.. · ret +X% · stop below cost? Y/N
  HOLD → exits: T1 $.. (sell 40%) · T2 $.. (35%) · T3 $.. (25%) · stop $Y
  TRIM/SELL → stop $Y  (no ladder by design)
  reason: <1-2 lines - the decisive factor>
  tax: <Non-Reg harvest note if SELL/TRIM at a loss, else omit>
```
Sweep mode: lead with a one-line roster (`HOLD x4 · TRIM x1 · SELL x1`), then the blocks, worst-conviction-holds and all SELL/TRIM first.

## After output - log decisions
For each name log the verdict (this feeds the forward win-rate / track-record loop). Pick ONE tool - they take different params:
- **Default path → `mcp__aifolimizer__log_recommendation`**: `skill="position-review"`, action, conviction, rationale, and levels as `target_pct` + `stop_pct` (PERCENT from entry; entry is captured live at call time - do NOT pass absolute prices).
- **Adversarial route ran → `mcp__aifolimizer__log_trade_decision`**: `skill_used="position-review"`, action, conviction, `entry_price` / `target_price` / `stop_price` (ABSOLUTE prices), thesis_summary.

## Rules
- Verdicts are HOLD / TRIM / SELL only - this skill reviews EXISTING positions; it does not open new ones (that's top-trades-today / cash-deployment).
- Never invent data - if a routed tool returns empty, say "data unavailable" and lower conviction.
- Sweep output under 500 words total; single-ticker under 300.
- Respect `get_profile` account types for tax framing; do not hardcode capital.

## Gotchas
- Earnings calendar is yfinance - sparse for TSX names. For .TO holdings with large weight, note "verify earnings date on IR page."
- `get_triggered_alerts` is read-only (does not re-evaluate). If the user wants fresh evaluation, mention `run_alerts_now` - only if asked (adds latency).
- A name can match two routing conditions (e.g. big weight AND earnings soon) - earnings proximity wins; fold the size concern into the reason.
- Don't run the adversarial pipeline for more than ~2 names in one sweep - token cost balloons and the nesting limit blocks parallelism.
- TRIM verdicts: the engine omits target/RR by design - show stop only, don't flag the null as an error.
