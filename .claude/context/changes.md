# aifolimizer - Change Log

Append-only. Most recent at top.

## 2026-06-11 - cleanup: news.py sink redaction + tiingo through fetch_json (post-eed3528 /simplify)
- news._fetch_chain: redaction moved from producer-only to the recording SINK. Both drains (cache.log_source_call + errors.append/_LOG.warning) now wrap str(e) in redact_secrets via a shared safe_err. U4a (eed3528) only redacted because the two keyed fetchers route through fetch_json; this hardens the layer that RECORDS, so any future news source doing its own httpx.get (the original leak pattern) can't leak through the unchanged raw str(e) sink.
- tiingo_src.get_history: converted hand-rolled httpx.get/raise_for_status/json block to base.fetch_json(name="tiingo", symbol=..., default=[]) - closes the last U1 reuse residual. Dropped httpx import. NOT a security fix (tiingo's key is in the Authorization header, never in httpx error strings) - reuse/consistency + defense-in-depth. Behavior preserved: error msg format `tiingo http {symbol}: ...` unchanged, `resp.json() or []` -> default=[] equivalent.
- Skipped (behavior change / out of diff): fetch_json `resp.json() or default` simplification (reverted - contract + test expect default-omitted->{}); telegram half-migration of discovery.py + signal_change_detector.py (outside diff, deferred U5); backtest_core->quant.py consolidation (cross-module).
- Verified: pytest 397 pass/3 skip, ruff clean, import+construct ok.

---

## 2026-06-11 - Signal PG-port review fixes: signal-loss gate, stale-bar guard, source marker

Adversarial review (multi-agent) of the uncommitted signal_history JSONL→Postgres port found 3 real defects; all fixed.

- **recommendations.py / scheduler.py — signal loss (must-fix).** The `if not postgres_dsn:` gate around `log_signal` dropped JSONL logging for EVERY `get_recommendations` caller when PG configured, but PG only receives per-holding rows from the scheduler. Discovery scans, watchlist, REST, and skill_runner signals reached neither store → lost from the forward-audit corpus. `get_recommendations` is sync + tenant-less, so PG-insert from there is wrong (needs tenant_hash). Fix: added `log_jsonl: bool = True` param; ad-hoc callers keep JSONL (their only store); scheduler passes `log_jsonl=not postgres_dsn` so holdings go to PG only when PG is live (no double-write, no loss when no PG).
- **signal_backfill.py — stale-bar corruption (should-fix).** Port dropped the legacy 365d age cap. Bars span only ~1y, so a signal older than the earliest bar anchored both entry and exit to `bars[0]` via `_close_at_offset`, writing a wrong realized_return to the PG source-of-truth table. Fix: `_earliest_bar_date()` helper + skip (`skipped_data`) any candidate whose `ts.date()` predates the earliest bar — guard tied to actual coverage, not a hardcoded number.
- **mcp_server.py — provenance ambiguity (nit).** `_pg_or_jsonl_analytics` fell through to JSONL on empty PG with no way to tell which store served. Fix: `result["_source"]` = `postgres` | `jsonl`. (NOTE: live only after MCP server restart — persistent process.)
- Preserved a concurrent fix that removed `close_pool()` from the helper (closing the shared global pool mid-flight nulled it for the running backfill).

Verified: ruff clean on 4 files; 10/10 signal_backfill+analytics tests; log_jsonl gate (False→0 / True→1 log calls); age guard skips a synthetic 400d-old row without writing; `_source` marker on both paths in-process; live MCP PG read still n=256 @ h5.

---

## 2026-06-10 - Data-source resilience: multi-source news + remove single-source yfinance gaps

Problem: `get_news_headlines` was single-source `yfinance.Ticker().news` — flaky/laggy/often empty, no fallback, in-process-only cache (3-4x Yahoo hits across MCP/cron processes). Two other paths reused the same broken scrape or had no fallback.

Fixes:
- **news.py** rewritten: asset-aware multi-source chain. US `finnhub→yfinance→eodhd`; CA `yfinance→finnhub→eodhd`; crypto `yfinance→finnhub`; intl `yfinance→eodhd`. First non-empty wins. Shared circuit breaker (`default_breaker`) + drift logging (`source_stats` as `news:<src>`) + cross-process L2 cache. EODHD always LAST (free tier 20/day shared with price quota). No new keys (finnhub/eodhd already configured). Normalized article adds `published_ts` epoch.
- **data_cache.py**: added `news` table + `get_news`/`put_news`/`delete_news`; wired into `clear_all` + `invalidate_symbol`.
- **positioning.py** `_news_velocity`: was the SAME flaky `yf.Ticker().news` scrape → now `news.recent_headlines()` (multi-source, timestamped). Yahoo outage no longer zeroes crowding headline-velocity.
- **technicals.py**: added `_router_history_df()` last-resort fallback when both Massive + yfinance batch fail for a symbol → routes through `data_router.get_history` (twelve_data/tiingo/eodhd/stooq) + shared L2. Gives TSX names a fallback they lacked; preserves the fast batch path for the happy case.

Untouched (no better free source / correct as-is): options chains (yfinance-only, 15m cache), market_data sector tags (yfinance-only, L2 cached), macro (FRED 12h — slow-moving), fundamentals/earnings/insider (long cache correct). Quote/history/fundamentals router already robust.

Verified: ruff clean; 340 passed / 3 skipped; live AAPL→finnhub today's headlines, SHOP.TO/BTC→yfinance, cache hit 0ms; velocity ratio computed from 50 timestamped articles; router-fallback df → rsi/sma/trend computed.

---

## 2026-06-09 - Verdict-drift fix: decision-memory wiring + JSONL-sourced calibration

Goal: stop verdicts contradicting prior sessions, and make `get_calibration_report` return real data instead of permanent `no_data`. Root cause of drift: only 3 of 27 skills loaded prior decisions before deciding, so each session re-derived from scratch. Root cause of calibration emptiness: the report read a Postgres column nothing populates.

### Changed
- **All 23 verdict-emitting skills** (`.claude/skills/*/SKILL.md`) now carry a decision-memory protocol: **load** `get_ticker_decision_history` + `get_ticker_reflection` (per-ticker) or `get_cross_ticker_lessons` (portfolio-level) BEFORE forming a view, with a reconcile-don't-silently-flip rule; **log** every actionable verdict via `log_recommendation` (and `log_trade_decision` where applicable) AFTER. Was 3 load / 14 log → 23/23. Template: `adversarial-research` Stage 0 + Layer 5. Non-investment utilities (profile-setup/health-check/perf-optimizer) excluded.
- **`backend/app/services/calibration.py`** — `_fetch_pairs` now reads the live JSONL source of truth (`signal_history._load_history`), pairing `features.win_prob` with `outcomes["h{H}"]["win"]`, instead of a Postgres `realized_return_*d` column that the scorer never fills. `compute()` (pure, test-covered) untouched.
- **`backend/mcp_server.py`** — `get_calibration_report` computes live via `calibration_verdict` instead of reading the never-populated Postgres `calibration_reports` table; honest "windows not elapsed" reason per horizon.
- **`.claude/context/lessons.md`** — added Skills/decision-memory rules (load-prior+log-after; HIGH conviction is anti-predictive — never size up on it).

### Verified
- `pytest tests/test_calibration.py` 10/10 pass (compute untouched).
- `get_calibration_report` now returns real Brier/ECE: h5 n=188 verdict=overconfident (Brier 0.29, ECE 0.227), h10 n=59 underconfident (ECE 0.158), h21 n=0 no_data (legitimate — 21-trading-day window closes ~Jun 18; oldest signals ~May 20). "overconfident" corroborates HIGH-conviction-anti-predictive (HIGH 22% win vs MED 63% over 411 scored recs).
- `grep` coverage: load tools 3→23 skills, log tools 14→23 skills (same set).
- Markdown-only skill edits; backend changes import-clean. Running MCP server should be restarted to serve new calibration code.

### Note
- Decision-of-record for this session's reconciled committee verdicts (16 names) logged via `log_recommendation` so future sessions load them and stop drifting.

---

## 2026-06-04 - Onboarding: one-command setup + doctor + POSIX automation

Goal: a second technical user clones and runs it without tribal knowledge. Closed the "setup needs a developer" gap. No app-logic change — packaging + docs + portability.

### New
- **`setup.sh` + `setup.ps1`** (repo root) — idempotent one-command bootstrap: create venv, install deps, seed `backend/.env`, generate `.mcp.json` with this-machine absolute paths, register MCP with Claude (if CLI present), run doctor. `.mcp.json` emitted via venv-python `json.dump` so Windows backslash paths escape correctly with zero hand-editing. setup.sh resolves venv python at `bin/python` (POSIX) **or** `Scripts/python.exe` (Git-Bash on native Windows).
- **`scripts/run-claude-skill.sh`** — POSIX twin of `run-claude-skill.ps1` (claude → free-LLM fallback → Telegram; same exit codes 0/1/2).
- **`scripts/posix/`** — ready-to-edit launchd plist + systemd `.service`/`.timer` + README (`__REPO__`/`__HOME__` placeholders, `sed`-install one-liners).
- **`.gitattributes`** — force LF on `*.sh`/`*.service`/`*.timer`/`*.plist` (CRLF breaks shebang + systemd on POSIX); CRLF on `*.ps1`. Exec bit (`100755`) set on both `.sh`.

### Changed
- **`backend/scripts/health_check.py`** — doctor +2 checks: `env_file` (backend/.env present, WS_EMAIL not placeholder) and `mcp_registered` (`claude mcp list` contains aifolimizer). Fixed crash: `claude mcp list` emits non-cp1252 bytes → forced `encoding="utf-8", errors="replace"`, default `""`.
- **`README.md`** — one-command fast-path at top of Quick start (scripts were undiscoverable otherwise); layout + docs refs.
- **`docs/SETUP.md`**, **`scripts/AUTOMATION.md`** — pointed at the shipped scripts; removed stale "no POSIX twin yet" claim.

### Verified
- Full fresh-tree e2e (copied working tree, fake-`claude` shim so real MCP config untouched): `setup.sh` exit 0 → venv (Scripts/ branch) + full pip install clean + `.env` + `.mcp.json` (escaped paths) + doctor 8/8 PASS. Real global MCP registration confirmed intact (`✓ Connected`). `setup.ps1` parse-checked (not executed — would mutate live config). `bash -n` + ruff clean.

### Dropped (not worth it)
- **Tiering 102 tools behind `AIFOLIMIZER_ADVANCED` flag** — cosmetic gain; touches import-critical `mcp_server.py` (102 `@mcp.tool()` decorators), depends on a FastMCP `remove_tool` API that may not exist in the bundled server, and fights the `check_doc_counts.py` CI guard. Risk of MCP-dead-for-everyone outweighs "fewer tools shown."

---

## 2026-06-04 - Audit batch 2 (11 builds: rigor + skills + global wiring)

6 new MCP tools (DCF, backtest CI, sentinel, 3× hypothesis registry). Doc counts synced 84→98 tools, 22→25 skills (concurrent data-integration work added the rest).

### New services + tools (`backend/`)
- **`dcf.py`** → `get_dcf_valuation` — deterministic 5y FCF + Gordon terminal DCF, CAPM discount, sensitivity grid. Anchored to SEC EDGAR FCF (refactored `fundamentals._fetch_facts` + new `get_sec_cashflow`). US-only, net-debt-ignored, negative-FCF guard. Gives price targets a quantitative spine.
- **`backtest_stats.py`** → `get_backtest_confidence` (moving-block bootstrap CI on total-return/CAGR/maxDD + order-shuffle MC drawdown-risk) and `run_lookahead_sentinel` (perfect-foresight signal must NOT earn abnormal return → leak detector). Reuse backtest engine `_fetch_close`/`_run_signal`/`_run_buy_hold`.
- **`hypotheses.py`** → `log_hypothesis`/`list_hypotheses`/`resolve_hypothesis` — durable thesis registry (open→confirmed/refuted/expired) at `~/.aifolimizer/hypotheses.jsonl`. Covers un-executed/in-flight ideas (decision_memory only covers executed trades).

### Skills
- **`trading-desk`** (project) — chained meta-skill: adversarial-research → risk gate → crowding/concentration → pre-trade-check → PM approve/reject gate → ticket. Hard veto blocks ticket emission.
- **`optimize-allocation`** (project) — already logged batch 1; max-Sharpe reweighting.
- **`health-check`** (project) + `backend/scripts/health_check.py` — diagnostic: mcp import + tool count, core-service imports, WS token freshness, settings hooks. PASS/WARN/FAIL.
- **`premortem`** (global `~/.claude/skills/`) — pre-action failure-mode gate for irreversible trades/code; blocks on HIGH-severity unmitigated.
- Data-grounding anti-hallucination contract added to `stock-analysis` + `adversarial-research` (cite only fetched numbers).

### Global config (`~/.claude/`)
- `CLAUDE.md`: Context-Budget golden rules (70% precision threshold, new-task=new-session, delegate fan-out, lean memory files).
- `scripts/lint_edited.py` (PostToolUse ruff-on-edit) + `scripts/precompact_snapshot.py` (PreCompact recovery dump) — scripts written + tested; **hook registration pending user approval** (auto-mode classifier blocked settings.json self-edit).

### Notes
- Skipped (not worth it): alpha-factor library (high effort/cross-sectional infra), devcontainer (modest solo value), mobile Cloudflare tunnel (infra + outward exposure), cost/cache dashboard (no per-token bill on Pro), CLAUDE.md table-extraction split (CI-fragile vs check_doc_counts for modest gain).
- `health_check.py` confirms venv is Python 3.14 and runs clean — the CLAUDE.md `<3.14` pin note is stale (project uses `ta`, not pandas-ta).

---

## 2026-06-04 - Wire new free-data tools into 9 skills

Surfaced the 11 session-added tools inside the skills that consume them (research skills stay PII-free — public data only). No new tools; instruction-only edits to `.claude/skills/*/SKILL.md`.

- **macro-impact**: + `get_boc_snapshot`, `get_statcan_snapshot` (official CA, prefer over FRED mirror), `get_factor_snapshot` (factor leadership feeds sector call). Gotchas added.
- **risk-assessment**: + `get_factor_exposure` (top 3-5 holdings) + `get_factor_snapshot` → new output item "Factor concentration" (shared loadings = hidden factor bet). US-factor/low-R² caveat.
- **stock-analysis**: + `get_insider_sentiment` (feeds insider item), `get_finnhub_news` (sentiment cross-check), `get_recent_filings` (8-K event risk), `get_factor_exposure` (lens selection). US-only caveats.
- **adversarial-research**: Layer 1 8→12 calls (+insider/news/filings/search_interest); Consensus agent now consumes search-surge + insider tells; diagram + parallel-count rule updated.
- **daily-briefing**: + `get_boc_snapshot` (cheap, curve_signal) always; `get_crypto_fear_greed`+`get_crypto_macro` only if crypto held (token-budget respected). Risk-radar lines added.
- **sector-rotation**: + `get_factor_snapshot` (value/growth/quality leadership → sector tilt).
- **earnings-analyzer**: + `get_recent_filings(8-K)` pre-earnings events + `get_finnhub_news` positioning-into-print.
- **earnings-postmortem**: + `get_recent_filings(8-K,10-Q,10-K)` — primary-source filing + EDGAR link.
- **momentum-scanner**: + `get_factor_snapshot` (Mom regime gate) + `get_search_interest` (retail-demand confirm); parallel step range updated.

---

## 2026-06-04 - 3 more free integrations (factor/crypto-macro/filings) + SEC CIK fix

4 new MCP tools (total 98→102). All public data, no key, no PII. Congressional-trades skipped (free sources need keys / PDF-scraping; 45-day disclosure lag = arbed; novelty > edge for retail).

### New services (`backend/app/services/`)
- **`fama_french.py`** → `get_factor_snapshot` (latest + trailing 21d/252d FF5+Mom returns) and `get_factor_exposure(ticker, lookback_days)` — OLS regress ticker excess returns on Mkt-RF/SMB/HML/RMW/CMA/Mom → factor betas + annualized alpha + R² (numpy lstsq, no statsmodels). yfinance prices + Ken French Data Library zips (24h cache). Verified: AAPL market β1.22, profitability +0.55, alpha 3.45%/yr, R²0.34. Upgrades risk-assessment beyond single beta. US factors → non-US directional only.
- **`defillama.py`** → `get_crypto_macro` (no key). Total DeFi TVL + top chains, aggregate stablecoin mcap + top issuers ($B). 30m cache. Verified live (TVL $73.9B, stablecoins $316.6B, top chain Ethereum $38.5B).
- **`edgar_filings.py`** → `get_recent_filings(ticker, forms, limit)` (no key). Material SEC filings (8-K/10-K/10-Q/6-K/20-F/S-1/proxy/13D-G) with dates + doc URLs; event-detection feed. Reuses `fundamentals._load_cik_map`. US-listed only. 6h cache. Verified live (AAPL + SHOP).

### Fix (pre-existing bug, blocked EDGAR + DCF + sec_financials)
- `fundamentals._load_cik_map`: `www.sec.gov/files/company_tickers.json` 403'd urllib (Akamai WAF rejects urllib fingerprint; `data.sec.gov` used by `_fetch_facts` is permissive). Switched that one fetch to `httpx` (same UA → 200). Added `import httpx`. numpy added to requirements (explicit; used in fama_french).

### Deps + wiring
- `requirements.txt`: `numpy>=1.26.0`. `mcp_server.py`: 3 lazy modules + 4 `@mcp.tool()` wrappers (after get_search_interest).
- Verified: 102 tools register, all 3 services exercised live.

---

## 2026-06-04 - 5 free data integrations (no/low-cost, fill macro+sentiment gaps)

7 new MCP tools (total 84→98). All public data — no PII, no `pii_filter`. Each = own service module mirroring `geopolitical.py` (dict cache + TTL, httpx, graceful degrade).

### New services (`backend/app/services/`)
- **`boc_valet.py`** → `get_boc_snapshot` (no key). Bank of Canada Valet: policy/overnight target rate (`V39079`), USD/CAD (`FXUSDCAD`), GoC 2/5/10y yields (`BD.CDN.{2,5,10}YR.DQ.YLD`), 10y-2y curve slope. Fills Canadian gap vs US-centric FRED. 12h cache. Verified live (rate 2.25%).
- **`crypto_sentiment.py`** → `get_crypto_fear_greed` (no key). alternative.me Fear & Greed 0-100 + 7d/30d avg + history. Pairs with `get_crypto_data`. 1h cache. Verified live (12 = Extreme Fear).
- **`statcan.py`** → `get_statcan_snapshot` (no key). StatCan WDS: CPI all-items + computed YoY inflation (vector 41690973), unemployment rate (2062815). 12h cache. Verified live (CPI YoY 2.82%, unemployment 6.9%). StatCan WAF (Akamai) resets plain-Python TLS → fetch via `curl_cffi` `impersonate="chrome"` (lazy import, graceful degrade). New dep `curl_cffi>=0.7.0`.
- **`finnhub_extras.py`** → `get_finnhub_news` (company-news + bull/bear headline tally), `get_insider_sentiment` (MSPR trend), `get_economic_calendar` (PREMIUM → degrades to `{"error":"premium_endpoint"}` on 401/403). Reuses existing `FINNHUB_KEY`. 30m cache.
- **`google_trends.py`** → `get_search_interest` (no key, `pytrends` lazy-imported). Search-interest 0-100 + 4w change as retail-demand/crowding proxy. Rate-limited (429) → graceful degrade. 6h cache. New dep `pytrends>=4.9.2`.

### Wiring
- `mcp_server.py`: 5 lazy modules + 7 `@mcp.tool()` wrappers (after geopolitical, Macro section).
- `requirements.txt`: `pytrends>=4.9.2`. `.env.example`: noted no-key sources + Finnhub-unlocks-news comment.
- Verified: all 5 modules import, all 7 tools register (`mcp.list_tools()`=98), 3 no-key tools exercised live.

---

## 2026-06-04 - Feature-gap audit follow-up (4 builds from 19-repo audit)

### `mcp_server.py` + `portfolio_optimizer.py`
- **`optimize_portfolio` MCP tool** wired — exposes existing `portfolio_optimizer.optimize()` (PyPortfolioOpt max-Sharpe, Ledoit-Wolf cov, BL views from analyst targets) that was dead code (built, never registered). Params: account_id, top_n=20, use_analyst_views, risk_free_rate. Returns optimal weights + add/trim changes. % only, no $.
- New skill `optimize-allocation/SKILL.md` — trading-bucket reweighting (distinct from DCA-only auto-rebalance); calls tool + crowding/concentration sanity gate.

### `shadow_account.py` — behavioral-bias diagnosis (from Vibe-Trading)
- `_detect_biases(roundtrips)` on existing FIFO data: disposition effect (loser/winner hold ratio), gain/loss asymmetry (payoff ratio), overtrading (cadence + median hold), anchoring (entries near round numbers). Each flagged+evidence; `biases_flagged` list. Surfaced in `analyze_shadow_account` result.

### `.claude/agents/{analyst,researcher}.md` — fixed dead subagent files
- Added YAML frontmatter (name/description/tools/model) — previously plain-prose, harness couldn't dispatch. Least-privilege tools (researcher read-only, analyst gets MCP analysis tools); model: opus/sonnet routing now enforced.

### GLOBAL `~/.claude/` — security hooks (from awesome-toolkit + ultimate-guide)
- `scripts/guard_secrets.py` + PreToolUse hook: deny AI read/edit of .env/ws_session.json/keys + shell cat of secrets. Fail-open, allowlists .env.example/.sample/.md. Runtime enforcement of CLAUDE.md privacy rules.
- `scripts/scan_injection.py` + PostToolUse hook (matcher mcp__aifolimizer__.*): non-blocking prompt-injection warning on news/sentiment/positioning tool output (untrusted third-party text).

## 2026-06-04 - Trade ticket: entry zone + tiered exit ladder

### `trade_ticket.py`
- **`entry_zone`** (BUY/ADD): support-anchored buy band. `timing=buy_now` when price near nearest support (SMA20/Bollinger/SMA50); flips to `wait_pullback` when stretched (>2x ATR above support OR RSI ≥ 70). Returns low/high/reference/support_level/support_basis/note.
- **`exit_ladder`** (BUY/ADD): tiered T1/T2/T3 by conviction R-multiples (HIGH 2/4/6R, MED 1.5/3/4.5R, LOW 1/2/3R), scale-out 40/35/25% summing to full qty. T1 auto-anchors to nearest resistance (bb_upper/SMA) when it lands within range. Crypto → fractional shares.
- **`position`** block when held (`avg_cost>0`): avg_cost, return_pct, stop_below_cost, plus `gain_from_cost_pct` per ladder rung.
- **`action="HOLD"`**: management plan for a held name — stop + exit_ladder (profit-taking from current price) + position block, no entry zone, no sizing. order_type=`MANAGE`. Ladder sized against `position_quantity`.
- Hoisted technicals fetch (reused by stop + zone + ladder). New helpers `_levels_below/_above`, `_atr_abs`, `_build_entry_zone`, `_build_exit_ladder`. All prior keys preserved (non-breaking).

### `mcp_server.py`
- `get_trade_ticket` now passes held-position `avg_cost` (book_cost/qty), `holding_return_pct`, `position_quantity` from live session; docstring documents HOLD + entry_zone/exit_ladder/position.

### Skill wiring
- **pre-trade-check**: Step 3 now sources levels from `get_trade_ticket` (entry_zone/stop/exit_ladder) — single source of truth, removed hand-rolled TP1/TP2 math; keeps 1.5%-NAV risk-based share sizing as the gate's discipline. Decision card shows buy zone + 3-tier ladder + held context.
- **position-review**: after verdict, calls `get_trade_ticket(action=verdict)` — HOLD renders exit ladder + stop + stop_below_cost; TRIM/SELL shows stop only.
- **cash-deployment**: §4 deployment plan routes the top-3 committed adds through `get_trade_ticket(action=ADD)` — replaces raw `pivot_levels.s1/s2` entry/stop with engine entry_zone (defers `wait_pullback` names) + stop + exit_ladder. §3 screening table unchanged. Risk-first 2%-max-loss / 5%-cap sizing kept.
- **top-trades-today**: deliberately NOT wired — lean 5-name one-shot; `get_trade_ideas` already supplies entry/stop/target/R:R and filters wait_pullback. A 3-tier ladder per idea would bloat the Telegram output and add 5 calls for no decision value at shortlist altitude.

---

## 2026-06-01 - Audit fixes: quant correctness, security, perf, self-improvement loop

### P0 quant correctness
- **BS put theta sign** (`options.py`): split call/put branches; put now adds `r*K*disc*N(-d2)` (was using call formula → wrong sign on puts).
- **`alpha_attribution._annualize`**: takes Series, computes years from calendar-day index span (was using bar count → +45% inflation on 1y horizon).
- **`shadow_account._fifo_pair`**: per-lot `remaining_qty`, consumes `min(buy_rem, sell_rem)` per slice (prior pairing leaked qty across roundtrips).
- **`signal_history._classify_subset`**: dropped redundant precision/recall/f1 (collapsed to `win_rate` by construction - were tautological).
- **`mcp_server.score_signal_horizons`**: defaults to `_DEFAULT_HORIZONS` `(1,3,5,10,21,42,63)`.

### P0 security
- **CVE-2025-69872** (diskcache CVSS 9.8): `cache_layer.py` forces `JSONDisk` + dir mode 0700.
- `/ws/portfolio/debug-pnl` now gated behind `WS_DEBUG=1`.
- `main.py` CORS: dead `https://*.vercel.app` literal entry replaced with `allow_origin_regex`.

### P1 perf
- `wealthsimple._finalize_session`: `ThreadPoolExecutor` for per-account balance+pnl, FX hoisted out of loop.
- `market_data.enrich`: parallel `_ticker_meta` warmup.
- `skill_backtest._prefetch_universe`: one batched `yf.download` seeds `_BATCH_BARS_CACHE`.
- `technicals_mtf`: one batched `yf.download` per timeframe.
- `recommendations`: pool size `min(16, 3+len(symbols))`.
- `data_router`: cap fallback cascade at 16 symbols.
- `circuit_breaker`: failure threshold 4 → 6.
- New `http_helpers.request_with_retry_after`; wired into `send_telegram`.

### P1 self-improvement loop
- Scheduler nightly now invokes `signal_history.score_horizons` + `decision_memory.resolve_outcomes`.
- `skill_llm_runner.run_adversarial` injects `decision_memory.get_ticker_history` + `get_cross_ticker_lessons`.
- `weights_tuner` consults calibration verdict; overconfident → bump 1.02 / cut 0.92.
- Calibration loop now scores 5 / 21 / 63 day horizons.
- New modules: `backtest_gate` (DSR<0.5 mute), `adaptive_regime` (per-(skill,regime) multipliers), `skill_health` (live hit-rate/PF gate), `threshold_tuner` (nightly buy/sell threshold), `source_drift` (data-router rerank), `shadow_recs` (champion-challenger scaffold).
- `decision_memory.get_open_decisions` helper added.

### P1 fault tolerance
- `.cache/scheduler_state.json` persists `last_score_date` / `last_sentry_ts`.
- `start_scheduler` hydrates state and fires `catch_up_missed_runs` - forces immediate score on wakeup/restart if any weekday's post-close window was missed.

### P1 docs
- README: 25-word elevator on line 1; reconciled adapter count (was 13, filesystem has 12); listed all 21 skills incl. 5 scheduler-driven.
- TRACK_RECORD: replaced `<CITE_URL_HERE>` with real Wealthsimple Performance Disclosure URL.
- `check_doc_counts.py`: extended with `_ADAPTER_PATTERNS` + `count_adapters` guard.

### P1 CI / ops
- `ci.yml`: top-level `permissions: contents: read`; `ruff format --check`; removed pyright `continue-on-error`; dropped pytest skip-guard; placeholder-string guard; pin-SHA TODO comment for gitleaks.
- `dependabot.yml`: weekly cadence, grouped minor/patch, conventional-commit prefixes.
- New `.github/CODEOWNERS` pinning sensitive files.
- `.gitignore`: `.pytest_cache` + `.ruff_cache` at root.
- `SECURITY.md`: GitHub PVR link replaces placeholder email; CVE-2025-69872 advisory section.
- Lockfile tooling: `backend/scripts/lock_deps.{sh,ps1}`.
- Renamed `backend/run_mcp_tools.py` → `backend/scripts/smoke_mcp_tools.py`.

### Tests
- 13 new unit tests: `test_options_theta`, `test_shadow_account_fifo`, `test_signal_history_horizons`, `test_decision_memory_open`, `test_http_helpers_retry_after`.
- `test_pii_filter` fixture email scrubbed.
- `test_mcp_pii_integration`: `asyncio.run` replaces deprecated `get_event_loop()`.
- Final: **303 passed, 5 skipped, 0 failed.**

### Commits (7, pushed to origin/master)
- `0926bc0` fix(quant): correct BS put theta sign + annualize by calendar days + qty-aware FIFO
- `8cd0891` sec: mitigate diskcache CVE-2025-69872 + gate /portfolio/debug-pnl + tighten CORS
- `7615120` perf: batch + parallelize hottest paths; cap fallback cascades; honor Retry-After
- `0fe5237` feat(self-improving): close the learning loop end-to-end + fault-tolerant catch-up
- `fe362ef` test: add new-feature unit tests + un-skip MCP PII tests on Python 3.12+
- `aea36c2` docs(readme): tighter elevator + 21-skill listing + fix adapter-count drift
- `bddc8b7` chore(ci+ops): pin perms, weekly dependabot, CODEOWNERS, lockfile tooling, hygiene

---

## 2026-05-29 - Event-driven MFA: heads-up + local popup, no polling

### Why
8h WS TTL force re-auth, defeat Claude-primary preference for skill automation. User want (a) one Telegram heads-up moment session dies, (b) one-click launcher open local popup for code entry, (c) no polling watchdog burn resources or duplicate notifications.

### Built
- `backend/scripts/mfa_notify.py` - Telegram heads-up only (no reply loop). 6h cooldown prevent repeat-spam while user away.
- `backend/scripts/mfa_popup.py` - Tk simpledialog OTP entry + WS login + session persist. Clears notify cooldown on success so next real expiry triggers fresh heads-up. Exit codes: 0 ok, 1 config/login error, 2 cancelled, 3 rejected, 4 creds missing.
- `scripts/aifolimizer-launch.ps1` - user-facing launcher. Probes backend + session; spawns popup if expired; reports ready. Pin desktop shortcut.
- `backend/main.py` lifespan: when `restore_session()` returns None (file missing / stale / WS rejected), spawns `mfa_notify.py` in background via `subprocess.Popen`. Single trigger per backend startup. No polling Scheduled Task - purely event-driven.

### Flow
1. Backend starts → restore fails → fires `mfa_notify.py` once → Telegram heads-up arrives.
2. User runs `aifolimizer-launch.ps1` → probes session → spawns Tk popup → user types 6-digit code → WS login → session persisted → confirmation popup → launcher exits 0.
3. Skills run Claude-primary next 8h. Free-LLM fallback only on Claude CLI failure, never on session expiry.

### Verified
ruff clean; backend restarted; `~/.aifolimizer/.mfa-notify.last` stamp written → Telegram dispatched on restart.

### Resource / privacy
No standing process. mfa_notify spawn one-shot (~200ms python + single Telegram POST). No PII in message - just "session expired". Reads same `.env` secrets rest of backend already needs.

### Install
```
# nothing to register - backend lifespan IS the trigger
```
Pin desktop shortcut to `scripts/aifolimizer-launch.ps1`.

### Prereqs
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `WS_EMAIL`, `WS_PASSWORD` in `backend/.env`.

---

## 2026-05-29 - MCP cold-start fix + project .mcp.json

### Why
Skills failed run via Claude Code: aifolimizer MCP tools absent from session schema despite `claude mcp list` showing ✓ Connected. Root cause: top-level eager import of 30+ service modules in `mcp_server.py` (~5s cold start) blew past Claude Code MCP handshake window. Skills fell back to manual REST, which also failed because WS session expired.

### Fix
- **Lazy service imports** (`backend/mcp_server.py`): replaced eager `from app.services import (...)` block with `_LazyModule` proxy class. Module-level service refs (`market_data`, `fundamentals_svc`, etc.) defer `importlib.import_module` until first attribute access. Tool bodies unchanged - still call `market_data.foo()` etc.
- **Cold-start: 5.0s → 1.1s** (verified: `time python -c 'import mcp_server'`). MCP handshake now reliably registers all 80 tools in Claude Code session.
- **Project `.mcp.json`**: declares `aifolimizer` server explicitly at repo root so any Claude Code session in this dir auto-registers without depending on user-scope global config.

### NOT changed
- `_TOKEN_TTL_HOURS = 8` in wealthsimple.py kept. Documented future option (let WS reject naturally) added to `restore_session` docstring. Trade-off: hourly security cycle vs. unattended-run convenience.

### Verified
Lazy import smoke test passes. `mcp list` still shows ✓ Connected.

### Next session
User must MFA-login once (`python backend/mcp_login.py`) to populate `~/.aifolimizer/ws_session.json`, then restart Claude Code to pick up `.mcp.json` and re-handshake MCP with fast cold-start.

---

## 2026-05-29 - Claude-run skill automation (headless + fault-tolerant)

### Why
Reasoning skills only ran when interactively in Claude Code. Goal: run them automatically *by Claude*, push to Telegram, survive reboots; free-LLM agent route kept as fallback if Claude Pro lost.

### Built / changed
- **Unified WS session file** (fault-tolerance must-fix): `mcp_server._SESSION_FILE` and `mcp_login.py` now both use `~/.aifolimizer/ws_session.json` - same file `wealthsimple._persist_session` rewrites on token refresh. Prevents rotated refresh token from orphaning file MCP server reads (headless runs now survive full refresh-token lifetime; MFA only on first login / forced re-auth). `mcp_login.py` writes canonical `{email, session_json, saved_utc}` schema + chmod 0600.
- **main.py lifespan**: calls `wealthsimple.restore_session()` at startup so scheduler re-seeds session after restart and keeps token warm.
- **MCP tools**: `get_earnings_calendar(account_id, symbols=[])` now unions watchlist/extra symbols + adds `held` flag (Option A). New `get_watchlist`. New `get_trade_ideas(top_n, include_watchlist, min_risk_reward)` - reuses `recommendations.get_recommendations` (no duplicated scoring), filters to actionable + R:R floor, returns entry/stop/target/RR/conviction.
- **New skills**: `top-trades-today` (composer: get_trade_ideas + crowding + catalyst guards), `position-review` (router -> earnings-analyzer / earnings-postmortem / adversarial-research / stock-analysis -> HOLD/TRIM/SELL, logs decisions; respects subagent-nesting limit in sweeps).
- **Automation scripts**: `backend/scripts/send_telegram.py` (plain-text, 4096-char chunked; verified real send), `backend/scripts/run_skill_fallback.py` (free-LLM tier via agent_registry runner), `scripts/run-claude-skill.ps1` (Claude primary -> free-LLM fallback -> Telegram, WS-session preflight, run log), `scripts/register-skill-task.ps1`, `scripts/install-backend-service.ps1` (NSSM), `scripts/AUTOMATION.md` runbook.

### Resilience model
Two-tier: **Claude** (`claude -p`, Pro) primary; **free LLMs** (existing backend agent route) fallback when Pro/auth unavailable. New composer skills have no free-LLM runner (Claude-only). Keep agent_registry + skill_llm_runner.

### Verified
Import/compile-clean (mcp_server, main, mcp_login, both py scripts); PS scripts parse; send_telegram real send EXIT=0; fallback exits 4 cleanly with no session. Live `get_trade_ideas` / full `claude -p` run pending user MFA login (Phase 0).

### Known follow-ups
MCP cold import ~5s (eager service imports) -> `mcp list` health-check can time out; harmless for `claude -p`. Lazy-import pass = perf-optimizer task. Optional phases not built: MFA-relay over Telegram, watchlist earnings in daily-briefing, event-driven Claude skills, hosted backend.

---

## 2026-05-28 - Backtest + Technicals + Geopolitical Upgrades

### What

**backtest.py + MCP `backtest_portfolio`:**
- `profit_factor` (gross_profit / gross_loss) added to per-symbol output for all signal strategies
- `insufficient_trades_warning` flag (True when num_trades < 150 - statistically insufficient)
- `insufficient_trades_count` added to portfolio_totals per strategy
- `exclude_weekdays: list[int]` param - skip entries on specified weekdays (0=Mon). Pass `[0]` to test "no Monday entries" filter from backtesting research
- `max_hold_days: int` param - force-exit positions after N calendar days regardless of signal. Adds time-based exits to reduce overnight/gap exposure
- Both params wired through entire call chain and cache key

**technicals.py:**
- `_candle_patterns()` - detects doji, hammer, shooting star, bullish/bearish engulfing, marubozu on last 2 bars. Returns `{detected: [...], signal: bullish|bearish|indecision|neutral}`. Added to `_compute_from_df` output as `candle_patterns` field
- `get_technicals_mtf()` - multi-timeframe analysis. Fetches 1d/1wk/1mo data per symbol via yfinance, runs `_compute_from_df` for each TF, returns key signals per TF + `mtf_confluence` dict (`trend_alignment`, `signal_alignment`, `overall`). Cached 1h
- New MCP tool `get_technicals_mtf` with `timeframes: list[str]` param

**geopolitical.py (new service):**
- `get_geopolitical_signals(lookback_hours=24)` - queries GDELT 2.0 Doc API (free, no key) for conflict/trade/sanctions/energy themes
- Returns `global_tension_index` (0-100), per-region scores (Americas, Europe, Asia_Pacific, Middle_East, Emerging), `hot_regions` (score >= 60), `categories_detected`, `market_implications` (ETF/sector impacts)
- New MCP tool `get_geopolitical_signals` - use alongside `get_macro_snapshot` in macro-impact analysis

### Why
Multiple external sources (GeoTrade architecture, backtesting research, ICT curriculum) independently identified: (1) missing profit_factor/trade-count quality gates, (2) single-timeframe blind spot, (3) zero geopolitical risk signal. All gaps closed without new paid data sources.

---

## 2026-05-28 - Quant Anomaly Skills: PEAD + Momentum + TOTM

### What
3 evidence-based market anomaly signals added as skills:

- **`pead-tracker`** - Post-Earnings Announcement Drift (Bernard & Thomas 1989). Scans holdings for earnings surprises in last 85 calendar days, computes remaining drift window (60 trading days), estimates residual edge by firm size (large 2.8%, mid 4.3%, small 5.1%). Uses `get_earnings_results` + `get_technicals` + `get_fundamentals`.
- **`momentum-scanner`** - 12-Month Momentum (Jegadeesh & Titman 1993) + 52-Week High Effect (George & Hwang 2004). Ranks all holdings by composite score (50% 12m return from `backtest_portfolio`, 30% 52wk high proximity from `get_technicals`, 20% Minervini score). Flags laggards as trim candidates; crowding-suppresses add signals.
- **Turn-of-Month signal in `daily-briefing`** - McConnell & Xu (1897-2005): all positive equity returns concentrated in last trading day of month + first 3 trading days. Added TOTM window check to catalyst day section.

### Why
Portfolio analytics lacked systematic exploitation of documented academic anomalies. All three use existing MCP tools with zero new data sources.

---

## 2026-05-18 - Data Layer + Accuracy/Benchmarking Pass (Phase 1-6)

### Why
No track record, single yfinance source, no alpha measurement, no trust signal. Added multi-source fallback, historical backtest of all 13 skills, forward paper-trade pipeline, alpha vs benchmarks, public TRACK_RECORD.md.

### Phase 1 - Multi-Source Data Router
- `backend/app/services/data_sources/` - new package with ABC + 5 adapters:
  - `base.py` - `DataSource` ABC, `PriceBar`, `Quote`, `Fundamentals`, `SourceUnavailable`
  - `yfinance_src.py` - primary (no key)
  - `stooq_src.py` - EOD fallback (`STOOQ_KEY`, free captcha)
  - `alphavantage_src.py` - fundamentals fallback (free 25/day, `ALPHA_VANTAGE_KEY`)
  - `finnhub_src.py` - quote + metrics fallback (free 60/min, `FINNHUB_KEY`)
  - `tiingo_src.py` - history fallback (free 50/hr, `TIINGO_KEY`)
- `backend/app/services/data_cache.py` - SQLite disk cache (`.cache/data.sqlite`, gitignored). Tables: quotes, history, fundamentals, source_stats. TTL-checked. `log_source_call` records every provider call for reliability reporting.
- `backend/app/services/data_router.py` - fallback chain router. `get_quote` → `get_history` → `get_fundamentals` each try chain in order, hit disk cache first. `get_quotes_batch` uses `yf.download` for N symbols - **13.5x faster** than serial (413ms vs 5585ms for 8 symbols). `prewarm()` batch-warms on startup.
- `.env` - added `ALPHA_VANTAGE_KEY`, `FINNHUB_KEY`, `TIINGO_KEY`, `STOOQ_KEY` slots (all blank, free-tier).
- MCP: 2 new tools - `get_quote_with_source`, `get_data_source_reliability`.

### Phase 2 - Skill Backtester (Historical KPIs)
- `backend/app/services/skill_backtest.py` - codifies all 13 skills as deterministic Python rules. `backtest_skill(skill, universe, lookback_days)` → `SkillBacktest` with CAGR, Sharpe, Sortino, max DD, hit-rate, num_trades, alpha vs SPY+XEQT. `backtest_all_skills()` runs all 13, persists JSON to `.cache/backtests/`.
- MCP: `get_skill_track_record(universe, lookback_days, fresh)`.

### Phase 3 - Forward Paper-Trade Pipeline
- `backend/app/services/paper_trade.py` - `log_recommendation` appends to `recommendations.jsonl` with live entry price. `score_recommendations` marks-to-market open recs, flags stop-out/target-hit. `get_track_record` returns rolling 7/30/90d win-rate + avg return by conviction.
- MCP: `log_recommendation`, `score_recommendations`, `get_live_track_record`.

### Phase 4 - Alpha Attribution + AUM Bench
- `backend/app/services/alpha_attribution.py` - `snapshot_equity` appends daily NAV to `portfolio_history.jsonl` (idempotent per day). `get_alpha_attribution` computes annualized return, alpha, beta, R², info ratio, tracking error vs SPY/XEQT/TSX/QQQ. Includes `_WS_MANAGED` published profile returns (conservative/balanced/growth/aggressive/halal_growth, 1y/3y/5y).
- `main.py` - pre-warms quote cache for 10 common symbols on startup (background task, non-blocking).
- MCP: `snapshot_portfolio_equity`, `get_alpha_attribution`.

### Phase 5 - Trust Signal
- `backend/app/services/trust_report.py` - writes `TRACK_RECORD.md` (public) + `track_record_full.jsonl` (gitignored). Includes methodology, data-source table, backtest KPIs, live rec stats, source reliability, WS Managed comparison, audit trail.
- MCP: `generate_trust_report`.

### Phase 6 - Performance
- `data_router.get_quotes_batch` - 13.5x speedup (413ms vs 5585ms serial). Disk-cached, falls back to serial on parse failure.
- MCP: `get_quotes_batch`.
- Startup pre-warm via `@app.on_event("startup")` (non-blocking `create_task`).

### MCP tool count: 22 → 32 (+10)

---

## 2026-05-17 - Optimization Pass (Tier 1+2+3)

### Why
Audit surfaced: crowding not on UI, alerts had no Task Scheduler, positioning feature without crowd_fade backtest, no PII filter tests.

### Tier 1 - Visibility + Safety
- `.claude/skills/daily-briefing/SKILL.md` - morning digest, 7 MCP tools, ≤400 words. Auto-triggers on "morning briefing", "daily digest", "what changed overnight?".
- **Crowding on dashboard**: `GET /ws/crowding` (top_n=15). `PortfolioTable` "Crowding" column renders `consensus / neutral / contrarian · NN` badge (rose/slate/emerald) + hover tooltip (inst%/short%/analysts/news). Dashboard fetches crowding in parallel on session change + refresh.
- **Alerts scheduler**: `backend/scripts/schedule_alerts.ps1` - registers Windows Scheduled Task running `run_alerts.py` every 30 min Mon-Fri 9:30-16:00. Flags: `-DryRun`, `-Unregister`. No admin required. Snapshots crowding for top 15 holdings (idempotent per-day) → regime-shift dataset.
- **pii_filter tests**: `backend/tests/test_pii_filter.py` - 5 tests, `filter_portfolio` + `filter_user_context`. Asserts PII keys never appear at any nesting depth. 5/5 passing pytest 9.0.3.
- `backend/requirements.txt` - adds `pytest>=9.0.0` + `diskcache>=5.6.0`.

### Tier 2 - Validate Positioning Thesis
- `backend/app/services/backtest.py`:
  - 2 new strategies: `crowd_fade` (sma_cross, skip consensus-crowded) + `crowd_buy` (sma_cross, contrarian-only).
  - `tx_cost_bps` param (default 5 bps/leg).
  - `_run_strategy_on_window` helper for walk-forward reuse.
- `backend/app/services/positioning.py`:
  - `snapshot_to_history(symbols)` - appends `{date, symbol, crowding_score, crowding_label}` JSONL (idempotent per-day).
  - `detect_regime_shifts(symbols, lookback_days=30, score_delta_threshold=25.0)` - compares first vs last score in window.
  - 2 new MCP tools: `snapshot_positioning_history` + `get_crowding_shifts`.

### Tier 3 - Honest Math + Cross-Process Cache
- `backtest.py` - `walk_forward=True` splits window: in-sample (first `train_frac=0.7`) + out-of-sample. Output adds `in_sample`, `out_of_sample`, `oos_minus_is_pct`.
- `backend/app/services/cache_layer.py` - thin `diskcache.Cache` at `.claude/context/.diskcache/` (200 MB cap, gitignored). `cache_get/cache_set(ns, key, value, ttl_seconds)`. Pickled, SQLite-backed, thread+process-safe.
- `positioning.py` + `fundamentals.py` - L1 (in-process dict) + L2 (diskcache, shared FastAPI ↔ MCP). Cold MCP start hits L2 if FastAPI warmed within 6h TTL.

### MCP tool count: 17 → 20

### Verified
- pytest 5/5 (pii_filter)
- backtest smoke: `crowd_fade` + `tx_cost_bps=5` + `walk_forward=True` correct shapes
- positioning snapshot: idempotent, regime detector reads back correctly
- frontend: PortfolioTable compiles, dashboard fetch parallel

---

## 2026-05-16 - Positioning / Crowding Signals

### Why
Goldman/BlackRock 2025: AI-driven flows pile into same names → late entries into consensus trades have negative expected alpha. Guard for stock-analysis/cash-deployment/adversarial-research.

### Added
- `backend/app/services/positioning.py` - per-symbol crowding signal:
  - `institutional_ownership_pct`, `short_pct_float`, `insider_ownership_pct`, `analyst_count`, `analyst_recommendation`
  - `headlines_7d`, `headlines_30d`, `headline_velocity_ratio`
  - `crowding_score` 0-100 - weighted (inst 35%, short 20%, analyst 20%, news 25%)
  - `crowding_label`: `consensus` (≥70) / `neutral` / `contrarian` (≤30)
  - Cache 6h, parallel fetch ThreadPoolExecutor(8)
- MCP: `get_positioning_signals(account_id, symbols)`. Defaults to top 15 holdings if `symbols=[]`. Tool count 17 → 18.
- Skill wiring: stock-analysis (Stage 7), cash-deployment (Setup Score /5→/6, consensus disqualified bucket), adversarial-research (Stage 1 6th tool, Consensus sub-agent in Stage 2).

### Smoke test
- AAPL: inst 65.7%, short 0.9%, 43 analysts → crowding 85.0 → `consensus` ✓
- NVDA: inst 70.6%, short 1.2%, 57 analysts → crowding 88.3 → `consensus` ✓
- XEQT.TO: all null (ETF gap) → crowding 39.0 → `neutral` ✓

### Known limits
- yfinance.news max ~10 articles → velocity ratio caps artificially high (consistent bias)
- TSX/.TO sparse on institutional+analyst fields - label unreliable when 3+ inputs null
- Crowding ≠ overvaluation. Adjusts conviction, doesn't invert call.

---

## 2026-05-16 - Backtesting Service + `backtest_portfolio` MCP tool

### Added
- `backend/app/services/backtest.py` - per-position rule-replay over historical OHLCV. Strategies: `buy_hold`, `rsi_swing` (RSI<30 buy/RSI>70 sell), `sma_cross` (close > SMA50).
- Metrics: `total_return_pct`, `cagr_pct`, `sharpe`, `max_drawdown_pct`, `num_trades`, `days`.
- Portfolio aggregation: weighted total/CAGR per strategy, worst single-position drawdown. `delta_vs_buy_hold_pct`.
- Cache: 1h per `(symbol, strategy, lookback_days)`.
- MCP: `backtest_portfolio(account_id, symbols, lookback_days, strategies, top_n)`. Defaults: top 15 holdings, 365d, all 3 strategies. `lookback_days` clamped 30..730.

### Smoke test
AAPL 365d: buy_hold +42.7% (sharpe 1.7, DD -13.8%); rsi_swing +11.9% (-30.8 vs buy_hold); sma_cross +32.9% (-9.8). Both active lose to passive - expected for momentum names in uptrend.

---

## 2026-05-16 - Alerts Service + ntfy.sh Push

### Added
- `backend/app/services/alerts.py` - 6 rules + ntfy.sh dispatcher + JSONL history. Dedup: same `(rule, symbol, day)` fires once. State `.claude/context/alerts_state.json` (auto-trimmed 7d). History `.claude/context/alerts.jsonl`.
- `backend/scripts/run_alerts.py` - CLI runner. `--dry-run` skips push. `--account TFSA` filters.
- MCP: `get_triggered_alerts(since_hours, limit)` + `run_alerts_now(account_id, price_drop_pct, dry_run)`.

### Rules
`price_drop_intraday` (−5%), `rsi_oversold` (≤30), `rsi_overbought` (≥75), `earnings_imminent` (next 3 days), `concentration_single` (>10%), `concentration_sector` (>35%)

### Config
`NTFY_TOPIC` in `backend/.env`. Unset → alerts only logged. ntfy.sh free tier, no signup.

---

## 2026-05-16 - New skill: `cash-deployment`

### Added
- `.claude/skills/cash-deployment/SKILL.md` - routes uninvested cash to holdings ranked by setup quality. Excludes concentration-flagged, stage 3/4, overbought, deteriorating. Outputs Setup Score /5 table + dollar/share allocation.
- MCP `list_analysis_modes` → 12 skills.
- Triggers: "where do I put my cash?", "I have $X to invest", "deploy my cash", "what should I buy with my settled funds?"

---

## 2026-05-16 - New skill: `earnings-postmortem` + MCP `get_earnings_results`

### Added
- `.claude/skills/earnings-postmortem/SKILL.md` - post-report breakdown: headline beat/miss, 4-quarter trend, guidance shift, analyst reaction, valuation re-rate, Canadian tax-aware action rec.
- MCP: `get_earnings_results(account_id, symbols, quarters=4)`. Cached 12h.
- `backend/app/services/fundamentals.py` - `get_earnings_history(symbols, quarters)` via yfinance `Ticker.earnings_history`. Parallel ThreadPoolExecutor(max_workers=8).
- Triggers: "did X beat?", "what did Y report?", "how did earnings go?", "Q1 results"
- Smoke: AAPL/MSFT 4 quarters each, all "beat", surprise_pct 3-13%.
- Gotcha: EPS only - no revenue. TSX/.TO coverage sparse.

---

## 2026-05-16 - New skill: `stock-compare`

### Added
- `.claude/skills/stock-compare/SKILL.md` - Goldman/Citadel side-by-side matchup. Strategy lens (growth/income/value) + horizon. Reuses `get_fundamentals`, `get_technicals`, `get_news_headlines` for two tickers.
- Output: verdict-first → 15-row matrix → moat → catalysts/risks → valuation → TA setup → Canadian tax-aware placement.
- MCP `list_analysis_modes` → 10 skills.
- Triggers: "X vs Y", "which is better A or B", "should I pick X or Y"

---

## 2026-05-14 - Phase 6: Performance Pass

### Backend
- `app/api/ws.py` - `_PORTFOLIO_CACHE` key → `(session_id, account_id)` per-tab. `asyncio.Lock` per key with double-checked locking - concurrent fetches dedupe to one round-trip.
- `app/services/market_data.py` - `_TICKER_CACHE` (5-min TTL) for `yf.Ticker.info` + `fast_info`. 2.0s → 0.0s cached.
- `app/services/technicals.py` - batches into one `yf.download(group_by="ticker")`. 5 syms in 0.5s vs ~1.4s serial.
- `app/services/fundamentals.py` - `ThreadPoolExecutor(max_workers=8)` for uncached symbols. 5 syms in 1.2s.

### Frontend
- `components/CountdownLabel.tsx` - isolates 5s tick (was re-rendering all charts/tables every 5s).
- `React.memo` on: `AllocationChart`, `HealthScoreWidget`, `MacroWidget`, `BenchmarkWidget`, `OptimizerWidget`, `AlertsPanel`, `RecommendationsPanel`.
- `lib/api.ts` - all `wsGet*` helpers accept optional `signal?: AbortSignal`.
- `app/dashboard/page.tsx` - per-loader `AbortController` (new fetch cancels prior in-flight). Stale-while-revalidate: skeleton only on initial load. Cleanup effect aborts in-flight on unmount.

---

## 2026-05-14 - Phase 5: Multi-Provider LLM Narrative Layer

### Goal
AI-generated narrative per recommendation card - no Anthropic key. Router auto-selects best free provider at runtime.

### New service
- `backend/app/services/llm_router.py` - 4 providers: GitHub Models → Gemini → OpenRouter → Qwen. Per-provider: 2 consecutive failures → 5-min cooldown → retry. 30-min narrative cache keyed by (symbol, score, market_regime). `generate_narratives_batch()`: concurrent, semaphore (4 max). Returns `None` per symbol when all providers fail.

### Updated `backend/app/core/config.py`
Added: `github_token`, `google_api_key`, `openrouter_api_key`, `dashscope_api_key` (all optional)

### Endpoints
- `GET /ws/ai-narratives` - `{narratives: {symbol: text}, providers: [...]}`
- `GET /ws/llm-status` - available providers

### Frontend
- `RecommendationsPanel.tsx`: AI narrative per card (italic, indigo left-border). Pulse skeleton while loading. Provider badge.
- `dashboard/page.tsx`: Narratives load 3s after render; re-fetch on refresh with same stagger.

### .env additions (at least one required):
```
GITHUB_TOKEN=ghp_...           # GitHub Pro - best free option
GOOGLE_API_KEY=AIza...         # Google AI Studio free tier
OPENROUTER_API_KEY=sk-or-...   # OpenRouter free models
DASHSCOPE_API_KEY=sk-...       # Qwen via Aliyun
```

---

## 2026-05-14 - Phase 4: Auto-Recommendation Dashboard

### Goal
Always-on BUY/SELL/HOLD/WATCH without manual Claude commands. Rule-based engine, no Anthropic API key.

### New backend service
- `backend/app/services/recommendations.py` - scoring engine (0-10):
  - Technical: Minervini stage, RSI, MACD histogram, SMA200 trend, 52w range
  - Fundamental: analyst rec/target, EPS growth, short interest, revenue growth
  - Macro: market regime (bull/bear × fear), VIX, Fear & Greed
  - Position: weight concentration, total return
  - Thresholds: ≥7.5=BUY, ≥5.5=HOLD, ≥3.5=WATCH, <3.5=SELL. ETFs skip fundamental signals.

### Updated services
- `macro.py` - `fear_and_greed()` (CNN Fear & Greed, free HTTP, 1h cache). Merged into `market_breadth()`.
- `market_data.py` - `day_change_cad` on `PortfolioSummary`.

### Endpoints
- `GET /ws/recommendations` - sorted SELL→BUY→WATCH→HOLD
- `GET /ws/macro` - market breadth + FRED snapshot

### New frontend
- `RecommendationsPanel.tsx` - grouped by action, color-coded cards. Score bar, analyst upside%, Minervini badge, RSI badge, top 3 reasons.
- `MacroWidget.tsx` - regime badge + signal text. VIX, SPY vs SMA200, Fear & Greed, FRED rates.

---

## 2026-05-14 - Phase 3: Market Breadth + Minervini Stage Analysis

### New MCP tool
- `get_market_breadth()` - VIX, SPY vs SMA200, composite market_regime + regime_signal. Cached 1h.

### Updated services
- `macro.py` - `market_breadth()` using yfinance `^VIX` + SPY 1y daily OHLCV.
- `technicals.py` - Minervini: `stage` (1-4), `minervini_score` (0-7), `sma_150`, `sma_200_slope_pct`, `week52_high`, `week52_low`, `pct_from_52w_high/low`.

### New REST endpoint: `GET /ws/market-breadth`

### Updated skills
- `macro-impact` - step 4 calls `get_market_breadth`; step 7 uses `market_regime` for risk stance.
- `stock-analysis` - technical section includes Minervini stage/score + 52w context.
- `sector-rotation` - step 4 calls `get_market_breadth`; rotation conviction calibrated to regime.

---

## 2026-05-14 - Phase 2: Real-time Dashboard + Multi-agent Auto-analysis

### New backend services
- `health_score.py` - rule-based health score (0-100, grade A-F). No external calls.
- `crypto_data.py` - CoinGecko v3 free, no key. Live CAD prices, 24h/7d/30d change, ATH drawdown, 20 crypto symbols. 5-min cache.

### New REST endpoints: `GET /ws/health-score`, `GET /ws/alerts`, `GET /ws/crypto`
### New MCP tools: `get_crypto_data(account_id, symbols)` - symbols=[] auto-detects from portfolio.

### New frontend
- `HealthScoreWidget.tsx` - grade badge + 5-dimension breakdown.
- `AlertsPanel.tsx` - dismissable alert cards (high/warning/info).
- Health widget in summary grid. Alerts auto-load. Auto-refresh 5 min. Skill panel click-to-copy. Parallel loads.

### Updated all 9 skills: all call `mcp__aifolimizer__get_profile` as step 1.
### New tooling: `backend/scripts/build_skills.py` - lists MCP tools + skill health, scaffolds new SKILL.md.

---

## 2026-05-14 - Phase 1: Data Foundation

### Added
- `fundamentals.py` - yfinance.info: P/E, EPS, div yield, payout, market cap, earnings date, analyst targets, beta, short interest. 6h cache.
- `technicals.py` - `ta` lib: SMA20/50/200, RSI(14), MACD, Bollinger Bands, volume SMA, trend signal. 1h cache.
- `news.py` - yfinance news, 5 articles/ticker, 30-min cache.
- MCP: 4 new tools: `get_fundamentals`, `get_technicals`, `get_earnings_calendar`, `get_news_headlines`.
- REST: 4 new endpoints: `/ws/fundamentals`, `/ws/technicals`, `/ws/earnings-calendar`, `/ws/price-history`.
- `backend/requirements.txt` - `ta>=0.11.0` (NOT pandas-ta - incompatible with Python 3.14).
- `.claude/skills/` - 8 skills moved from `~/.claude/skills/` to project-level.

### Updated skills
- `stock-analysis` - calls `get_fundamentals` + `get_technicals` + `get_news_headlines`.
- `earnings-analyzer` - calls `get_earnings_calendar` + `get_fundamentals`.
- `dividend-strategy` - calls `get_fundamentals` for div yield/payout.

### New skills: `adversarial-research` - parallel bull/bear sub-agent pipeline, probability-weighted synthesis.

---

## 2026-05-XX - MVP Build (Initial)

### Built
- `wealthsimple.py` - MFA-aware login, 8h token TTL in RAM.
- `pii_filter.py` - strips account IDs, names, emails before MCP response.
- `mcp_server.py` - 9 tools: get_profile, get_portfolio, get_xray, get_concentration_warnings, get_tax_loss_candidates, get_risk_metrics, get_correlation_matrix, get_macro_snapshot, list_analysis_modes.
- FastAPI REST API (`main.py` + `app/api/ws.py`) - login, OTP, portfolio, profile endpoints.
- `market_data.py` - live prices, sectors, day change.
- `macro.py` - FRED: Fed funds, 10Y, CPI, CAD/USD, BoC rate. 12h cache.
- `quant.py` - Sharpe, Sortino, VaR 95%, correlation matrix, pure Python.
- `portfolio_analytics.py` - ETF X-ray, concentration warnings, tax-loss candidates.
- 8 institutional analysis skills at `~/.claude/skills/`.
- Next.js 14 dashboard - login (MFA), portfolio table, allocation chart, skill directory.
## 2026-06-04 — MAX/lottery-stock reversal guard
- `technicals.py`: added `max_1d_return_21d_pct` + `lottery_flag` (Bali-Cakici-Whitelaw 2011). Flag = max single-day gain in last 21d >= 8% AND >= 3x trailing-63d daily vol (self-normalized). Surfaces as signal_conflict (chase warning).
- `pre-trade-check`: lottery flag = warning gate for BUY/ADD (wait for mean-revert).
- `cash-deployment`: lottery_flag != true added to Setup Score + ideal-add cross-ref.
- Source: review of academic-anomaly screenshots. 5 of 6 anomalies already shipped (52wk-high, PEAD, 12m momentum); pairs-trading + turn-of-month skipped (no short / tx-cost in retail TFSA). Verified: ruff clean, fires on synthetic 15% spike, no false positive on AAPL/NVDA/XEQT.

## 2026-06-04 — Full audit pass (test/perf/security/logic/automation/integrations)
Baseline: 335 pass + 1 fail. Now 336 pass / 0 fail. 12 files changed.
- **PII test fix**: portfolio_analytics xray/sector/asset breakdowns returned unrounded float weights (16-digit fractional → tripped `\d{14,}` account-ID guard). Rounded to 6dp.
- **Perf (high)**: mcp_server `_load_portfolio` called sync `market_data.enrich()` on event loop (blocked ~25 MCP tools). Wrapped in `asyncio.to_thread`.
- **Logic (currency)**: `xray_exposures` used `pos.market_value` (native) vs CAD denominator; siblings use `market_value_cad`. Fixed. Same class in `tax_loss_candidates` → `market_value_cad`/`book_cost_cad`.
- **Logic**: trade_ticket SELL/EXIT nulled phantom long-side `target_price`/`risk_reward_ratio`.
- **Security**: data_router `_try_source` scrubs `(api)key|token=...` from provider error strings before SQLite log (`_scrub`). llm_router dropped `prompt_first_120_chars` from audit log (footgun).
- **Integrations**: macro `_fred_csv` guards row unpack (len!=2 skip); defillama added `raise_for_status` on both calls; coingecko_src docstring CAD→USD; circuit_breaker doc drift 4→6.
- **Perf**: data_cache enabled WAL + synchronous=NORMAL; data_router copy-before-mutate cached quote.
- **Automation (self-logging)**: wired nightly equity NAV snapshot into scheduler `_score_once_if_due` so `get_alpha_attribution` self-populates (was manual-only).
Verified: imports clean, ruff+format clean, 336 tests pass, e2e 14/14, live get_xray/integrations OK, all hooks fire + continuous code-graph sync confirmed.
Deferred (not worth breakage risk): dead RQ task wrappers (nightly_q/run_risk_gate/run_alerts_for_tenant — harmless no-ops, inline+external paths work); fundamentals/technicals in-flight stampede guard (deadlock risk); signal_history calendar-vs-trading-day skip-label (no wrong numbers); py3.14 vs pyproject <3.14 pin (stale, runs fine).

## 2026-06-04 — Fix: no Telegram alerts from scheduled runs
Three independent root causes, all fixed:
1. **Auth (blocker)**: `run_alerts.py._load_session` hand-rolled `_finalize_session` (no token refresh) → `UNAUTHENTICATED`/`invalid_grant` every run. Now uses hardened `wealthsimple.restore_session()` (force-refreshes expired access token from refresh_token). Verified: 17 alerts triggered, exit 0.
2. **Schedule**: `schedule_alerts.ps1` used `New-ScheduledTaskTrigger -Once` + `.DaysOfWeek` (no-op on -Once) + `RepetitionDuration 6h30m` → task fired once May 28, never recurred (NextRun empty). Fixed: dropped DaysOfWeek hack, removed RepetitionDuration (=indefinite 30-min repetition), bumped ExecutionTimeLimit 3→10min (cold yfinance+massive 429s exceed 3min). Also fixed em-dash in -Description (broke PS 5.1 ANSI parse of BOM-less file). Re-registered: NextRun armed, scheduled run = result 0.
3. **Scheduler dead**: FastAPI backend (hosts APScheduler via main.py lifespan; MCP process does NOT) wasn't running (port 8000 down, `aifolimizer-backend` task exited 0xC000013A). Started it → /health ok → nightly scoring/skills/calibration/equity-snapshot live again. `run.py` reload=True→env-gated (default off) so the scheduled service is single-process/robust (documented dev start uses `uvicorn --reload`).
Telegram delivery confirmed end-to-end (sendMessage 200, ok:true). Dedup is per-rule/symbol/day (7d state) — correct; today's keys already seeded by test runs, fresh pushes resume tomorrow.
Noted (not changed): `aifolimizer-daily-briefing` task mislabeled (runs run_alerts.py; works now post-auth-fix); `aifolimizer-worker` (RQ consumer) down — only affects per-tenant RQ skill ticks; nightly self-learning runs inline in scheduler.

## 2026-06-04 — Daily-briefing→Telegram + RQ worker fix
- **New `scripts/send_daily_briefing.py`**: headless/no-LLM digest. restore_session → portfolio → `skill_runner.run_all_skills` (codified composer) → formats daily-briefing snapshot (NLV/return/cash, next_action, insights, alerts) → Telegram (raw httpx, UTF-8). stdout reconfigured utf-8 for --dry-run on cp1252 consoles. Verified: real send exit 0, delivered.
- **Repointed `aifolimizer-daily-briefing` task** from run_alerts.py → send_daily_briefing.py (kept Mon-Fri 8:30 trigger; ExecutionTimeLimit 10min). NextRun tomorrow 8:30.
- **Worker fix `scripts/worker.py`**: RQ default Worker forks per job; `os.fork()` absent on Windows → worker crashed/exited every start. Now `SimpleWorker` (in-process, no fork) when `not hasattr(os,'fork')`. Verified: processes queued `run_skill_tick_for_tenant`, stays listening. Started persistently (Redis reachable at redis://localhost:6379/0).
- Discovered: scheduler HAD been enqueuing per-tenant skill ticks all along — they sat unconsumed because the worker never ran on Windows.

## 2026-06-04 — Alert noise reduction + lifecycle notifications
User: MFA alerts too frequent (30min); portfolio alerts only on critical/major; add system up-after-down.
- **Portfolio alerts → high-severity only**: `alerts.dispatch(min_severity=)` gates Telegram push (history still logs all). `run_alerts.py --min-severity` default **high** → only major price moves (≥10%) push; earnings/concentration/RSI logged + surfaced via daily briefing. Verified: 3 triggered → 1 pushed, 2 held.
- **MFA → once per expiry event**: `mfa_notify.py` cooldown 6h→24h (daily reminder cap) + new `clear_flag()`. `run_alerts.py` on dead session fires notify once + exits 0 (was crashing every 30min); on valid session calls `clear_flag()`. `main.py` lifespan clears flag on successful restore, fires notify on None. Net: one heads-up per real expiry, reset on re-auth.
- **System-up notification**: `main.py._fire_system_up()` pushes "🟢 aifolimizer online" once per boot, deduped 30min (crash-loop guard) via `.online-notify.last` marker. Fired from lifespan. Verified live (marker written = push sent).

## 2026-06-04 — Fix: MCP hangs + wrong account values (per-holding currency)
User: skills get_profile/get_portfolio_analysis/get_personal_context "stick" forever (no error, must interrupt); account values + per-holding currency wrong (most holdings USD shown as CAD).

Root causes + fixes:
1. **Per-account holdings empty** — `wealthsimple._position_account_id` read singular `account`/`accountInfo`; WS FetchIdentityPositions puts membership under `accounts` (list of {id}). Returned "" for all → per-account filter dropped every holding (single-account view: total ok from NLV, positions []). Fixed to read `accounts[0].id`.
2. **Calls hang with no error** — running MCP server predated the 30s requests-timeout patch (disk code correct: fresh proc get_profile 3.92s). Architecture had NO async-layer backstop and ran all WS calls on the shared `asyncio.to_thread` default pool, so a WS stall starved unrelated tools (get_personal_context, zero network). Added `mcp_server._WS_EXECUTOR` (dedicated 4-worker pool) + `_ws_call()` wrapper with `asyncio.wait_for(WS_OP_TIMEOUT_S=90)`; swapped all WS to_thread sites (_load_cached_session, login, get_positions, get_all_positions, get_portfolio_response). Now a stall → bounded RuntimeError, never an infinite stick, never cross-contaminates non-WS tools.
3. **Per-holding currency wrong** — WS reports per-holding money (totalValue/bookValue/averagePrice) in account base CAD even for NYSE/USD stocks; `_to_position_dict` tagged everything CAD off `totalValue.currency`. Now uses `security.currency` (authoritative native) and divides CAD→native by `_cad_per_usd()` FX for USD holdings. enrich re-multiplies → CAD totals/weights preserved, return % FX-invariant, per-holding displays native (26 USD / 3 CAD). Verified UNH $1586.92 USD (WS $1586.84), TFSA total 23503.65 (WS 23504.89), BB 259.20 CAD.
4. **USD cash always 0** — `_load_portfolio` called `enrich` with 4 positional args, dropping usd_cash_balance/net_deposits_cad/simple_return_pct. Now passes all (single: per_account fields; aggregate: sum usd + session net_deposits). cash_available_usd now 4101.92 (WS truth), account_return_pct 6.83 (was 0).

Deleted `tests/test_ws_timeout_patch.py` per user request (timeout patch itself retained in wealthsimple.py).
Verified: ruff clean, 336 tests pass, security suite 17/17, all 102 MCP tools import, concurrent get_profile+get_personal_context 3.79s (no starvation).
OPEN: per-holding return % is CAD-frame (~40.3%, = WS API unrealizedReturns) vs WS *app* native-USD return (UNH 36.8%). Native return needs cost basis at native FX — NOT in the position API (only CAD bookValue at historical FX). Pending user decision.
ACTION REQUIRED BY USER: restart/reconnect the MCP server (Claude Code keeps it alive across chat sessions) so these fixes load — the live hang is a stale pre-patch process.

## 2026-06-04 — WS MFA/stall root cause: duplicate MCP registration
- aifolimizer registered in BOTH project .mcp.json AND ~/.claude.json local scope (3 project paths) -> two mcp_server.py processes per session -> contention over one rotating WS token -> stalls + spurious MFA (token was NOT expired).
- Filelock (prior fix) only serialized token rotation; never removed the 2nd process, so symptom recurred.
- Fix: stripped aifolimizer from ~/.claude.json (backup .claude.json.bak-*); .mcp.json is sole source. Added non-blocking instance FileLock tripwire in mcp_server.py __main__ that warns to stderr on 2nd instance.

## 2026-06-04 — Shared rotating-token fix (A+B): _run_ws on read paths
- Root cause of recurring WSApiException/MFA: get_positions + get_all_positions built WealthsimpleAPI with _noop_persist, so ws-api mid-call access-token refresh ROTATED the single-use refresh token server-side but never persisted it -> disk token went dead (kills even a single session on next access-expiry). Concurrent Claude sessions compounded it: each refreshed independently and invalidated the others.
- Fix: new _run_ws(email, fn) runs every read-path WS call under _PERSIST_LOCK, reloads freshest token from disk first, builds client with _persist_session so rotations are saved. Serializes WS calls cross-process; lock reentrant so balances thread-pool unaffected. Removed dead _noop_persist (module-level; mcp_login.py keeps its own local copy).
- NOT a duplicate-process bug: the "two instances" every prior session chased = venv launcher-stub (.venv python.exe) + base C:\Python314 interpreter = one logical server. Verified by killing child -> parent died, no respawn.
- Latent: venv is Python 3.14.5; CLAUDE.md pins <3.14 (ta incompat). Rebuild venv on 3.12/3.13.
- Verified: py_compile, ruff F-codes clean, 9 WS/session tests pass.

## 2026-06-04 — CORRECTION to Fix B: _run_ws is lock-free, not locked
- Prior note said B wraps every WS call in _PERSIST_LOCK. That caused a head-of-line CONVOY: with multiple Claude sessions, one slow/held lock wedged every read call behind the 45s timeout -> tool "stuck". Isolated test proved the WS path itself is fine (restore 4.5s, valid session); the hang was pure lock contention my own change introduced.
- Revised: _run_ws is lock-free. Reload freshest token from disk (atomic-rename writer => torn-read safe, no lock needed) + persist rotations via _persist_session. Rotation conflicts only occur during the ~1s refresh on access-token expiry (~30min), not on valid-token fetches; the rare simultaneous-refresh race fails one call and self-heals on next reload. Session establishment (_finalize_session) still serializes under the lock (infrequent).
- Verified: compile, ruff F clean, 9 tests pass, _run_ws completes 0.4s while a live peer holds the lock (convoy gone).
- NOTE: running mcp_server processes hold OLD code in memory until restarted; reload window/chat to pick up the lock-free version.

## 2026-06-04 — Fix get_profile NLV double-count (pii_filter output relabel)
- Bug: `filter_user_context` exposed per-account `invested_value` and top-level `total_invested`, but the internal field holds **NLV (cash + securities)**, not securities. Consumers (and humans) computing `NLV = total_invested + total_cash` double-counted cash (e.g. $23.5k NLV mis-read as $29.7k).
- Root cause: internal `Account.invested_value` = `financials.currentCombined.netLiquidationValue`; all 6 internal consumers (ws.py, run_alerts, send_daily_briefing, skill_runner, mcp_server) correctly treat it as NLV. Only the public output labels were misleading.
- Fix: relabel at the pii_filter boundary only (internal field + consumers untouched). Per account now: `cash_balance`, `securities_value` (=nlv-cash), `net_liquidation_value`. Top-level: `total_cash`, `total_securities`, `total_nlv`. Identity `total_nlv == total_cash + total_securities` now holds.
- Tests: test_pii_filter.py updated to assert new keys + identity; 5 passed.
- NOTE: running MCP server must restart to serve new shape (module already loaded).
- OPEN (separate bug, not fixed): get_portfolio per-position `market_value` is native USD while `book_cost`/tax-loss are CAD → wrong `weight` + bogus summary `total_return_pct`. Needs FX normalization in enrichment path.

## 2026-06-07 — ops cleanup: kill restart popups, fix launcher, disable Sentry
- Removed 4 stale scheduled tasks: `aifolimizer-backend` (run.py @logon), `aifolimizer-worker` (worker.py @logon — REDIS_URL unset → sys.exit(1) every boot), `aifolimizer-daily-briefing` (root dup of `\aifolimizer\daily-briefing`), `\aifolimizer\backend-watchdog` (probed :8000). The two @logon tasks were the "restart popups."
- Kept Telegram automation: `aifolimizer-alerts` + `\aifolimizer\{daily-briefing,top-trades-today,position-review}`.
- Hid skill-task consoles (`-WindowStyle Hidden`); switched alerts to `pythonw.exe` (no console). `register-skill-task.ps1` updated so future regs are hidden.
- Rewrote `scripts/aifolimizer-launch.ps1`: was probing dead FastAPI :8000 + /ws/restore. Now probes session via `wealthsimple.restore_session()` directly and drives interactive `mcp_login.py` for MFA. No backend daemon needed (MCP-only model).
- Disabled `SENTRY_DSN` in backend/.env (commented; errors → local logs). Stops cloud error flood + dead digest job. SENTRY_AUTH_TOKEN/ORG/PROJECT left (read-only digest path, inert without worker).
- Killed stale concurrent mcp_server.py instances + removed stale instance.lock. Confirmed MCP path is lock-free disk-reload (no per-call WS validate) — forced-MFA was refresh-token expiry, already mitigated by prior write-lock fix.

## 2026-06-08 — signal-policy postmortem + fixes: kill the negative-EV BUY leg
- Postmortem of 353 scored forward signals (live track record, NOT the stale 103 in TRACK_RECORD.md): overall 44.2% win / -0.58% avg. Loss is ENTIRELY the BUY leg (197 sigs, 17.8% win, -5.3% alpha vs XEQT). TRIM/SELL have real edge (77-94% win, +5-7% alpha). HIGH conviction INVERTED vs MED (23.6% vs 74.1%). Decay curve: edge ultra-short, h1 49% → h5 34%.
- Root cause: `win_prob` heuristic anti-correlated with outcomes — analyst-buy→0.62 (realized 18% win), sell→0.35 (realized 94%).
- Fixes (recommendations.py): (1) removed inverted analyst_rec→win_prob map (symmetric score-based only); (2) Kelly SUPPRESSED until calibrated (kelly_pct=None → EV/max-loss cascade off); (3) BUY gate 7.5→8.0 + fundamentals≥0 floor (ETF-exempt) + signal_quality≥3 floor, marginal longs→WATCH; (4) conviction capped at MED for BUY/ADD until calibration proves HIGH>MED>LOW; (5) wired `_evidence_context.calibrated` to `signal_history.calibrate_confidence` (was hardcoded False) — self-activates as h21 fills.
- trust_report.py: new SEED-NEGATIVE-EV / DEVELOPING-NEGATIVE-EV tiers — sign of realized return gates the label; negative EV can no longer read as a milestone. Regenerated TRACK_RECORD.md (now SEED-NEGATIVE-EV, 353 sigs).
- Deferred (staged in `.claude/context/signal-policy-fix-plan.md`, need review): horizon re-match to decay, benchmark-relative gate, AI-critic pre-emit gate, top-trades-today demote, portfolio risk-gate→engine wiring.
- Verified: ast+import OK, BUY-gate smoke 5/5, `score_signal_horizons` scored 208 new rows. NOTE: running MCP server holds pre-edit code — restart to load.

## 2026-06-08 — wire portfolio risk-gate into trade-ideas surfacing layer
- Hole (confirmed obs 2481): `risk_gate` state existed only as a read-only MCP tool; the sync recommendation engine + `get_trade_ideas` never consulted it, so portfolio-level halt/reduce did NOT brake surfaced BUY ideas (scheduler path was already gate-aware via size_multiplier).
- Low-touch fix (addendum B "surfacing layer" option): `get_trade_ideas` now fetches `risk_gate.get_current(thash)` after ranking. On `halt` → suppress BUY/ADD ideas (retain SELL/TRIM), add `suppressed_by_risk_gate` count + note. On `reduce_size` → annotate. Best-effort try/except — never blocks idea generation if pool/gate down. No engine edit (engine-path wiring B-core still deferred — touches the contended sync scorer).
- Also addresses #4 (top-trades-today 0/11): risk-off longs no longer surface as "top trades".
- Verified: project ruff clean, syntax OK; mirrors existing get_risk_gate_state fetch pattern.

## 2026-06-09 — full sync/audit sweep: doc-count drift, guard crash, skill+logic+dep audit
- Trigger: "make everything synced/up-to-date, optimize, cleanup, test, assess all skills, no busywork."
- Baseline (read-only): tree clean + synced w/ origin/master; ruff clean; 340 pass / 3 skip; graph fresh (HEAD 33bffe2); ONE logical MCP server (stub pair, not a dup); registered only in .mcp.json (no contention); Obsidian brain scaffolded.
- Ran 4-agent focused audit (skills / docs / deps / logic). Findings:
  - **Skills (27): healthy.** Zero broken/renamed MCP-tool refs across all 27 SKILL.md (every ref ∈ canonical 103-tool list). All frontmatter valid YAML. get_profile-FIRST satisfied; 3 correct exemptions (perf-optimizer, health-check, profile-setup). Skill-cluster overlaps (earnings, rebalancing, portfolio, trade-gate) are intentional + self-disambiguated.
  - **Logic: no bugs.** Known "OPEN FX bug" (USD market_value vs CAD book_cost) is ALREADY FIXED — market_data.py:201 `market_value_cad = market_value * fx`; per-position total_return_pct computed in native currency; weights/totals CAD-consistent. PII identity (total_nlv==cash+securities) holds; no id/email/name leak. recommendations.py invariants intact (BUY gate ≥8.0, kelly_pct None until calibrated, conviction capped MED). The prior changes.md "OPEN" note was stale.
- **Doc-count drift fixed (source of truth: 103 tools / 27 skills / 15 adapters):** CLAUDE.md (5), README.md (12 incl. dropping brittle "85 market-data + 15 WS"=100 split for a number-free phrasing; "other 23 read-only"→25), AGENTS.md (2), architecture.md (3), SETUP.md (4), FAQ.md (1), mcp-servers-pr-draft.md (2). check_doc_counts.py now passes. `.github/release-notes-v0.1.0.md` left at historical 84/22 (frozen release record).
- **Tooling bug fixed:** check_doc_counts.py crashed on Windows (cp1252) printing em-dash/arrow from quoted drift lines, masking 2 architecture.md failures. Added `sys.stdout.reconfigure(encoding="utf-8")` guard.
- **Deps (6 dependabot PRs, assessed vs actual usage, NOT merged — human/CI decision):** pydantic 2.8→2.13 + codeql 3→4 = merge-now (low). yfinance 0.2→1.4 = low (1.0 "no breaking changes"; auto_adjust always explicit; watch curl_cffi default). redis 5→8 + rq 2.0→2.9 = medium, co-test (RESP3 + new 5s socket_timeout can TimeoutError the RQ blocking worker). ws-api 0.33→0.35 = high-touch (unofficial GraphQL client; read changelog + live login→positions→token-refresh smoke before merge).
- Verified: ruff clean, check_doc_counts OK, 340 pass / 3 skip (×2).

## 2026-06-10 - perf: L2-cache _ticker_meta static fields
- market_data._ticker_meta now caches currency+sector to shared L2 (ns=ticker_meta_static, 24h) so cold MCP processes skip the per-holding yf `.info` HTTP. fast_info/price stays on 5min L1. Cold enrich for the book drops ~2.5s->~0.3s (24 names/8 workers). No freshness change to prices or WS-authoritative currency. pytest 340 pass.

## 2026-06-10 - get_profile real-time + last-good fallback
- mcp_server.get_profile now fetches live every call (real-time) but returns a last-good copy (flagged _stale) on WS error/timeout instead of blocking the full _WS_OP_TIMEOUT_S. Kills the stuck->kill->retry loop. pytest 340 pass, ruff clean.

## 2026-06-10 - bg tasks off WS token (fix random expiry)
- NEW app/services/portfolio_snapshot.py: single source of truth for portfolio snapshot L2 ns/keys + read(). mcp_server aliases its _PORTFOLIO_NS/_LASTGOOD_NS to it.
- run_alerts.py: reads snapshot (no restore_session); overlays real-time public quotes (data_router.get_quotes_batch) onto holdings so price alerts stay live; skips cleanly if no snapshot. Dropped wealthsimple/market_data/asyncio/mfa_notify usage.
- run_maintenance.run_ws_jobs: reads snapshot for NLV + symbols instead of WS.
- Result: zero bg WS-token rotation => kills the random-MFA race. ruff clean (5 files), snapshot roundtrip + live overlay verified (AAPL fake -1.5% -> live +0.35%), pytest 340 pass.

## 2026-06-10 - eager-warm market_data at MCP startup (kill get_profile import-convoy hang)
- mcp_server __main__: import app.services.market_data before mcp.run(). Moves the cold yfinance/numpy C-ext load off the concurrent WS enrich path (where it convoyed on the import lock for minutes) to a one-time 3.3s single-threaded boot step. Enrich lazy import now 1ms warm hit. ruff clean, mcp_server imports clean, pytest 340 pass. Effective on next MCP spawn.

## 2026-06-11 - signal_history JSONL->PG port (PG canonical, JSONL retired)
- Fixed split-brain: scheduler wrote PG signal_history while the interactive engine wrote a parallel JSONL with a separate realized-return model; PG realized_return_*d were NEVER filled (calibration had retreated to JSONL). PG is now the single source of truth.
- schema.sql + pool._apply_migrations: added entry_price + realized_return_{3,10,42}d (additive, idempotent; startup ALTER covers already-initialized containers). signals_repo allow-set -> 7 horizons via _HORIZONS/_REALIZED_COLS.
- scheduler._persist_integrated_signals persists entry_price (rec.current_price); signals_repo.insert_signal gains entry_price col ($25).
- NEW app/services/signal_backfill.py: ports score_horizons compute (data_router bars + _close_at_offset, sign-flip SELL/TRIM) into PG UPDATE realized_return_{H}d. Pure compute_return() unit-tested. Wired into scheduler nightly (Step 1b, beside the JSONL scorer).
- NEW signals_repo fns: rows_needing_backfill, set_realized_return, fetch_scored. NEW app/services/signal_analytics.py: PG rows -> legacy shape -> the SAME signal_history math (accuracy/decay/attribution/calibrate*). Refactored 5 signal_history fns to accept injected rows= (default JSONL).
- mcp_server: 6 signal tools now PG-first via _pg_or_jsonl_analytics helper; JSONL fallback when no pool OR PG empty (pre-migration).
- recommendations.py: interactive log_signal JSONL write guarded behind `not settings.postgres_dsn` (scheduler is sole PG writer; dissolves the sync-thread/async-pool boundary).
- migrate_jsonl_to_postgres: carries entry_price + outcomes.hN -> realized_return_Nd.
- Verify: pytest 349 pass / 3 skip (+9 new), ruff clean (project config), imports+compile clean. pyright NOT run (not installed locally). Plan: .claude/context/signal-history-pg-port-plan.md.
- NEXT (manual, docker up): run `python backend/scripts/migrate_jsonl_to_postgres.py` once to backfill PG from existing JSONL.
## 2026-06-11 - symbol/exchange resolution (CA tickers fetch correctly)
- Bug: WS-held Canadian tickers stored bare (XEQT, VFV) 404'd on yahoo; data_router never appended an exchange suffix even when classify_asset returned ca_equity. Backfill skipped_data=67.
- data_router._resolve_fetch_symbol (NEW): appends .TO for suffix-less ca_equity (KNOWN_CANADIAN) symbols; applied in get_quote/get_history/get_fundamentals/get_quotes_batch (cache key + chain stay on original symbol; US/crypto/fx/index untouched). Verified: XEQT/VFV -> 252 bars; batch keys preserved.
- Root cause of dual-listed ambiguity: wealthsimple.py stripped the WS exchange prefix (TSX:/NYSE:/NASDAQ:) at ingestion, discarding the only signal that distinguishes e.g. TSX:T (Telus->T.TO) from NYSE:T (AT&T->T). NEW _qualify_symbol maps the prefix -> Yahoo suffix (TSX/TSE->.TO, TSXV->.V, NEO/Cboe CA->.NE, CSE->.CN, US exchanges->bare); replaces the blind .replace() strip. Self-contained: uses the prefix already in the WS payload at ingestion, so resolution needs no live WS session later. Prefix-less symbols fall back to the offline classifier + _resolve_fetch_symbol.
- Live re-backfill after .TO fix: skipped_data 67->33 (remainder = truly delisted e.g. AH + open windows), CA realized_return_5d 0->8, with_entry 969->981.
- Known/benign: WS pseudo-symbols (A1, B1, CACHE1, cash/gold products) have no exchange prefix + no yahoo listing -> fetch-skip, no analytics (not real holdings to score). RR=Richtech (NASDAQ) and GLD (gold proxy) resolve bare.
- Tests: +10 (test_data_router_resolve 4, test_wealthsimple_symbol 6 incl dual-listed T). pytest 360 pass/3 skip, ruff clean, imports clean.

## 2026-06-11 - signal_history backfill: recover NULL entry_price (follow-on)
- Follow-on: legacy rows with NULL entry_price are now recoverable — rows_needing_backfill no longer filters on entry_price; signal_backfill derives entry from the signal-date close (offset 0) and persists it via NEW signals_repo.set_entry_price. Live run: with_entry 760->969, realized_return_5d 41->248, written=695. Remaining NULL/empty = delisted symbols (data_router fetch fail, e.g. XEQT/$VFV) + windows not yet closed (21d+). pytest 350 pass.

## 2026-06-11 - security: redact API keys from data-source exception messages
- Vuln: adapters passing credentials as URL query params leaked the key into httpx exception strings (full request URL embeds `apikey=`/`token=`). Those strings flow into SourceUnavailable -> logs + data_source_reliability error fields. Confirmed live: a twelve_data SHOP.TO 404 printed `apikey=<live-key>`.
- Fix (single chokepoint, base.py): NEW redact_secrets() masks apikey|api_key|api_token|access_token|access_key|token|secret query values via regex. Applied at every `{e}` raise site in the 6 credential-bearing adapters: twelve_data, alphavantage, eodhd, finnhub, stooq, massive. Keyless adapters (yfinance/coingecko/binance/coinbase/frankfurter/kraken/openerapi) untouched; tiingo safe (Authorization header, not query param).
- Verified: real SHOP.TO 404 now shows `apikey=<redacted>` (KEY LEAKED: False); AAPL quote unaffected; helper unit-tested on all 5 param forms incl camelCase apiKey; ruff clean; all 7 files import clean.
- TWELVE_DATA_KEY rotated by user + verified working (AAPL/USDCAD live OK). twelve_data was 0/16 in reliability window on the old key.

## 2026-06-11 - routing: drop tiingo from ca_equity chains (reliability)
- Root cause of tiingo's 28.6% reliability: it was in the ca_equity quote+history chains but maps `.TO -> SYM-CA`, which 404s (no TSX coverage on free/standard tier). Every CA fallthrough = guaranteed-miss call + skewed metric. Removed _tiingo from both ca_equity chains in data_router.py (_quote_chain_base, _history_chain_base); stays in us_equity (works for US). yf/twelve/eodhd cover CA.
- Not changed (verified by live probe, not actually broken): eodhd 0/2 = free-tier 20 calls/day quota exhaustion (SHOP.TO works on fresh quota, strong TSX); massive 0/3 = us_equity last-resort deep fallback (only hit when 5 providers ahead fail) + by-design is_tsx() reject — not in CA chain. twelve_data 0/16 was a genuinely bad key (rotated + fixed separately).
- pytest 363 pass/3 skip, ruff clean.

## 2026-06-11 - refactor: pathfinder unifications U1/U5/U6 (behavior-preserving)
- U1 DataSource base helpers: base.py gains to_float/pct_normalize (verified behavior-compatible with the per-adapter _f/_pct/_pct100) + fetch_json (GET+raise+json, with redact_secrets baked in -> CENTRALIZES the commit-4f3f0d8 security fix, does not regress it). Converted alphavantage/eodhd/finnhub/twelve_data to use them (~20 copy-paste http blocks collapsed). Deliberately skipped period-map merge (twelve_data uses bar-count outputsize, others lookback days - NOT identical) and bars/quote parser merges (field-mapping varies). NEW tests/test_data_source_base.py (17). Live: 4 adapters OK; SHOP.TO 404 still -> apikey=<redacted>, KEY LEAKED: False.
- U5 Telegram notifier: NEW notifications/telegram.py send()+push(); alerts._push_telegram is now `from notifications.telegram import send as _push_telegram` (zero signature change -> run_alerts/signal_change_detector unchanged); event_dispatcher + scheduler session-expired now call push(). main.py bespoke online-ping + standalone scripts left (different shapes/processes). NEW tests/test_telegram_notifier.py (4).
- U6 backtest_core: NEW backtest_core.py with sharpe/sortino/max_drawdown/cagr/annualize_factor extracted VERBATIM from the verified line-equivalent copies. backtest.py + skill_backtest.py import them as their private aliases (_sharpe/_cagr/_max_drawdown/_max_dd/_annualize/_sortino) - all 7 alias identities == core confirmed. backtest_stats ndarray drawdown + per-engine bar caches left (different contract / mutable state). NEW tests/test_backtest_core.py (9, locks formulas). Engine smoke: buy_hold 20% ret / -12.04% DD correct.
- Verify: pytest 393 pass/3 skip, ruff clean, 14 new tests. Adversarial review (3 dims x verify): 2 findings, both FALSE_POSITIVE (confirmed=0). Both prior fixes preserved: redact_secrets wired via fetch_json; tiingo absent from ca_equity chains (=0). Net -109 lines.

## 2026-06-11 - security+refactor: U4a news fetchers through fetch_json (extends 4f3f0d8)
- Gap found by post-refactor re-hunt: news._fetch_finnhub/_fetch_eodhd hand-rolled httpx.get+raise_for_status (no redaction), and _fetch_chain logged str(e)[:200] into source_stats + _LOG.warning -> a finnhub/eodhd apikey leaked into data_source_reliability, the SAME class as 4f3f0d8 but in news.py (outside the adapter set the original fix covered).
- Fix: both fetchers now call base.fetch_json(name="news:finnhub"|"news:eodhd", default=[]) -> redact_secrets applies; raises SourceUnavailable (redacted) which _fetch_chain already catches (contract unchanged: raise on transport fail, [] = no news). Dropped news.py httpx import.
- Verified: pytest 397 pass/3 skip, ruff clean; NEW tests/test_news_redaction.py (4: finnhub/eodhd error->redacted, parse rows, no-key->[]); live finnhub=244 + eodhd=50 articles, chain=50 (behavior preserved).
- Skipped U1b (keyless adapters / yfinance _f) deliberately: keyless = no secret to leak, pure cosmetic dedup; deferred (handoff in 04b). Period-dicts re-confirmed spec=True (kline-limit vs outputsize vs days) - not mergeable.
