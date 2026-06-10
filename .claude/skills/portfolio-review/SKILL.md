---
name: portfolio-review
description: Whole-portfolio periodic review that produces ONE unified holdings decision table (Buy/Hold/Trim/Sell/Avoid per name) plus watchlist ranking, a keep-what-works list, and a tax/date-aware execution sequence. Use for "full portfolio review", "go through all my holdings", "what should I do across everything", "monthly review", "rebalance my whole book". Long-term-first; not a single-name desk pass (use trading-desk for one ticker) and not the daily digest (use daily-briefing).
---

# Portfolio Review (whole-book decision table)

## Goal

One coherent, execution-ready plan across EVERY holding and watchlist name —
optimized for long-term after-tax wealth. The output is a single decision
table, not nine separate analyses. Periodic cadence (monthly/quarterly), not
daily. Bias toward FEW high-value actions: most holdings should end "Hold".

## When to invoke

- User wants the whole book reviewed at once, not one ticker
- A monthly/quarterly check-in across holdings + watchlist
- NOT for a single-name buy decision (that's `trading-desk`) or a morning
  digest (that's `daily-briefing`)

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) — portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable verdict: for each BUY/SELL/TRIM/ADD/HOLD issued, call `mcp__aifolimizer__log_recommendation` (`skill="portfolio-review", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## How to run (call in parallel where independent)

Stage 0 — context:
- `get_profile` (capital, accounts — never hardcode)
- `get_personal_context` (tax bracket, FHSA/TFSA/RRSP room, account waterfall)

Stage 1 — portfolio truth:
- `get_portfolio`, `get_concentration_warnings`, `get_xray`

Stage 2 — per-name signal (top holdings + watchlist):
- `get_fundamentals` (holdings + watchlist union)
- `get_positioning_signals` (crowding — gates ADD decisions)
- `get_tax_loss_candidates` (underwater names = harvest candidates)

Stage 3 — date awareness:
- `get_earnings_calendar` (symbols = holdings + watchlist) — next 14d flags
- `get_dividend_calendar` (symbols = holdings) — ex-div / pay timing for
  don't-sell-before-ex-div and tax-year placement

Stage 4 — watchlist:
- `get_watchlist` → fold into the fundamentals/crowding pulls above

## Decision logic (per holding)

Assign ONE action:
- **Sell**: thesis broken OR severe concentration with weak fundamentals OR
  better use of capital + tax-efficient to exit.
- **Trim**: single-name > 10% or sector > 35% (`get_concentration_warnings`),
  OR consensus-crowded (`crowding ≥ 70`) and extended.
- **Hold**: thesis intact, sizing fine — the DEFAULT. Most names land here.
- **Add (small)**: fundamentals strong AND not crowded (`crowding < 70`,
  ideally contrarian ≤ 30) AND room under concentration caps AND cash available.
- Conviction HIGH/MED/LOW drives any sizing.

## Keep-What-Works rule (anti-overtrading — REQUIRED section)

Before any Trim/Sell, list names that should be LEFT UNCHANGED even if they
look "boring", because the cost of acting outweighs the benefit:
- Large embedded gain in a taxable (Non-Reg) account → selling realizes 50%-
  inclusion cap gains now; holding defers it.
- Safe, growing dividend (low payout, positive FCF) → income compounding.
- Core index ETF (the user's chosen broad-market fund) doing its job → don't tinker with the base.
- Within a few days of ex-dividend → don't sell before ex-div without reason.
Overtrading is a known long-term return killer. The bar to touch a working
position is HIGH. State why each kept name is kept.

## Output structure

### 1. Executive summary (3-4 sentences)
What matters most this review. The 1-3 actions that actually move the needle.

### 2. Holdings decision table
One row per holding:
```
TICKER · weight% · ACTION · conviction · horizon · tax note · risk note · reason
```
Sort: actionable (Sell/Trim/Add) first, Holds grouped after.

### 3. Keep-what-works
The leave-alone list with per-name justification (see rule above).

### 4. Watchlist ranking
Each name → 🔥 High-conviction buy / 👀 Monitor / ❌ Avoid (hype/overvalued/weak),
with a one-line reason and ideal-entry note. Crowding-aware: a popular name
with `crowding ≥ 70` and no fundamental edge is ❌, not 🔥.

### 5. Tax + account plan
- Harvest candidates (`get_tax_loss_candidates`) and the Non-Reg cap-gains note
- Account placement: where new contributions go (FHSA→TFSA→RRSP→Non-Reg per
  `get_personal_context` waterfall)

### 6. Date plan
- Earnings within 14d (hold-through vs trim-before)
- Ex-dividend / pay dates worth timing around (`get_dividend_calendar`)

### 7. Execution sequence
Numbered, practical: do-first / wait-on / avoid. Tie each step to a table row.

## Rules

- Most holdings should be **Hold**. If the table is mostly Trim/Sell, re-check —
  that's an overtrading smell, not a plan.
- Crowding gates ADD (per CLAUDE.md): `crowding ≥ 70` → never default to Add.
- Every number traces to a tool call — no fabricated prices, yields, or dates.
- Long-term-first framing (user's stated goal). Don't surface intraday calls here.
- Tax note on every Sell/Trim: Non-Reg realizes 50%-inclusion cap gains; TFSA/RRSP do not.
- If `get_dividend_calendar` or earnings data is empty for a name (sparse for TSX),
  say so — don't invent dates.
- One-shot. For a single name needing the full gated workup, hand off to `trading-desk`.

## Gotchas

- `get_dividend_calendar` filters out past ex-dates by design — a held dividend
  payer with no row just has no *upcoming* ex-div in the window; not an error.
- Watchlist fundamentals share the `get_fundamentals` cache — cheap to include.
- Concentration is whole-book; a name split across TFSA + Non-Reg still counts once.
