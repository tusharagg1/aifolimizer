---
name: cash-deployment
description: Plan how to deploy uninvested cash across existing portfolio holdings. Use when the user asks "where do I put my cash?", "I have $X to invest", "deploy my cash", "what should I buy with my settled funds?", or "add to my best names". Routes capital to existing tickers ranked by setup quality and avoids concentration pile-on. Fetches portfolio + technicals + fundamentals via aifolimizer MCP.
---

# Cash Deployment (Add-to-Winners with Discipline)

## How to run

**Step 0 - Memory (call in parallel with Step 1):**
- Call `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` - surface recent stop-outs or wins. If any top add candidates were recently stopped out, require new data to justify re-entry.
- Call `mcp__aifolimizer__recall_preferences` with `query="cash deployment add position"` - investor preferences on sizing and account allocation.

1. Call `mcp__aifolimizer__get_profile` - per-account cash balances. Cash sits in specific accounts (TFSA, RRSP, Non-Reg) - can't move cash across account types without contribution-room consequences
2. Confirm with user: which account(s) is cash in? If unstated, use account(s) with largest cash balance from `get_profile`
3. Call `mcp__aifolimizer__get_portfolio` with `account_id=<that account>` - current holdings, weights, cost basis
4. Call `mcp__aifolimizer__get_concentration_warnings` - already-overweight names to EXCLUDE from add candidates
5. Call `mcp__aifolimizer__get_fundamentals` on existing holdings - sort by valuation + analyst target upside
6. Call `mcp__aifolimizer__get_technicals` on existing holdings - sort by Minervini stage 2 + RSI not overbought + trend uptrend
7. Call `mcp__aifolimizer__get_positioning_signals` on existing holdings - crowding score per name. EXCLUDE consensus-crowded names from add candidates
8. For the top 3 candidates after filtering, call `mcp__aifolimizer__get_ticker_decision_history` (one call per candidate, parallel) - check for prior stop-outs or failed entries on these specific tickers
9. Cross-reference: ideal add = (not concentration-flagged) AND (stage 2 uptrend) AND (RSI 40-65) AND (analyst upside >10%) AND (fundamentals not deteriorating) AND (crowding_score < 70) AND (lottery_flag != true) AND (no recent stop-out without new thesis override)

## Investor profile

- Canadian retail investor
- Time horizons: short-term + long-term (10yr+)
- Account types and capital: always read from `get_profile` - never hardcode
- Strategy lens: confirm with user (defensive top-up / aggressive growth / dividend reinvest); default "aggressive growth" if unstated

## Output structure

### 1. Cash summary (top)
- Account: which account, how much cash settled
- Strategy lens assumed
- Number of candidate positions reviewed

### 2. Disqualified buckets (one-liner each)
- **Concentration-flagged** (already overweight) - list tickers, current weight, threshold breached
- **Technically broken** (stage 3/4, downtrend) - list tickers, why excluded
- **Overbought** (RSI >70) - list tickers, wait-for-pullback prices
- **Fundamentally deteriorating** (analyst downgrades, margin compression) - list tickers, what changed
- **Consensus-crowded** (crowding_score ≥ 70) - list tickers, score, "edge already priced - defer add"

### 3. Eligible add candidates (markdown table)
Columns: Ticker | Current Weight | Setup Score /7 | Technical Score | Crowding | Entry | Stop | Notes.
Setup Score rubric (1 point each):
- Stage 2 uptrend
- RSI 40-65 (room to run)
- Above SMA50
- Analyst target upside >10%
- Not flagged for concentration
- Crowding score < 70 (not consensus-crowded)
- Volume score ≥ 1.0 (above-average volume confirmation)
- No lottery/MAX flag (`lottery_flag != true`) - abnormal single-day spike = chase risk (Bali-Cakici-Whitelaw)

### 4. Deployment plan (risk-first)
For the **top 3 add candidates** (by Setup Score; tiebreak higher `technical_score`), call `mcp__aifolimizer__get_trade_ticket` with `ticker`, `action="ADD"`, `conviction=<from Setup Score: 7=HIGH, 5-6=MED, 3-4=LOW>`. This is the single source of truth for levels (same engine as pre-trade-check) — supersedes raw `pivot_levels`:
- `entry_zone` — `{timing: buy_now | wait_pullback, low, high, reference, support_basis}`. **If `wait_pullback`, do NOT deploy at market** — state the pullback band and route that cash to the next ranked `buy_now` candidate or hold it.
- `stop_loss_price`, `exit_ladder` (T1/T2/T3 scale-out), `position` block (avg cost / return / stop_below_cost since these are held names).

