---
name: cash-deployment
description: Plan how to deploy uninvested cash across existing portfolio holdings. Use when the user asks "where do I put my cash?", "I have $X to invest", "deploy my cash", "what should I buy with my settled funds?", or "add to my best names". Routes capital to existing tickers ranked by setup quality and avoids concentration pile-on. Fetches portfolio + technicals + fundamentals via aifolimizer MCP.
---

# Cash Deployment (Add-to-Winners with Discipline)

## How to run

1. Call `mcp__aifolimizer__get_profile` — per-account cash balances. Cash sits in specific accounts (TFSA, RRSP, Non-Reg) — you can't move cash across account types without contribution-room consequences
2. Confirm with user: which account(s) is the cash sitting in? If unstated, use the account(s) with the largest cash balance from `get_profile`
3. Call `mcp__aifolimizer__get_portfolio` with `account_id=<that account>` — current holdings, weights, cost basis
4. Call `mcp__aifolimizer__get_concentration_warnings` — already-overweight names to EXCLUDE from add candidates
5. Call `mcp__aifolimizer__get_fundamentals` on existing holdings — sort by valuation + analyst target upside
6. Call `mcp__aifolimizer__get_technicals` on existing holdings — sort by Minervini stage 2 + RSI not overbought + trend uptrend
7. Call `mcp__aifolimizer__get_positioning_signals` on existing holdings — crowding score per name. Used as a tie-breaker and to EXCLUDE consensus-crowded names from "add" candidates
8. Cross-reference: ideal add = (not concentration-flagged) AND (stage 2 uptrend) AND (RSI 40-65) AND (analyst upside >10%) AND (fundamentals not deteriorating) AND (crowding_score < 70)

## Investor profile

- Age: 32, Canadian investor
- Time horizons: short-term + long-term (10yr+)
- Account types and capital: always read from `get_profile` — never hardcode
- Strategy lens: confirm with user (defensive top-up / aggressive growth / dividend reinvest); default to "aggressive growth" if unstated

## Output structure

### 1. Cash summary (top)
- Account: which account, how much cash settled
- Strategy lens assumed
- Number of candidate positions reviewed

### 2. Disqualified buckets (one-liner each)
- **Concentration-flagged** (already overweight) — list tickers, current weight, threshold breached
- **Technically broken** (stage 3/4, downtrend) — list tickers, why excluded
- **Overbought** (RSI >70) — list tickers, wait-for-pullback prices
- **Fundamentally deteriorating** (analyst downgrades, margin compression) — list tickers, what changed
- **Consensus-crowded** (crowding_score ≥ 70) — list tickers, score, "edge already priced — defer add"

### 3. Eligible add candidates (markdown table)
Columns: Ticker | Current Weight | Setup Score /6 | Crowding | Entry Price | Trigger | Notes.
Setup Score rubric (1 point each):
- Stage 2 uptrend
- RSI 40-65 (room to run)
- Above SMA50
- Analyst target upside >10%
- Not flagged for concentration
- Crowding score < 70 (not consensus-crowded)

### 4. Deployment plan
- Top 3 add candidates by Setup Score
- Dollar allocation per ticker (sum to cash balance, round to whole shares)
- Total share count + estimated execution price
- Cash remaining after deployment (if any — keep for opportunistic adds)

### 5. Tax-account reasoning
- Confirm the deploy stays in the same account as the cash (no contribution-room hit)
- If user wants to deploy cash into a different account type, flag the contribution-room cost (TFSA 2026 limit, RRSP earned-income headroom)
- US-dividend stocks → prefer RRSP cash if available (no 15% withholding)
- High-growth no-dividend → TFSA cash preferred (gains tax-free)
- Canadian dividend payers → Non-Reg or TFSA (Cdn dividend tax credit applies in Non-Reg)

### 6. What's NOT in this plan
- New tickers outside the user's existing universe (use stock-screener — not built yet — or research individual names with stock-analysis)
- Bond / GIC / HISA cash parking (deploy ≠ park)
- Crypto allocations (user does not hold crypto in Wealthsimple per profile)

## Rules

- Under 700 words
- Always render the eligible-candidates table
- Never recommend adding to a concentration-flagged position
- Never recommend a stage 3 or stage 4 ticker even if user holds it — say "do not add" explicitly
- Whole-share counts only (Wealthsimple supports fractional but the plan should be a real trade ticket)
- Reference user's actual cash balance from `get_profile` — never hardcode

## Gotchas

- `get_profile` cash balance is per-account — DO NOT sum cash across accounts and recommend a single-account deploy. TFSA cash can't buy positions in RRSP without an in-kind transfer that resets contribution room
- `get_concentration_warnings` threshold defaults: single 10%, sector 35%. Adjust via tool params if user has different risk tolerance — but don't recommend adds above the active thresholds
- `get_technicals` cached 1h — entry prices stale on high-volatility days. State the timestamp
- "Add to winners" can morph into momentum-chasing — if all top candidates are extended (>20% above SMA50), recommend partial deploy + cash hold for pullback rather than full deploy
- Settled cash vs unsettled: Wealthsimple T+1 settlement on equity sales. If user just sold, the cash may not be available — note this in the plan
- USD cash in a CAD account triggers FX conversion at WS spread (~1.5%). If cash is USD and recommended buy is .TO, flag the FX cost
- "Aggressive growth" tilt amplifies single-name risk — cap any single add at 5% of total portfolio even if cash > 5%
- Don't double-count: if user has a recurring auto-deposit going to XEQT, mention that ongoing flow before recommending another XEQT lump add
- Tax-loss-harvested cash: if cash came from a tax-loss sale, the superficial-loss rule blocks rebuying the same security for 30 days — verify with tax-loss-review skill before recommending the same ticker back
- Crowding score is a tilt, not a veto for existing holdings — never recommend SELLING a held name just because it became consensus. Only use it to defer/reduce ADDS
- If ALL eligible candidates are consensus-crowded, recommend partial deploy (cap 50% of cash) + cash hold for pullback. Do not force a full deploy into crowded names just because the table renders empty
