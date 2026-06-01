# Examples

Sample skill outputs and a redacted LLM-prompt sample so evaluators can judge
quality without needing Wealthsimple credentials or running the live backend.

## Purpose

These artifacts let a reviewer see what aifolimizer actually produces —
analysis depth, structure, citations, and how PII is stripped before any
prompt leaves the machine — without provisioning WS auth or waiting on
OTP/MFA. All numbers are synthetic.

## Files

- `daily-briefing.md` — sample `/daily-briefing` output (synthetic data, not a
  real portfolio).
- `stock-analysis.md` — sample `/stock-analysis NVDA` output.
- `cash-deployment.md` — sample `/cash-deployment` output.
- `scrubbed-prompt.txt` — exact bytes sent to a free-LLM fallback for
  `/portfolio-health`, post-`pii_filter`. Shows symbols, weights (% of NLV),
  returns %, and scores only — no dollar balances, account IDs, email, name,
  or WS tokens.

## Disclaimer

Numbers in these files are synthetic and chosen to illustrate skill output
shape. Nothing here is investment advice, a recommendation, or a forecast.
Do not trade off these examples.