**Before sizing any position:**
1. Check `get_concentration_warnings` - exclude overweight names FIRST
2. Max position loss = (`entry_zone.reference` − `stop_loss_price`) × shares. Ensure no single add risks >2% of total portfolio
3. Entry = `entry_zone.reference` (only deploy when `timing == buy_now`)
4. Stop = `stop_loss_price`
5. Position size = min(risk-based shares, 5% of total portfolio in dollar terms)

- Dollar allocation per ticker (sum to cash balance, round to whole shares)
- Total share count + estimated execution price; render each name's `exit_ladder` as the profit-taking plan
- Cash remaining after deployment (if any - keep for opportunistic adds, or for `wait_pullback` names)
- Max loss per position stated explicitly

### 5. Tax-account reasoning
- Confirm deploy stays in same account as cash (no contribution-room hit)
- If user wants to deploy into different account type, flag contribution-room cost (TFSA 2026 limit, RRSP earned-income headroom)
- US-dividend stocks → prefer RRSP cash (no 15% withholding)
- High-growth no-dividend → TFSA cash preferred (gains tax-free)
- Canadian dividend payers → Non-Reg or TFSA (Cdn dividend tax credit in Non-Reg)

### 6. What's NOT in this plan
- New tickers outside user's existing universe (use stock-analysis for individual research)
- Bond / GIC / HISA cash parking (deploy ≠ park)
- Crypto allocations (user does not hold crypto in Wealthsimple per profile)

## Rules

- Under 700 words
- Always render eligible-candidates table (Setup Score /7, Technical Score, Entry, Stop columns required)
- Never recommend adding to concentration-flagged position
- Never recommend stage 3 or stage 4 ticker even if user holds it - say "do not add" explicitly
- Whole-share counts only (Wealthsimple supports fractional but plan should be real trade ticket)
- Reference user's actual cash balance from `get_profile` - never hardcode
- Risk-first: compute max loss per position BEFORE sizing. Never size first and check risk second
- After plan is finalized: call `mcp__aifolimizer__log_trade_decision` for each ticker in the deployment plan (parallel calls). action=BUY, conviction based on Setup Score (7=Strong Buy, 5-6=Buy, 3-4=Neutral), skill_used="cash-deployment"

## Gotchas

- `get_profile` cash balance is per-account - DO NOT sum cash across accounts for single-account deploy. TFSA cash can't buy RRSP positions without in-kind transfer that resets contribution room
- `get_concentration_warnings` threshold defaults: single 10%, sector 35%. Adjust via tool params if user has different risk tolerance - don't recommend adds above active thresholds
- `get_technicals` cached 1h - entry prices stale on high-volatility days. State timestamp
- "Add to winners" can morph into momentum-chasing - if all top candidates extended (>20% above SMA50), recommend partial deploy + cash hold for pullback rather than full deploy
- Settled cash vs unsettled: Wealthsimple T+1 settlement on equity sales. If user just sold, cash may not be available - note in plan
- USD cash in CAD account triggers FX conversion at WS spread (~1.5%). If cash is USD and buy is .TO, flag FX cost
- "Aggressive growth" tilt amplifies single-name risk - cap single add at 5% of total portfolio even if cash > 5%
- Don't double-count: if user has recurring auto-deposit to XEQT, mention that flow before recommending another XEQT lump add
- Tax-loss-harvested cash: superficial-loss rule blocks rebuying same security for 30 days - verify with tax-loss-review before recommending same ticker back
- Crowding score is tilt, not veto for existing holdings - never recommend SELLING held name because it became consensus. Only use to defer/reduce ADDS
- If ALL eligible candidates consensus-crowded, recommend partial deploy (cap 50% of cash) + cash hold for pullback. Do not force full deploy into crowded names
- `pivot_levels` null for very new/halted symbols - fall back to current price as entry, SMA50 as stop
- `volume_score` null for some TSX ETFs - skip volume criterion (score out of 6 for those tickers, note it)
- `technical_score` is a composite screening signal; always verify against individual sub-criteria before committing capital
- If `get_ticker_decision_history` shows a recent stop-hit on a candidate, require explicit new bullish evidence (earnings beat, technical breakout, crowding unwound) before recommending re-entry. State why this time is different in the Notes column.
- Cross-ticker lesson: if get_cross_ticker_lessons shows recent stop-outs in same sector as candidate, flag "sector risk - recent stop pattern" in Notes column
- `log_trade_decision` calls are not optional - they build the feedback loop that improves future cash deployment plans
