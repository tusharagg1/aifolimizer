# aifolimizer - Lessons

Append-only. Short rule + source incident per entry. Read at session startup.

---

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
