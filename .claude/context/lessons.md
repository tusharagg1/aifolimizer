# aifolimizer - Lessons

Append-only. Short rule + source incident per entry. Read at session startup.

---

## Skills / decision memory

- **Verdict-emitting skills MUST load prior decisions first and log their verdict after.** Each session re-deriving from scratch caused cross-session contradictions (QS/OKLO/NNE/BMNR/SOFI flipped between two same-day runs). Pattern: at start call `get_ticker_decision_history` + `get_ticker_reflection` (per-ticker) or `get_cross_ticker_lessons` (portfolio-level) and reconcile — don't silently flip; at end call `log_recommendation` (and `log_trade_decision` for a full BUY/SELL with levels). Gold-standard template: `adversarial-research` Stage 0 + Layer 5. *(Source: 2026-06-09 committee workflow contradicted prior break-even-vs-recycle verdicts; root cause = only 3/27 skills loaded prior decisions. Fixed: load+log wired into all verdict skills.)*

- **HIGH conviction label is anti-predictive on logged recs — never size up on it.** Over 411 scored recs HIGH won 22% (avg −6.8%) vs MED 63% (avg +4.2%); BUY won 18.5%, SELL/TRIM 70–100%. The engine already caps long-side conviction at MED (`recommendations.py` conviction-decouple) and bounds `win_prob` to a tagged heuristic — keep it until `calibrate_confidence_labels` proves HIGH>MED>LOW with ≥10pp spread. Note: `calibrate_confidence_labels`/`get_calibration_report` read `signal_history.win_prob` pairs (separate store), currently unpopulated → formal report stays `no_data`; rely on `score_recommendations` by_conviction until that pipeline is fed. *(Source: 2026-06-09 score_recommendations.)*

## Python

- **Local-variable scoping is whole-function.** If `name = ...` appears anywhere in function body, every earlier reference treated as local → `UnboundLocalError`. Scan whole function for assignments before referencing name. *(Source: `recommendations.py:407` referenced `em` before line 419 assigned it - crashed `_score_position` every position, emptied recs panel.)*

- **Cache failure modes.** When wrapping external call in cache, decide: store empty on failure (fast recovery, data gap for TTL) or skip caching on failure (retry next call). Pick deliberately. *(Source: `_ticker_meta` 5-min cache stores empty meta if yfinance fails - masks outages full 5 min.)*

- **Locks need double-check.** Using `asyncio.Lock` to dedupe concurrent fetches: re-check cache inside lock - another waiter may have populated while waiting. *(Source: `_get_portfolio` lock pattern.)*

## Cross-platform / packaging

- **`subprocess` text decode crashes on Windows non-ASCII output.** `subprocess.run(..., text=True)` decodes with the locale codec (cp1252 on Windows). Tools that emit emoji/box-drawing (e.g. `claude mcp list`) raise `UnicodeDecodeError` in the reader thread → `.stdout` becomes `None` → downstream `in`/iter `TypeError`. Always pass `encoding="utf-8", errors="replace"` and default `... or ""`. *(Source: `health_check.py` `_check_mcp_registered` crashed reading `claude mcp list`.)*

- **venv python path differs by platform.** POSIX = `.venv/bin/python`; Git-Bash on native Windows = `.venv/Scripts/python.exe`. A bash script hardcoding `bin/python` fails on Git-Bash. Resolve by checking both. *(Source: `setup.sh` first draft assumed `bin/`.)*

- **Shell scripts must be LF, or the shebang breaks on POSIX.** Git's autocrlf rewrites `*.sh` to CRLF → `#!/usr/bin/env bash\r` → "bad interpreter". systemd/launchd unit files same. Ship a `.gitattributes` with `*.sh text eol=lf` (+ `.service`/`.timer`/`.plist`). Set exec bit via `git update-index --chmod=+x` so POSIX clones get `100755`. *(Source: setup scripts authored on Windows.)*

