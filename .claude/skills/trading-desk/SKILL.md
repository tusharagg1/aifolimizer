---
name: trading-desk
description: End-to-end institutional trade pipeline with a hard Portfolio-Manager approve/reject gate. Use when the user wants a full desk workup before committing real money — "run the full process on X", "desk review for X", "take X through the whole pipeline", "should I actually buy X — do it properly". Chains research → risk gate → pre-trade discipline → trade ticket, and REFUSES to emit a ticket on reject.
---

# Trading Desk (Analyst → Research → Risk → PM Gate → Execution)

## Goal

One orchestrated pass that mirrors an institutional desk: independent analysis,
adversarial debate, portfolio-level risk check, behavioral discipline gate, and
a final Portfolio-Manager decision that GATES whether a trade ticket is emitted.
No single stage can wave a trade through. Output ends in APPROVE + ticket or
REJECT + reasons — never a ticket on reject.

## When to invoke

- User wants the complete process on a name before real capital
- A high-conviction idea needs a disciplined second look
- Before any position-sized entry (not a quick quote check)

## Pipeline (run in order; each stage can veto)

**Stage 0 — Mandate (REQUIRED):**
- `mcp__aifolimizer__get_profile` — capital, account types (never hardcode)
- `mcp__aifolimizer__get_ticker_decision_history` (`ticker=TICKER, max_decisions=5`) + `mcp__aifolimizer__get_ticker_reflection` (`symbol=TICKER, n=3`) + `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) — load prior decisions BEFORE the desk forms a view. If a prior decision exists and this run flips it, the PM must state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.
- Confirm ticker, direction (BUY/ADD/TRIM/SELL), horizon, rough size. If the
  user can't state a thesis, stop here (REJECT: no thesis).

**Stage 1 — Analyst + adversarial research:**
- Run the `adversarial-research` skill (bull / bear / consensus) on the ticker.
- Capture: fair-value estimate, key bull driver, key bear risk, base-rate.
- For US names, fold in `mcp__aifolimizer__get_dcf_valuation` as a quantitative
  fair-value anchor.

**Stage 2 — Risk gate (HARD veto):**
- `mcp__aifolimizer__get_risk_gate_state` — if BUYs are halted (drawdown / VIX /
  loss-streak), a BUY/ADD is auto-REJECTED regardless of conviction.
- `mcp__aifolimizer__get_positioning_signals` — crowding ≥70 on a BUY/ADD =
  veto (defer; negative expected alpha for late entries).
- `mcp__aifolimizer__get_concentration_warnings` — if this trade breaches
  single-name/sector limits, veto or force size-down.

**Stage 3 — Pre-trade discipline gate (HARD veto):**
- Run the `pre-trade-check` skill. If it returns REJECT, the desk REJECTS.

**Stage 4 — Portfolio-Manager decision:**
- Synthesize stages 1-3 into one explicit verdict:
  - **APPROVE** only if: thesis present, no risk-gate halt, crowding < 70 (or
    contrarian), pre-trade-check PASS, and reward:risk ≥ 1.5.
  - Otherwise **REJECT** with the specific failing gate(s).
- State conviction (HIGH/MED/LOW) — drives sizing in the ticket.

**Stage 5 — Execution (ONLY if APPROVE):**
- `mcp__aifolimizer__get_trade_ticket` with the action + conviction → entry
  zone, stop, exit ladder, sizing.
- `mcp__aifolimizer__log_recommendation` to forward-track the call.
- Optionally `mcp__aifolimizer__log_hypothesis` to register the thesis with
  acceptance/invalidation criteria.

## Output

```
DESK VERDICT: APPROVE | REJECT
Ticker | Direction | Conviction
Gate results: research ✓/✗ · risk ✓/✗ · crowding ✓/✗ · pre-trade ✓/✗ · R:R ✓/✗
[If APPROVE] Trade ticket: entry zone, stop, exit ladder, size, account
[If REJECT]  Failing gate(s) + what would have to change to revisit
```

## Rules

- A veto at ANY hard gate (Stage 2 risk halt, Stage 3 pre-trade REJECT)
  overrides high conviction. Do not emit a ticket.
- Never fabricate numbers — every figure traces to a tool call (see the
  data-grounding rule in stock-analysis).
- Tax note on the ticket: Non-Reg trims realize 50%-inclusion cap gains.
