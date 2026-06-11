---
name: profile-setup
description: Capture or update the user's personal-finance context (age, province, salary, monthly expenses, contribution room RRSP/TFSA/FHSA, FTHB horizon, ESPP details, IOUs, debts, insurance, external crypto). Use when the user says "set up my profile", "personalize advice", "tell aifolimizer about me", "update my context", "I have new information", or whenever a downstream skill returns generic advice because get_personal_context.present is false. All fields optional.
---

# Profile Setup - Personal-Finance Context Capture

## Purpose

Personalize every advice skill by storing one structured profile of life context (separate from Wealthsimple account data). Stored locally only at `~/.aifolimizer/personal_context.json` with mode 0o600, never committed, never sent to external LLMs in raw form.

## How to run

1. Call `mcp__aifolimizer__get_personal_context` to see what is already stored. Keep `context_hash` in mind - if the user updates anything, the hash flips and self-learning loops will know to re-weight.
2. Ask the user which mode they prefer:
   - **Guided**: walk through field groups conversationally. Recommended first time.
   - **Paste template**: user pastes a filled JSON object - tool validates and writes atomically.
3. Walk through groups (skip any group the user does not want to share - every field is optional):

### Group A - Identity (★★★)
- `age`
- `province` (e.g. ON, BC, AB)

### Group B - Income (★★★)
- `gross_salary_cad`
- `employer_match_pct` (RRSP match)
- `bonus_target_cad`

### Group C - Cashflow (★★★)
- `monthly_expenses_cad`
- `monthly_savings_target_cad`

### Group D - Goals (★★★)
- `fthb_yes` (first-time home buyer?)
- `home_horizon_years`
- `mortgage_planned`
- `dependents_count`
- `spouse_present`

### Group E - Tax shelter room (★★★)
- `room_rrsp_cad`, `room_tfsa_cad`, `room_fhsa_cad`

### Group F - Risk (★★)
- `risk_tier_per_account` - map per account-type, values ∈ `{conservative, moderate, aggressive}`

### Group G - ESPP (★★)
- `espp_employer_label` (anonymized - "EmployerA", never the real name)
- `espp_discount_pct`, `espp_cycle_months`, `espp_monthly_contrib_pct`, `espp_qualifying_hold_months`

### Group H - Liabilities (★★)
- `iou_receivable_cad` (sum only - never counterparty names)
- `debts` (list of `{type, balance_cad, rate_pct}` - `type` examples: student_loan, credit_card, line_of_credit)

### Group I - Banking (★)
- `institutions` (list of anonymized labels: "BankA", "BankB" - never real names like RBC/TD)
- `external_cash_cad` (non-WS chequing + HISA total)

### Group J - Insurance (★)
- `insurance_tier` ∈ `{basic, middle, comprehensive}`

### Group K - External crypto (★★)
- `external_crypto` (list of `{symbol, qty, cost_basis_cad, platform_label}` - `platform_label` is anonymized: "PlatformA")

4. After each group, call `mcp__aifolimizer__set_personal_context_field` for each provided field (one call per field - atomic + idempotent).
5. For paste-template mode: ask user to paste a JSON object matching `backend/personal_context.template.json`. Then call `mcp__aifolimizer__set_personal_context_bulk` once with the parsed payload.
6. After capture, call `get_personal_context` again and read back the `derived` block (`marginal_tax_rate_pct`, `emergency_fund_target_cad`, `account_waterfall`, `fhsa_priority_first`) so the user sees what skills will now infer.

## Output structure

1. Confirm what was set (field count, fields touched).
2. Show `derived` summary: marginal tax rate, emergency-fund target, account-funding order.
3. Print the new `context_hash` (16 hex chars) - used by track-record stratification.
4. List skills that will now produce more personalized output: cash-deployment, tax-loss-review, dividend-strategy, portfolio-health, risk-assessment, daily-briefing, pre-trade-check.
5. Reminder: re-run this skill any time a field changes (job change, dependents, FTHB completed, contribution room used).

## Rules

- Never store real names, emails, account numbers, employer names, bank names, counterparty names. Use anonymized labels. If user provides a real name, replace before calling `set_personal_context_field`.
- Every field is optional. Do NOT pressure the user to provide a value - accept silence or "skip" and move on.
- Validate values before submitting: province must be 2-letter Canadian code; risk tier ∈ `{conservative, moderate, aggressive}`; balances and rates ≥ 0.
- Do NOT echo dollar values back in summaries beyond order-of-magnitude (use bands like ">90k") if the user later asks for a sharable summary.
- If user says "wipe my profile", call `mcp__aifolimizer__clear_personal_context` and confirm.

## Gotchas

- Field validation happens server-side via Pydantic - if a `set_personal_context_field` call fails, surface the error to the user verbatim and re-prompt that field only (do not abort the whole flow).
- `risk_tier_per_account` expects a full dict, not a single key - collect all account-type tiers in one prompt before sending.
- `debts` and `external_crypto` are lists - sending an empty list `[]` is fine; do not send placeholder example items from the template.
- `personal_context_hash` is short (16 hex). It will change every time any of the canonical-keys fields is updated. That is expected and is what enables self-learning stratification.
- For the paste-template mode, the template ships with `_INSTRUCTIONS` and `_help` keys - strip those before validating.