- **`git clone` only carries committed files — uncommitted files vanish from a "fresh clone" test.** To e2e new uncommitted work, copy the working tree (`tar --exclude=.venv --exclude=.git`), not `git clone`. *(Source: first onboarding e2e cloned HEAD, setup.sh wasn't there → exit 127.)*

- **A fake-CLI shim on PATH only shields `bash command -v`, not Python `shutil.which`, on Windows.** Extensionless shim scripts are invisible to `shutil.which` (needs PATHEXT match), so Python subprocesses still hit the real binary. When testing a script that mutates global state, verify which lookup mechanism each consumer uses before trusting the shim. *(Source: onboarding e2e — setup.sh's `claude mcp add` hit the fake (safe), but health_check's `claude mcp list` hit the real CLI.)*

## Networking / external APIs

- **Gov/enterprise APIs behind Akamai reset plain-Python TLS (JA3 fingerprint block).** Symptom: `httpx`/`requests` get `WinError 10054 connection forcibly closed` or SSL handshake timeout, but system `curl` returns 200. Fix: fetch via `curl_cffi` with `impersonate="chrome"` (mimics browser TLS). Lazy-import + degrade gracefully so absence doesn't break server. *(Source: StatCan WDS `www150.statcan.gc.ca` — httpx reset every time, curl_cffi 200. BoC Valet via same httpx worked fine → host-side WAF, not client.)*

- **SEC: `www.sec.gov` 403s urllib but allows httpx; `data.sec.gov` allows both.** Same UA, different result by client + host. `www.sec.gov/files/company_tickers.json` (CIK map) rejected `urllib.request` with `403 Forbidden`; `httpx` with identical `User-Agent` returned 200. `data.sec.gov/api/xbrl/...` (companyfacts) tolerates urllib. Rule: hit SEC via httpx, not urllib. *(Source: `fundamentals._load_cik_map` silently failed → every CIK-dependent tool (EDGAR filings, DCF, sec_financials) got empty map.)*

## yfinance

- **Batch when possible.** `yf.download([syms], group_by="ticker", threads=True)` one HTTP regardless of symbol count. Serial loops easy bottleneck.

- **`group_by="ticker"` returns MultiIndex even for 1 symbol.** Slicer code must handle both single-level and MultiIndex columns. *(Source: technicals batch refactor - single-symbol path returned `'Close'` KeyError until `_slice_symbol` handled both.)*

- **`yf.Ticker(sym).info` is 1 HTTP call.** `.calendar` and `.dividends` each another. Three HTTP calls per symbol if touching all three.

## Frontend (React / Next.js) - ARCHIVED

> Frontend removed; project backend-only now (Claude Code / Claude Desktop drive analysis via MCP). Lessons kept in case dashboard revived.

- **AllocationChart sums must use `_cad` suffix.** Mixing native `market_value` (USD or CAD) with CAD total under-sizes USD slices ~1.38x. *(Source: pre-existing bug in `AllocationChart.tsx:20`.)*

- **`useEffect(() => () => abort, [])` interacts with strict mode.** Empty-deps cleanup runs at dev strict-mode unmount, aborts in-flight fetches. Verify second mount re-fires loaders.

- **`React.memo` only helps if parent passes stable prop refs.** Arrays/objects rebuilt every render bypass memo. For props from `setState(apiResponse)`, ref changes per fetch - memo skips re-renders when no fetch happened (e.g. countdown ticks).

- **SetState in effect body trips React 19 lint.** Use derived `now` state ticked by interval, compute remaining inline. *(Source: `CountdownLabel.tsx` first version.)*

## Workflow

- **Compile-clean ≠ working.** Import-check + tsc + lint pass without exercising code. Call changed function with realistic input before declaring done. *(Source: 9-step perf pass shipped clean lints; `em` UnboundLocalError caught only by one mock-portfolio invocation.)*

- **Pre-existing bugs surface during refactors.** Fresh integration tests (cache cleared, services restarted) expose latent bugs hidden behind cached responses. Budget time.

- **Skip skeletons on background refresh.** Set `loading=true` only when state null - prevents flicker on refresh while still showing skeleton on first load. Don't blanket-apply - account-switch needs skeleton.

## Cross-cutting

- **Shipped ≠ user-facing.** Backend feature returning data via MCP/REST but not on dashboard invisible to user. New field added, ask "does UI surface this?" before declaring done. *(Source: crowding score shipped May 16 via positioning.py + MCP, but PortfolioTable didn't render full day until audit caught gap.)*

- **Scheduling is part of feature.** `run_alerts.py` nobody runs is dead code. Same for crowding snapshots - without scheduler, regime-shift detector has nothing to detect. Ship scheduler artifacts in same PR as underlying logic. *(Source: alerts shipped May 16, no scheduler until May 17 audit.)*

- **Honest backtests need fees + walk-forward as default flags.** Zero-friction backtests overstate strategy returns 5-30%+ in regime-favorable lookbacks. Every strategy must accept `tx_cost_bps` and `walk_forward`.

- **Two-tier cache pattern.** L1 = in-process dict (hot path). L2 = diskcache (survives restarts + cross-process FastAPI ↔ MCP sharing). Never replace L1 with L2 - pickled SQLite reads 1000x slower. Always L1 → L2 → fetch.

- **Multi-process app = multiple cold starts.** MCP and FastAPI two Python processes with independent dict caches. In-process cache only helps process that fetched. Cross-process diskcache makes second process see first's work.

- **Verify isinstance/subclass claims with repro before writing into commit messages.** np.float64 IS subclass of float (issubclass(np.float64, float) is True). `isinstance(x, (int, float))` check accepts numpy scalars. *(Source: 8a3ac35 commit message claimed isinstance silently rejected np.float64 - false. New try-cast harmless superset, premise wrong.)*

- **Cross-currency invariants: read producer before writing "fix" math in consumer.** wealthsimple.py:369 stores `acc["cash"] = cad_cash + usd_cash * fx` (CAD-equivalent total) before per_account propagation. `cash_available_usd` RAW USD kept for per-currency display only - adding to `cash_available` double-counts. fa17419 added comment claiming wrong invariant; d7b6ffd weaponized comment to introduce high-severity double-count. e3a65d8 reverted both. Always trace producer → field semantics → all consumers before adding currency arithmetic. *(Source: cash_pct double-count saga, May 29 2026.)*

- **Caveat for self-correcting fixes: comment IS part of fix.** Wrong comment near math worse than no comment - actively misleads next reviewer/editor. Omit invariant claim or verify across all producer/consumer call sites before committing.

- **MCP server cold-start gates session tool exposure.** Eager top-level imports of heavy modules (yfinance, ta, pandas) in MCP server entry point can push cold start past Claude Code's handshake window. `claude mcp list` may still show ✓ Connected later, but tools never made it into session's tool schema → invocations fail with "tool not found." Fix: `_LazyModule` proxy defers `importlib.import_module` until first attribute access; tool bodies unchanged. *(Source: aifolimizer mcp_server.py 5.0s → 1.1s cold start, May 29 2026.)*

- **Pivot ≠ spot. Never infer current_price from pivot levels.** `get_technicals` pivots computed from PRIOR session H/L/C — NOT live quote. When `current_price` null (partial yfinance fetch / pre-market / data race), call `get_quote_with_source` for spot before producing any trade plan. *(Source: stock-analysis incident Jun 2026 — inferred spot from pivot, real spot ~9% lower after sector-gap event. Trade plan stale.)*

- **`claude mcp list` shows server health, not session schema availability.** Connected server can have zero tools exposed in current session if schema fetch raced startup. Verify with actual tool call, not list output.

- **Never destroy a credential on transient validation failure.** Token/session validators making live network calls must distinguish "provider rejected credential (revoke it)" from "call merely failed (keep it, retry later)". Deleting persisted token on any `Exception` turns 1-second network blip into forced re-auth/MFA. Default to keeping credential; let genuinely-revoked one fail naturally on next real call and re-auth overwrite it. *(Source: `wealthsimple.restore_session` cleared `ws_session.json` on any `_finalize_session` exception → recurring MFA re-entry despite valid 14-day token, Jun 2026.)*

- **A "validity probe" via fresh login is self-defeating for MFA-gated providers.** Full username+password login to check whether session still valid is call most likely to trigger MFA. Probe via saved token (restore) instead; if fresh login succeeds, persist its result rather than discarding. *(Source: `mfa_popup` "already valid" branch fresh-logged then dropped session without persisting, Jun 2026.)*

- **Match persist/callback signature to what library ACTUALLY passes, and never swallow persist failure silently.** ws-api calls `persist_session_fct(self.session.to_json(), username)` — first arg is JSON *string*, not session object. Our callback assumed object and called `.to_json()` on str → `AttributeError`, caught and logged at WARNING only. Net effect: with rotating single-use refresh tokens, every refresh minted new refresh token never written to disk, so next restart presented consumed token → forced MFA on ~hourly cadence. Root cause hid for months behind swallowed warning. Rule: verify callback arg types against caller's source; let persist failures be loud (they break durability silently). *(Source: `wealthsimple._persist_session` str/obj mismatch, Jun 2026.)*

- **Rotating refresh tokens are single-use — never test refresh against live token without throwaway clone.** Forcing refresh consumes on-disk refresh token server-side; if persist broken (or test doesn't save rotated token), live session destroyed and needs re-auth. Clone session file to temp path and point persist target there before exercising any refresh path. *(Source: burned live WS session mid-debug by force-refreshing against real file, Jun 2026.)*

## Strategy / Behavioral Analysis

- **Bias audit must not symmetrize all biases.** When original analysis cites concrete evidence (specific track-record patterns, cycle math, horizon-bounded math), abstract "recency anchor" critique should not silently soften verdict. Weigh evidence quality, don't treat both sides as equal.

- **Specific evidence beats abstract framework.** When two analyses contradict, weight one grounded in concrete data over one citing only general behavioral-finance framework names.

- **Plan flip between iterations damages trust more than single wrong call.** Restoring earlier verdict requires explicit acknowledgment + change log + lesson, never silent revert.

- **Behavioral guardrails must err harder toward mechanical discipline when user's revealed pattern is hold-and-hope.** Generic "don't sell at bottom" advice dangerous for hold-pattern investors. Mechanical sell rules beat nuanced "what if it recovers" hedging for that risk profile.

- **Self-learning: name specific bias dynamic, not generic principle.** "Audit overcorrected original verdict without re-weighing concrete evidence" useful. "Be consistent" not.

- **Repo files stay clean of session-specific data.** No tickers, no $ amounts, no holdings, no drawdown %, no balances, no account labels. Lessons capture process rules. Session context belongs in transient memory.
## WS token rotation: never skip persist on email=None (2026-06-04)
- **Symptom**: MFA re-prompt ~40min after valid session, despite S448 Bug 1/3/4 persistence fixes. Worsened by parallel MCP load (4-agent workflow).
- **Root cause**: `_persist_session(session, email=None)` had `if email is None: return`. ws-api's auto-refresh grant calls persist WITHOUT username, so every rotated refresh_token silently dropped. Disk kept old refresh_token; WS invalidates it server-side on rotation; next `restore_session` -> refresh fails -> MFA.
- **Why prior fixes missed it**: Bug 4 fixed str-vs-object crash in persist, but `email is None` guard then discarded the very refresh writes it rescued. Concurrency just rotated token faster.
- **Fix**: module-global `_last_email` set in `_finalize_session`; `_persist_session` falls back email -> `_last_email` -> on-disk email, skips only if all None.
- **Rule**: token-rotation persist callbacks must NOT depend on username arg library may omit. Persist session_json whenever any owner identity resolves. Dropped rotation write = dead session at access-token horizon, not refresh-token horizon.
- **Op note**: dropped rotation can't be revived — re-auth once (`mcp_login.py`) to seed fresh refresh_token, and restart long-running MCP process so patched module loads.
## Windows automation gotchas + scheduler coupling (2026-06-04)
- **RQ default `Worker` forks per job; `os.fork` absent on Windows** -> worker crashes every job, exits 0. Use `SimpleWorker` (in-process) when `not hasattr(os, "fork")`.
- **`New-ScheduledTaskTrigger -Once` + `.DaysOfWeek` is a silent no-op** (DaysOfWeek only honored by `-Weekly`/`-Daily`); plus a bounded `-RepetitionDuration` makes the task fire one window then stop. Omit duration for indefinite 30-min repetition. Symptom: task ran once on register day, NextRun empty forever after.
- **uvicorn `reload=True` under a non-interactive scheduled task** spawns a reloader child that can outlive the parent and orphan-hold the port (TCP entry shows a dead PID). Run services `reload=False` (single process); gate reload behind an env var for dev.
- **PowerShell 5.1 parses a BOM-less UTF-8 file as ANSI** -> a multibyte char (em-dash) shifts tokenization and breaks an unrelated later string ("missing terminator"). Keep `.ps1` ASCII or write with a BOM.
- **Windows console is cp1252**; `print()` of emoji raises UnicodeEncodeError. `sys.stdout.reconfigure(encoding="utf-8")` in scripts that emit unicode.
- **Nightly scheduler is coupled to the FastAPI lifespan** (`start_scheduler()` in main.py), NOT the MCP server process. If the backend process is down, zero nightly automation runs even though MCP tools still work. Keep the backend task alive (logon/boot trigger + restart) for self-learning loops to fire.
- **Notify cadence != check cadence.** A 30-min alert sweep is fine if the Telegram push is gated (per-day dedup + min-severity); the spam came from un-gated pushes and a crash-looping auth path, not the schedule itself.

## WS per-holding money is account-base CAD, not native (2026-06-04)
- **Symptom**: USD-listed holdings (UNH, INTU…) shown as CAD; per-account portfolio empty; inflated/garbage returns into skills.
- **Root causes**: (a) `_position_account_id` read `account` but WS FetchIdentityPositions uses `accounts` (list of {id}) → per-account filter dropped all 29 holdings. (b) `_to_position_dict` took currency from `totalValue.currency` which WS reports as CAD even for NYSE stocks; the authoritative native currency is `security.currency`. (c) `_load_portfolio` passed only 4 positional args to `enrich`, silently defaulting usd_cash_balance/net_deposits/simple_return → cash_available_usd always 0.
- **Fix**: read `accounts[0].id`; tag holdings from `security.currency` and divide CAD→native by current FX (enrich re-multiplies, so CAD totals/weights/return-% unaffected); pass the full enrich arg list.
- **Data limit**: WS position payload exposes cost basis ONLY in CAD at *historical* purchase FX (bookValue, averagePrice). Native USD cost (hence WS-app native return %) is NOT recoverable from this endpoint — only the live native quote (quoteV2.price) is. CAD return ≠ native return by the FX-since-purchase component.

## MCP hang = stale process + no async backstop + shared thread pool (2026-06-04)
- **Symptom**: get_profile/get_portfolio_analysis/get_personal_context hang forever, no error, interrupt then proceeds; restarting FastAPI "temporarily" helps.
- **Root cause**: ws_api calls `requests.request` with no timeout. The disk fix (30s patch) was correct but the *running* MCP server predated it — and Claude Code keeps the MCP server alive across chat sessions, so "every new session" reconnected to the same stale process with permanently-blocked worker threads. Worse, all WS calls ran on the shared `asyncio.to_thread` default executor, so a stall starved even zero-network tools (get_personal_context).
- **Fix**: dedicated `_WS_EXECUTOR` (4 workers) + `_ws_call()` with `asyncio.wait_for` ceiling. WS stall → bounded RuntimeError, isolated from other tools.
- **Rule**: any indefinite blocking I/O behind an MCP tool needs BOTH a transport timeout AND an async-layer `wait_for` backstop, and must not share the default to_thread pool with unrelated tools. Editing a .py does NOT reload a live MCP server — must reconnect/restart it.

## Duplicate MCP registration = the real cause of WS stalls/MFA
- BEFORE blaming token/refresh logic: check for >1 mcp_server.py process and for the server defined in BOTH .mcp.json and ~/.claude.json[projects][path][mcpServers]. Two registrations = two processes = token contention.
- A valid (unexpired) JWT + an MFA prompt is the signature of contention, not a dead session.

## WS token death = dropped rotation, not process duplication
- Before blaming "duplicate MCP instances": check process PARENT PIDs. .venv\Scripts\python.exe is a launcher stub that spawns base interpreter as a child and waits -> 2 python procs per server that die together. That is ONE logical server, not a contending duplicate. Kill the child; if the parent dies too with no respawn, it was the stub pair.
- The real token killer was persist_session_fct=_noop_persist on read paths: ws-api auto-refresh rotates the single-use refresh token but the noop persist discarded it. Any WS call that can trigger an internal refresh MUST persist the rotation, or the shared on-disk token dies.
- Genuine multi-session contention exists at the CHAT level (N Claude sessions, 1 token), fixed by serializing all WS calls through _PERSIST_LOCK + disk reload, not by killing processes.

## Never let an uncalibrated probability drive position sizing
- The `analyst_rec→win_prob` map (buy=0.62, sell=0.35) was ANTI-correlated with reality: analyst-buy names realized ~18% win, sell ~94%. Feeding it to Kelly levers into losers.
- Rule: gate Kelly on an empirical calibration verdict (HIGH>MED>LOW with ≥10pp spread); default `kelly_pct=None` until earned. A heuristic prob is for display, not sizing.
- Rule: when a confidence label exists, validate it against realized win-rate per bucket before trusting it. HIGH here was INVERTED (23.6% vs MED 74.1%) because it tracked bullish convergence = the losing BUY leg. Cap/decouple until calibration proves the ordering.
- Rule: trust-tier labels must gate on the SIGN of realized expectancy, not just sample size — a large negative-EV sample is evidence of a LOSING policy, not a milestone. Always verify the live forward store (353 sigs) over a stale committed report (103).

- Perf: `quant.correlation_matrix` was 42.5ms/call (every get_correlation_matrix + get_risk_suite) because it ran O(n²) pure-Python Pearson loops. Fixed with single vectorized `np.corrcoef` fast-path (equal-length series; ragged inputs fall back to old loop). Gain: 42.485 → 0.260 ms = 163x (-99.4
- Perf: quant.correlation_matrix was 42.5ms/call (every get_correlation_matrix + get_risk_suite) because it ran O(n^2) pure-Python Pearson loops. Fixed with single vectorized np.corrcoef fast-path (equal-length series; ragged inputs fall back to old loop). Gain: 42.485 -> 0.260 ms = 163x (-99.4%), output bit-identical incl. zero-variance->None.

- Bug: scheduled skills + MCP died fleet-wide because restore_session() deleted the shared ws_session.json on a SINGLE parse/read failure. Every cron script (run_alerts/maintenance/skill_fallback/daily_briefing) + FastAPI call it; under concurrent atomic-writes from N MCP servers, one transient unreadable read (rename window / AV lock / torn read) unlinked the token for ALL processes -> forced MFA cascade. Fix: 3x retry read + NEVER delete on read/parse failure (re-login overwrites anyway; keeping is strictly safer). Only TTL>14d still deletes. Mirrors the safe _load_cached_session pattern.

- Perf/arch: skills lagged/hung/failed on every MCP WS access because _load_portfolio made a LIVE Wealthsimple round-trip (get_positions + enrich) per call; N MCP servers + cron contended one single-use-token session through Cloudflare. Fix: cache enriched PortfolioResponse to shared L2 disk (PORTFOLIO_CACHE_TTL_S, default 90s) + in-process single-flight asyncio lock + 24h last-good fallback (WS down -> serve stale, no hang). WS now touched ~once/TTL/process instead of every call. PortfolioResponse carries no PII (symbols/values/weights only) so disk-caching is privacy-safe. NOTE: live MCP servers must be restarted to pick up the change (editing .py does not reload them).

- ROOT CAUSE (skills always lag/hang/fail on MCP): 3x print() to STDOUT in mcp_server.py corrupted the stdio JSON-RPC stream. stdout IS the MCP protocol channel; any non-JSON-RPC byte desyncs the framing -> Claude Code client can't parse responses -> every tool hangs/fails. The second-instance warning prints at startup of every 2nd+ server, so with multiple Claude windows each extra server's stream was broken from byte one. Fix: all print() -> file=sys.stderr. Verified via real mcp stdio client: parse error gone, initialize clean. Rule: NEVER write to stdout in a stdio MCP server — stderr or logging only. NOTE: running servers must be restarted to load the fix.
- Secondary stall (get_profile 90s then ERR): _ensure_session retries _load_cached_session twice; each _finalize_session holds cross-process _PERSIST_LOCK across a WS get_accounts() that can stall under Cloudflare throttle -> 45s lock timeout x2 = 90s fail. Only bites under concurrent establishes (multiple windows) + transient WS throttle. Lock is free at rest. Fix pending sign-off: hold _PERSIST_LOCK only for the disk token read, run get_accounts lock-free (mirror _run_ws design) to kill the convoy.

- Bug: log_recommendation raises ValueError unless conviction in {HIGH,MED,LOW} and action in {BUY,SELL,HOLD,ADD,TRIM,WATCH,NO_EDGE} (paper_trade.py _VALID_CONV/_VALID_ACTIONS). pre-trade-check passed conviction="filtered" -> threw on EVERY pass -> its forward track record was silently empty (weekly-mirror saw nothing). Fix: use "MED" (codebase neutral default; bulk path already coerces unknown->MED). Rule: any skill calling log_recommendation must use the valid enums; "discipline gate, no conviction" still maps to MED, not a custom string.

- Perf: enrich() cold load was slow because _ticker_meta re-fetched the heavy yf `.info` HTTP (currency+sector) per holding on every cold MCP process (L1-only per-process cache, lost on restart). Fixed by caching the static currency/sector fields to shared L2 disk (24h TTL); fast_info (price) stays live on the 5min L1. Measured AAPL: 828ms cold -> 101ms L2-warm (-88%/symbol). Prices/WS-currency freshness unchanged.

- Adding a new field to a cached payload (e.g. `published_ts` on news articles) does NOT retro-fit rows already in the L2/in-process cache — old rows lack the field and downstream readers see None until the entry expires or is invalidated. When a schema change must take effect immediately, call `data_cache.delete_*` / `invalidate_symbol` for affected keys (or bump the cache namespace), don't just rely on TTL. Surfaced as `_news_velocity` returning 30d=0 right after the news rewrite — fixed by clearing stale news rows.

- Perf/UX: get_profile hung for the full WS timeout on Cloudflare stalls (it was the only WS tool with no last-good fallback). Fixed: real-time fetch every call (user requires live sync) + last-good fallback flagged `_stale:true` only on live error/timeout, so it degrades to slightly-stale instead of stuck->kill->retry. No success-snapshot (stays real-time).
- Diag: "random session expiry" = aifolimizer-alerts (30min) + maintenance scheduled tasks each rotate the shared single-use WS refresh_token; concurrent with an interactive session => rotation race => one token invalidated => forced MFA. In-mem expires_at=14d is fiction (does not track WS server-side token life). Token-rotation core is hardened/high-risk — do NOT rewrite blind.

- Arch/expiry-fix: bg scheduled scripts (run_alerts every 30min, run_maintenance) were each calling restore_session -> rotating the shared single-use WS refresh_token, racing interactive sessions into random forced MFA. Fixed by adding app/services/portfolio_snapshot.py (shared L2 read of the snapshot mcp_server writes) and pointing both scripts at it instead of WS. Alerts stay real-time on PRICE via data_router.get_quotes_batch overlay (public tickers); HOLDINGS come from the cached WS snapshot. No bg process touches the WS token now.

- ROOT CAUSE (long-pending get_profile hang): market_data (yfinance->numpy/pandas C-ext) was imported LAZILY inside the concurrent account-enrich ThreadPool on the first get_profile of a cold MCP process. N enrich threads hit the first-ever import together -> convoyed on Python import lock behind a multi-minute cold numpy .pyd dlopen (Windows Defender scan + 3 MCP instances contending disk). Looked like a deadlock; effectively a hang until killed. Proven via py-spy: T14800 held import lock loading numpy multiarray, T31160 blocked on import lock in _enrich_account, T31944 blocked on its future.
- FIX: eager `import app.services.market_data` in mcp_server __main__ before mcp.run() — single-threaded, once at boot. Enrich-path lazy import becomes a warm sys.modules hit. Proven: cold import 3.30s one-time at startup; 8 concurrent enrich-path imports after warm = 1.0ms (was the convoy). Takes effect on NEXT MCP spawn.
- LESSON: never trigger a first-time heavy import (yfinance/numpy/pandas) inside a concurrent ThreadPool on a hot path — the import lock serializes the pool behind one cold C-ext dlopen. Warm such imports once at process startup.
