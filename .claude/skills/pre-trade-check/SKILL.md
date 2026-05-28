---
name: pre-trade-check
description: Forced discipline gate before any discretionary buy or sell. Use BEFORE placing any trade — intraday, swing, or position. Triggers on "should I buy X?", "thinking about X", "going to enter X", "is now a good time for X?", "I want to buy X", "saw X on twitter", "X is ripping". Refuses approval when FOMO, sizing, or stop-discipline rules are violated.
---

# Pre-Trade Check (Behavioral Guardrail)

## Goal

Stop bad trades before they happen. This skill is a **filter, not a recommender**. Output is PASS or REJECT with reasons. Most retail losses come from emotional entries — this skill makes those entries explicit and refusable.

## When to invoke

Any time the user is considering a buy or sell. Especially:
- Symbol seen on TikTok / X / Reddit / news
- "Friend told me about X"
- After a big up day on a name
- After a big down day on a held name (panic-sell candidate)
- Any FOMO / regret language

## How to run

**Step 0 — Establish intent (REQUIRED before any tool call):**

If user has not stated, ask explicitly:
1. **Ticker?**
2. **Direction?** (BUY new / ADD existing / SELL / TRIM)
3. **Horizon?** (intraday / swing days-to-weeks / position 1-6mo / long-term)
4. **Dollar amount considered?**
5. **Why now?** (specific catalyst, technical setup, or "saw it on X")

If user can't answer #5 with a specific reason that isn't "saw it on social media" → output **REJECT: no thesis** and stop. Do not run other tools.

**Step 1 — Pull state (parallel):**
1. `mcp__aifolimizer__get_profile` — total capital, per-account cash
2. `mcp__aifolimizer__get_portfolio` — current holdings, weights, existing position in this ticker if any
3. `mcp__aifolimizer__get_technicals` with `symbols=[TICKER]`
4. `mcp__aifolimizer__get_fundamentals` with `symbols=[TICKER]`
5. `mcp__aifolimizer__get_positioning_signals` with `symbols=[TICKER]`
6. `mcp__aifolimizer__get_news_headlines` with `symbols=[TICKER]`, `limit=10`
7. `mcp__aifolimizer__get_live_track_record` (no args) — user's own win-rate over last 30d
8. `mcp__aifolimizer__get_ticker_decision_history` with `ticker=TICKER` — prior decisions on this name

**Step 2 — Run filter gates (in order, stop at first REJECT for fatal gates):**

### Fatal gates (any one fails → REJECT, stop)

| Gate | Rule | Fail action |
|---|---|---|
| **FOMO-rip filter** | Daily change today > +5%, or 3-day change > +10%, AND reason was "saw on X / TikTok / news" | REJECT. State price and require 48h cooldown. |
| **Crowding cap** | `crowding_score >= 75` for BUY/ADD | REJECT. State score and Goldman 2025 finding (consensus = negative expected alpha). |
| **Position size sanity** | Proposed $ > 5% of total portfolio for any single BUY/ADD | REJECT. State max allowed = 5% of total NAV. |
| **No stop plan** | User cannot answer "what price do I exit if wrong?" | REJECT. Compute ATR-based stop suggestion and require user confirm. |
| **Concentration violation** | Position post-trade would exceed 10% single-name or sector > 35% | REJECT. State current weight + post-trade weight. |
| **Stage 4 add** | Technical `stage == 4` AND direction is BUY/ADD | REJECT. State "downtrend + below 200SMA — do not add to losers without fundamental override". |
| **Recent stop-out re-entry** | `get_ticker_decision_history` shows stop hit in last 30 days AND no new thesis | REJECT. List prior stop date + price. Require explicit new bullish catalyst. |

### Warning gates (do NOT block, but list in output)

| Gate | Rule |
|---|---|
| RSI overbought | `rsi_14 > 70` — entry late, expect pullback |
| Below SMA50 | Price < SMA50 — counter-trend entry |
| Earnings within 5 days | Per `get_fundamentals.next_earnings` — binary event risk |
| Negative news headlines | >50% of recent headlines negative tone |
| Personal track record poor | User's 30d win rate < 40% — cooling-off recommendation |
| Wide spread / low volume | `volume_score < 0.5` — execution risk |
| Below-average ADX | `adx_14 < 20` — chop regime, swing setups weak |

