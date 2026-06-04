# Setup

From a fresh `git clone` to Claude answering *"get my profile"* with your real data. Plan for about ten minutes if you only want stock research, twenty if you're wiring up the live Wealthsimple portfolio and Docker.

This guide assumes you're comfortable with a terminal and Python. If you'd rather have every term spelled out, the condensed version in the [README](../README.md#quick-start) is shorter, and most commands here are safe to copy verbatim.

---

## How the pieces fit

Three processes talk to each other, all on localhost:

```
   You, in Claude  ───────────►  Claude Code / Desktop (your Pro sub)
                                        │  speaks MCP
                                        ▼
                              backend/mcp_server.py     ← the 102 tools live here
                                        │  calls Python services
                                        ▼
        Wealthsimple · yfinance · FRED · CoinGecko · …   (free, mostly keyless)
```

Nothing is deployed anywhere. The only thing that leaves your machine is the prompt Claude sends to Anthropic — ticker symbols and percentages, never dollar amounts or credentials. The reasoning behind that split is in [Privacy](../README.md#privacy).

---

## Decide what you actually need first

You don't have to set up everything. Pick a row and skip the rest — you can always come back and add a piece later.

| You want | You need | You can skip |
|---|---|---|
| **Ticker research only** — fundamentals, technicals, earnings, macro, bull/bear theses | Steps 1, 4, 6, 7 | Wealthsimple, Docker, Telegram |
| **Your live portfolio** — allocation, risk, rebalance, tax-loss | Every step + WS credentials (Step 3) | Docker is still optional |
| **Long history + cross-process cache** | The above, plus Step 2 (Docker) | — |
| **Hands-off nightly skills** | The above, plus the [automation runbook](../scripts/AUTOMATION.md) | — |

The fastest path is research-only: no broker, no Docker, no keys. Install, register the server, start asking.

---

## Prerequisites

| Tool | Why | Required? |
|---|---|---|
| Python 3.12 or 3.13 | Runs the backend and the MCP server. Pinned below 3.14 — a transitive dependency breaks there. | Yes |
| Claude Code CLI **or** Claude Desktop (Pro) | Where you actually talk to the tools. | Yes |
| Wealthsimple account | Live portfolio sync. | Portfolio path only |
| Docker Desktop | Postgres for history, Redis for shared cache. | Optional |
| git + an editor | — | Yes |

Quick check: `python --version` should report `3.12.x` or `3.13.x`.

---

## 1. Install the backend

Create an isolated environment inside `backend/` and pull the dependencies.

<details open>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```
</details>

<details>
<summary><b>macOS / Linux / WSL (bash)</b></summary>

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
```
</details>

Your prompt should now carry a `(.venv)` prefix. You'll re-activate the same way at the start of every future session.

---

## 2. Postgres + Redis (optional)

Skip this and the app keeps all its state in JSONL files under `~/.aifolimizer/` — everything works, you just get less history. If you do want it, Compose won't start without a Postgres password file, so create that first.

<details open>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
New-Item -ItemType Directory -Force ..\.secrets | Out-Null
python -c "import secrets; open('../.secrets/pg_password.txt','w').write(secrets.token_hex(24))"
cd .. ; docker compose up -d ; cd backend
```
</details>

<details>
<summary><b>macOS / Linux (bash)</b></summary>

```bash
cd .. && mkdir -p .secrets && openssl rand -hex 24 > .secrets/pg_password.txt && chmod 600 .secrets/pg_password.txt
docker compose up -d && cd backend
```
</details>

`docker compose ps` should show `postgres` and `redis` healthy.

---

## 3. Wealthsimple credentials (portfolio path)

Copy the template into a local `.env`. Note the path — it goes in **`backend/.env`**, not the repo root. That trips people up.

```bash
cp ../.env.example .env        # PowerShell: Copy-Item ..\.env.example .env
```

Open it and set the two values that matter to start:

```bash
WS_EMAIL=you@example.com
WS_PASSWORD=your_wealthsimple_password
```

Everything else in the file is optional — the [cheat sheet](#env-vars-worth-knowing) below covers what's there. `WS_PASSWORD` loads into memory only; it's never written to disk and never reaches any LLM.

Doing research only? Leave the login blank. The portfolio tools simply return nothing, and every public-market tool still works.

---

## 4. Claude permissions and hooks (recommended)

Without this you'll see more "allow this tool?" prompts — functionally identical, just chattier.

```bash
cd ..
cp .claude/settings.example.json .claude/settings.json
```

Edit the copy and replace `<REPO_ROOT>` and `<USER_HOME>` with absolute paths. If there's a `hooks` block you don't recognize, delete it — that's personal Obsidian/sync tooling, not something the project needs. Then `cd backend` again.

Both `.claude/settings.json` and `.mcp.json` are gitignored because they hold machine-specific absolute paths; the `*.example.json` files are the templates.

---

## 5. First Wealthsimple login (portfolio path)

A one-time interactive login that captures and caches your tokens. You'll only repeat it when Wealthsimple forces re-auth, roughly every two weeks.

```bash
python mcp_login.py            # venv active
```

Enter the OTP when prompted. Tokens land in `~/.aifolimizer/ws_session.json` — outside the repo, `0600` on POSIX and NTFS-restricted on Windows. Backend restarts now resume without asking for a code.

---

## 6. Start the backend

```bash
python run.py                  # serves http://127.0.0.1:8000
```

This is the FastAPI side — REST testing plus the shared session store. The MCP server (`mcp_server.py`) is a separate process that Claude launches itself, which is the next step. Leave this window running.

---

## 7. Register the MCP server with Claude

Tell Claude where the tool server lives, using absolute paths.

<details open>
<summary><b>Claude Code CLI — one command</b></summary>

```bash
# Windows
claude mcp add aifolimizer "<REPO>/backend/.venv/Scripts/python.exe" "<REPO>/backend/mcp_server.py"
# macOS / Linux
claude mcp add aifolimizer "<REPO>/backend/.venv/bin/python" "<REPO>/backend/mcp_server.py"
```
</details>

<details>
<summary><b>Config file (or Claude Desktop)</b></summary>

Copy `.mcp.example.json` to `.mcp.json` and replace `<REPO_ROOT>` (use double backslashes on Windows: `C:\\Users\\you\\...`). For Claude Desktop, edit `claude_desktop_config.json` instead — OS-specific paths are in the [FAQ](FAQ.md).
</details>

Restart Claude, then try one of these:

```
get my profile         →  account types and balances (portfolio path)
/daily-briefing        →  full morning digest
/stock-analysis NVDA   →  works research-only, no broker needed
```

If real data comes back, you're done.

---

## Env vars worth knowing

Only `WS_EMAIL` and `WS_PASSWORD` matter for the portfolio path. Everything else is opt-in. The fully annotated list lives in [`.env.example`](../.env.example).

| Variable | What it does | When you need it |
|---|---|---|
| `WS_EMAIL` / `WS_PASSWORD` | Wealthsimple login, kept in memory only | Live portfolio |
| `WS_TOKEN_TTL_HOURS` | Token lifetime, default `336` (14d), range `1–720`. Lower means more frequent MFA but a shorter stolen-laptop window | Tuning re-auth |
| `POSTGRES_DSN` / `REDIS_URL` | Point at your Docker infra; defaults already match `docker-compose.yml` | Using Docker |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Push alerts to Telegram | Alerts |
| `GITHUB_TOKEN`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `DASHSCOPE_API_KEY` | Opt-in free-LLM fallback when Claude Pro is unavailable. Off unless set, and noticeably lower quality | No Claude Pro |
| `FINNHUB_KEY`, `ALPHA_VANTAGE_KEY`, `TIINGO_KEY`, `EODHD_KEY`, `TWELVE_DATA_KEY` | Extra market-data adapters in the fallback chain | Want data redundancy |
| `SENTRY_DSN` (+ org/project) | Error tracking | Debugging |
| `WS_DEBUG`, `STRUCTURED_LOGS` | `1` enables verbose / JSON logs | Troubleshooting |

Setting a fallback-LLM key opts you into sending %-of-NAV prompts to that provider. Leave them blank to keep all inference on-machine. Dollars, account IDs, your name, and the WS token never go out either way.

---

## What's where

```
aifolimizer/
├── backend/
│   ├── mcp_server.py      the 102 MCP tools Claude calls
│   ├── run.py             FastAPI entry (REST + shared session store)
│   ├── mcp_login.py       one-time interactive WS login
│   ├── .env               your credentials (gitignored) — note: backend/, not root
│   └── app/
│       ├── services/      data + compute, 50+ modules — the actual logic
│       ├── api/           REST routes (ws.py, agents.py, ops.py)
│       ├── jobs/          nightly scheduler + RQ task queue
│       ├── db/ cache/     Postgres repositories · Redis client
│       └── models/        Pydantic schemas
├── .claude/
│   ├── skills/            25 analysis skills (the /slash-commands)
│   ├── context/           architecture.md · changes.md · lessons.md
│   └── settings.json      your Claude perms/hooks (gitignored; .example is the template)
├── docs/                  this guide, FAQ, sample outputs
├── scripts/AUTOMATION.md  nightly-skill runbook (Windows + POSIX)
├── docker-compose.yml     Postgres (TimescaleDB) + Redis
└── ~/.aifolimizer/        (outside the repo) cached WS tokens + JSONL state
```

State lives in two places: auth tokens and the JSONL history files sit under `~/.aifolimizer/`; when Docker is up, Postgres mirrors the history with more depth.

---

## When the first run misbehaves

| Symptom | Cause and fix |
|---|---|
| `docker compose up` exits immediately | Missing `.secrets/pg_password.txt` — run Step 2's generator first |
| Claude can't find the server | A relative path in `claude mcp add` — use absolute paths |
| `get my profile` returns nothing | `.env` landed in the repo root instead of `backend/`, or the credentials are blank |
| MFA prompt loops | Token expired — run `python mcp_login.py` again from `backend/` |
| First `/daily-briefing` takes ~30s | Cold cache fetching from upstream. The next call hits diskcache and returns in seconds |
| `claude mcp list` is slow (~5s) | Eager imports in `mcp_server.py` (pandas, yfinance, ta). Harmless, paid once per session |

The [FAQ](FAQ.md) goes deeper, and [SECURITY.md](../SECURITY.md) has the threat model if you're hardening the install.

---

## Sanity check (optional)

```bash
# from backend/, venv active
ruff check .
PYTHONPATH=. python -m pytest tests/ -q     # PowerShell: $env:PYTHONPATH="."; python -m pytest tests/ -q
```

---

## Where to go next

Run `/daily-briefing` or `/portfolio-health` to see the suite work end to end. To make the nightly skills run on their own, follow [scripts/AUTOMATION.md](../scripts/AUTOMATION.md). And if you want to understand how the data flows, [`.claude/context/architecture.md`](../.claude/context/architecture.md) is the map.
