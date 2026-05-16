---
name: tax-loss-review
description: Canadian tax-loss harvesting review. Use when the user asks about tax-loss harvesting, "should I sell my losers?", capital losses, superficial loss rules, or year-end tax moves. Fetches loss candidates via aifolimizer MCP.
---

# Tax-Loss Harvesting Review (Canadian rules)

## How to run

1. Call `mcp__aifolimizer__get_profile` to identify which account holds each position (TFSA/RRSP losses are NOT deductible)
2. Call `mcp__aifolimizer__get_tax_loss_candidates` with threshold_pct=-5.0 (or stricter -10.0 for clearer picks)
3. For each candidate, check the account placement matters for tax

## Key Canadian rules to enforce

- **TFSA / RRSP / FHSA losses are NOT deductible** — only non-registered accounts qualify
- **Superficial loss rule (30 days)**: if you (or your spouse) buy back the same security or a "substantially identical" one within 30 days before or after the sale, the loss is denied
- **Capital losses** in non-registered accounts can offset capital gains (current year, carry back 3 years, carry forward indefinitely)
- **Substitute trades**: typically a different but similar ETF (e.g., sell VFV → buy XUS as a non-identical S&P 500 proxy) — though "substantially identical" is judgment

## Output structure

1. **Account-by-account breakdown** of which candidates are tax-loss-eligible (non-reg) vs not (TFSA/RRSP/FHSA)
2. **Ranked list** of eligible loss candidates by unrealized loss size
3. **For each top candidate**: ticker, unrealized loss $, %, suggested substitute ETF to maintain exposure
4. **Superficial loss warnings**: any candidate that conflicts with recent buys
5. **Total deductible loss** vs total realized gains YTD (ask user if unknown)
6. **Action plan**: sell list, reinvest list, calendar (avoid 30-day window)

## Rules

- Under 400 words
- Never recommend selling a position in TFSA/RRSP for tax reasons
- Always flag the 30-day superficial loss risk
- Suggest substitutes that avoid "substantially identical" designation

## Gotchas

- Superficial loss rule covers the user AND spouse/common-law partner AND any controlled corp — ask before assuming the buy-back window is clean.
- 30-day window is 30 calendar days BEFORE and AFTER the sale — both sides count.
- Same-class ETFs tracking the same index (e.g. VFV ↔ VOO) are likely "substantially identical" per CRA — recommend a different index proxy (e.g. VFV → XUS uses different index methodology, safer).
- TFSA losses are PERMANENT — contribution room is not restored. Mention this when discussing TFSA exits.
- USD-denominated cost basis must be converted at the transaction-date FX rate, not current — `get_tax_loss_candidates` may show a CAD-converted loss that misstates the actual ACB. Flag and recommend the user verify with their broker statement.
- Capital losses cannot offset interest / dividend income — only capital gains. Don't suggest harvesting to "offset T5 income".
