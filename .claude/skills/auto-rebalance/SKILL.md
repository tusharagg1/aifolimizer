---
name: auto-rebalance
description: Monthly rebalance + DCA prompt for the long-term core ETF sleeve only (broad-market index ETFs) — NOT single-stock adds (use cash-deployment) and NOT max-Sharpe reweighting (use optimize-allocation). Use when the user asks "should I rebalance?", "where should I deploy my paycheck?", "DCA recommendation", "is my allocation drifting?", "auto-invest plan", or on the 1st of each month. Computes target vs actual core allocation, suggests DCA amount per ETF, accounts for tax-account routing.
---

# Auto-Rebalance (Long-Term Core Maintenance)

## Goal

Keep the boring-core sleeve on target with minimum effort. This skill is for the **wealth-building bucket**, not the trading bucket. Output is a monthly DCA + rebalance instruction sheet the user can execute in 5 minutes.

Math behind it: rebalancing by adding new cash to underweighted positions (vs selling overweighted) avoids tax events and keeps drift small. Combined with biweekly/monthly DCA, this captures dollar-cost averaging benefits and removes timing decisions.

## When to invoke

- 1st of each month (can be scheduled via /loop)
- User asks "where do I put this paycheck?"
- User asks "is my allocation off?"
- Settled cash in TFSA/RRSP > $500 with no immediate trade plan
- After any contribution-room reset (TFSA Jan 1, RRSP March 1)

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) — portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable verdict: for each BUY/SELL/TRIM/ADD/HOLD issued, call `mcp__aifolimizer__log_recommendation` (`skill="auto-rebalance", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## How to run

**Step 1 - Pull state (parallel):**
1. `mcp__aifolimizer__get_profile` - per-account cash, contribution room if available, total NAV
2. `mcp__aifolimizer__get_portfolio` - current holdings per account
3. `mcp__aifolimizer__get_xray` - ETF exposure expansion (so VFV+XEQT overlap is detected)
4. `mcp__aifolimizer__get_concentration_warnings` - single-name or sector flags from xray
5. `mcp__aifolimizer__get_macro_snapshot` - current regime label (informational only; this skill does NOT time the market)
6. `mcp__aifolimizer__recall_preferences` with `query="rebalance core allocation"` - user's preferred target weights if previously set
7. `mcp__aifolimizer__get_personal_context` - ground TFSA/RRSP/Non-Reg routing in the actual account waterfall, contribution room, and horizon. If `present=false`, fall back to generic routing rules and suggest the user run profile-setup for personalized tax-account placement.

**Step 2 - Choose the core sleeve ON MERIT (REQUIRED first run):**

Do NOT default to specific tickers. The core fills *roles*; pick the best ETF for each role by comparing candidates, so every recommendation is earned, not assumed. If `recall_preferences` already holds confirmed targets, skip to Step 3.

a) Set the role mix from the user's risk/horizon (`get_personal_context`), and confirm it before picking any ticker:
   - Aggressive + long horizon → ~90-100% equity (one global all-equity fund ± a US / growth tilt) + small cash buffer
   - Balanced → equity + bond ballast + cash
   - Income tilt → dividend-equity + (covered-call or bond) + cash

b) For each role, build a candidate set — these are STARTING candidates, NOT the answer and NOT exhaustive; add any others you know of:
   - Global all-equity: XEQT, VEQT, XGRO, ZEQT, VGRO …
   - US large-cap: VFV, ZSP, XUS, VOO, VTI …
   - Bond ballast: ZAG, XBB, VAB …
   - Cash / T-bill: CASH.TO, CBIL, HISA …
   - Growth / tech tilt: QQQ, XQQ …

c) Score each candidate on objective criteria, then recommend the winner per role:
   - MER (WebSearch — lower wins; flag if unavailable)
   - Index coverage / diversification vs the role
   - Currency + withholding fit for the target account (CAD-listed vs US-domiciled in RRSP)
   - Overlap with existing holdings (`get_xray`) — don't stack the same exposure twice
   - Liquidity / AUM / tracking (volume from `get_technicals`; AUM via WebSearch)
   If XEQT / VFV win on these, recommend them — but only because they won, never by default. If a cheaper or better-fitting fund wins, recommend that instead.

d) If the user ALREADY holds core ETFs, evaluate keep-vs-switch: only switch when a candidate is clearly better net of switching cost + tax. Don't churn a working core just to chase a few bps of MER.

Output a "Core sleeve" recommendation table: Role | Recommended ETF | MER | why it beat the alternatives | runner-up. Confirm the picks + weights with the user, then save them via `mcp__aifolimizer__remember_preference` so future runs skip this step.

**Step 3 - Compute drift table:**

For each core ETF in target:
- Current weight (% of NAV) from `get_portfolio`
- Target weight from preferences
- Drift = current − target
- Dollar gap = drift × NAV
- Direction: ADD if negative drift, TRIM if positive drift > 5pp from target

**Step 4 - Match cash to deployment:**

For each account holding settled cash:
- TFSA cash → recommend US-listed XEQT or VFV equivalent (no US withholding on cap gains; dividends still face 15% if US-domiciled)
- RRSP cash → US-domiciled (VOO, VTI) preferred - no US dividend withholding tax in RRSP
- Non-Reg cash → Canadian dividend payers (Cdn Dividend Tax Credit) OR XEQT (cap gains tax efficient)
- USD cash in RRSP → keep as USD, deploy into US-listed ETF, avoid FX conversion

**Step 5 - Output rebalance + DCA card:**

```
AUTO-REBALANCE · <DATE> · NAV $X,XXX
====================================
Strategy: <user's confirmed allocation profile>

DRIFT TABLE:
| Ticker     | Current | Target | Drift  | $ Gap   | Action       |
|------------|---------|--------|--------|---------|--------------|
| XEQT.TO    | 52.1%   | 60%    | -7.9pp | -$1,580 | ADD $1,580   |
| VFV.TO     | 23.0%   | 20%    | +3.0pp | +$600   | hold (drift OK) |
| QQQ        | 8.5%    | 10%    | -1.5pp | -$300   | ADD $300     |
| CASH       | 16.4%   | 10%    | +6.4pp | +$1,280 | DEPLOY (this plan) |

DEPLOYMENT PLAN (per account):
TFSA  ($800 settled)   → BUY 32 sh XEQT.TO @ ~$25  ($800)
RRSP  ($1,200 settled) → BUY 8 sh VFV.TO @ ~$112  ($896) + 3 sh QQQ @ ~$100 ($300) [keep $4 idle]
NonR  ($0)             → no action this cycle

Result post-deploy:
- XEQT.TO  → 59.0% (close to target)
- VFV.TO   → 23.5% (slightly over - re-evaluate next month)
- QQQ      → 9.5%
- Cash     → 9.9% (on target)

CONTRIBUTION ROOM CHECK:
- TFSA 2026 room remaining: $X,XXX (from profile if available - else manual reminder)
- RRSP 2026 deduction limit: $X,XXX
- If room left, recommend full-room contribution next paycheck

NEXT REVIEW: <today + 30 days>
```

### 6. Honest reminders (always include this section)
- "This is the boring bucket. No FOMO names, no single-stock additions in this plan. Use cash-deployment skill for that."
- "Do not time this. Execute today even if market is at all-time-high - DCA into highs is mathematically fine over 10+ yr horizon."
- "If you skipped a month, double up. Missed DCAs compound into significant lag over decades."
- If macro regime is `bear_high_fear`: "Bear regime detected. Continue DCA - bear DCAs are statistically highest-EV. Do NOT pause auto-deposit."

## Investor profile

- Always pull capital from `get_profile` - never hardcode
- No fixed default allocation — run the Step 2 merit-based core selection UNTIL the user sets a preference, then honor the saved targets
- Currency = CAD aggregate; per-account if cash split

## Rules

- ≤ 350 words
- NEVER recommend selling a core holding to rebalance - always rebalance via new cash
- EXCEPTION: if a single position drifts > 15pp above target due to a big rally, recommend a TRIM (with tax-cost flag for non-registered accounts)
- Whole-share counts only (Wealthsimple supports fractional but a discrete plan is easier to execute)
- NEVER recommend timing the market based on macro regime - this skill is the anti-timing skill
- If user has no boring-core holdings (0% of NAV), this skill becomes onboarding: walk through opening positions in target ETFs, account-by-account
- Tax-loss-harvesting cash: superficial-loss rule blocks rebuying same ETF for 30 days - cross-check with tax-loss-review skill if cash came from a recent sell

## Gotchas

- `get_xray` may double-count: XEQT holds VFV's S&P 500 names. Don't sum naively - use xray's deduped underlying exposure if reporting "true US equity %"
- Wealthsimple Managed accounts are NOT user-controlled - exclude from drift table. Only show self-directed accounts
- USD cash in CAD-base account triggers FX conversion (~1.5% spread) - flag in plan
- TFSA contribution room: app doesn't always have live room from WS API. If unavailable, prompt user to check manually before contributing
- RRSP contribution room resets March 1 each year - flag if running this skill in Jan-Feb that user should defer large RRSP contributions until new room confirmed
- ETF distribution dates: if approaching ex-div date on a core ETF, distributions go to whoever holds before ex-div. Don't recommend BUYING just-before-ex-div in non-registered (creates tax inefficiency)
- DRIP enrollment: if Wealthsimple has DRIP enabled on a core ETF, dividends auto-reinvest - factor this when computing drift (dividends become invisible add)
- Crypto holdings: NOT part of boring-core. Don't include CADC/BTC in this drift table. Use separate plan
- "Cash buffer" target (10%) is intentional dry powder - do NOT recommend fully deploying it. Maintain minimum 5% cash for opportunistic buys
- If macro_snapshot regime is unavailable, proceed anyway - this skill is regime-agnostic by design
- "I'll just wait for a dip" is the most expensive sentence in retail investing. If user pushes back on deploying today, restate: missed 10 best days in last 20 years cuts S&P return in half (Hartford Funds 2024 study)
