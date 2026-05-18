---
name: tax-loss-review
description: Canadian tax-loss harvesting review. Use when the user asks about tax-loss harvesting, "should I sell my losers?", capital losses, superficial loss rules, or year-end tax moves. Fetches loss candidates via aifolimizer MCP.
---

# Tax-Loss Harvesting Review (Canadian rules)

## How to run

1. Call `mcp__aifolimizer__get_profile` — identify which account holds each position (TFSA/RRSP losses NOT deductible)
2. Call `mcp__aifolimizer__get_tax_loss_candidates` with threshold_pct=-5.0 (or stricter -10.0 for clearer picks)
3. For each candidate, check account placement matters for tax

## Key Canadian rules to enforce

- **TFSA / RRSP / FHSA losses are NOT deductible** — only non-registered accounts qualify
- **Superficial loss rule (30 days)**: if you (or spouse) buy back same security or "substantially identical" one within 30 days before or after sale, loss is denied
- **Capital losses** in non-registered accounts offset capital gains (current year, carry back 3 years, carry forward indefinitely)
- **Substitute trades**: typically different but similar ETF (e.g., sell VFV → buy XUS as non-identical S&P 500 proxy) — though "substantially identical" is judgment

## Output structure

1. **Account-by-account breakdown** — tax-loss-eligible (non-reg) vs not (TFSA/RRSP/FHSA)
2. **Ranked list** of eligible loss candidates by unrealized loss size
3. **Per top candidate**: ticker, unrealized loss $, %, suggested substitute ETF
4. **Superficial loss warnings**: candidates conflicting with recent buys
5. **Total deductible loss** vs total realized gains YTD (ask user if unknown)
6. **Action plan**: sell list, reinvest list, calendar (avoid 30-day window)

## Rules

- Under 400 words
- Never recommend selling TFSA/RRSP position for tax reasons
- Always flag 30-day superficial loss risk
- Suggest substitutes avoiding "substantially identical" designation

## Gotchas

- Superficial loss rule covers user AND spouse/common-law partner AND any controlled corp — ask before assuming buy-back window is clean.
- 30-day window is 30 calendar days BEFORE and AFTER sale — both sides count.
- Same-class ETFs tracking same index (e.g. VFV ↔ VOO) likely "substantially identical" per CRA — recommend different index proxy (e.g. VFV → XUS uses different index methodology, safer).
- TFSA losses are PERMANENT — contribution room not restored. Mention when discussing TFSA exits.
- USD-denominated cost basis must be converted at transaction-date FX rate, not current — `get_tax_loss_candidates` may show CAD-converted loss that misstates actual ACB. Flag and recommend user verify with broker statement.
- Capital losses cannot offset interest/dividend income — only capital gains. Don't suggest harvesting to "offset T5 income".