**Step 3 — Sizing math (only if all fatal gates PASS):**

Compute and display:

1. **Risk per trade** = 1.5% of total portfolio NAV (default; ask user if different).
2. **Entry price** = current price OR `pivot_levels.s1` (pullback to support if RSI > 65).
3. **Stop price** = entry − (1.5 × `atr_14`). Floor at SMA50 if SMA50 closer.
4. **Risk per share** = entry − stop.
5. **Max shares** = floor(risk_per_trade / risk_per_share).
6. **Position $** = max_shares × entry.
7. **Position % of portfolio** = position_$ / total_NAV.
8. **Take-profit 1** = entry + 2 × (entry − stop). Sell 50% here.
9. **Take-profit 2** = trail stop at 1 × ATR below highest close after TP1.

If `position_%` > 5%, cap shares so position = 5% of NAV (override risk-based sizing on max-size rule).

**Step 4 — Output decision card:**

```
PRE-TRADE CHECK · <TICKER> · <DIRECTION> · <HORIZON>
================================================
Verdict: PASS / REJECT
Reason: <one line>

Fatal gates:  [✓] FOMO  [✓] Crowding  [✓] Sizing  [✓] Stop  [✓] Concentration  [✓] Stage  [✓] Re-entry
Warning gates: <list any that triggered>

If PASS, trade ticket:
  Entry:    $X.XX
  Stop:     $X.XX (−A.A% / −1.5 ATR)
  TP1:      $X.XX (+B.B% / +2R, sell 50%)
  TP2:      trail 1×ATR after TP1
  Shares:   N
  Cost:     $C,CCC
  Position: P.P% of $NAV total
  Max loss: $L (risk = R.R% of NAV)

Personal track record (30d):
  Win rate: X%
  Avg win:  $X    Avg loss: $X    R-multiple: X.XR
  Verdict:  <healthy / cooling-off recommended / suspend trading>

Next action: <log via log_recommendation if PASS, else nothing>
```

**Step 5 — On PASS, log the intent:**

Call `mcp__aifolimizer__log_recommendation` with:
- `skill="pre-trade-check"`
- `ticker=<TICKER>`
- `action=<BUY/SELL>`
- `conviction="filtered"` (this skill does not assess conviction — only rejects)
- `rationale=<user's thesis sentence>`
- `entry_price`, `stop_loss`, `target_price` from sizing math

This builds the forward track record for `weekly-mirror` skill.

## Investor profile

- Always pull capital + accounts from `get_profile` — never hardcode
- Risk budget per trade: 1.5% of total NAV (ask user to override)
- Max single position: 5% of total NAV
- Max sector: 35% of total NAV

## Rules

- Output ≤ 250 words including decision card
- Direct. "REJECT — crowding 82/100" not "you may want to reconsider given elevated positioning metrics"
- If user argues with a REJECT, do NOT flip. Restate the rule and ask them to wait 48h or change their thesis
- Never recommend a trade. This skill only **rejects** or **passes**. Picking is user's job
- ATR must be present — if `atr_14` is null, REJECT with "insufficient data for risk-based stop"
- For SELL/TRIM direction: skip crowding gate, skip stage-4 gate. Apply: FOMO-panic filter (down day > -7% AND reason is "panic"), concentration unwind (good), tax-lot awareness (warn if short-term gain in non-reg)

## Gotchas

- `get_live_track_record` returns empty for new users — handle null gracefully, do not block on missing history
- `crowding_score` null for TSX (.TO) tickers — fall back to neutral, do not REJECT on null
- `pivot_levels.s1` null for halted/new symbols — use current price as entry
- `atr_14` requires ≥21 bars — REJECT IPOs and recently-listed tickers
- User-provided dollar amount overrides risk-based sizing only DOWNWARD (smaller is always allowed). If user wants larger than risk-based size, REJECT with the math
- "Friend's tip" / "X said" — counts as no thesis. REJECT at Step 0
- After REJECT, do NOT auto-rerun if user retypes. Require explicit "override" + written justification
- This skill is the user asking themselves to be disciplined. Honor that — do not soften output to be "nicer"
