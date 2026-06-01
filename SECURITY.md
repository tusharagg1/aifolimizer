# Security Policy

## Threat Model

aifolimizer is a **single-user, local-machine application**. It is not designed, hardened, or audited for:

- Multi-user / multi-tenant deployment
- Cloud or remote-server hosting
- Untrusted local users on the same OS account
- Internet-exposed services

The application **trusts the OS user** running it. Anyone with read access to the user's home directory and process memory can recover Wealthsimple session tokens, portfolio data, and any optional API keys. Protect the host accordingly.

## Reporting a Vulnerability

**Preferred:** open a [GitHub Private Vulnerability Report](https://github.com/tusharagg1/aifolimizer/security/advisories/new) — encrypted, ties to a tracking ID, no email required.

**Do NOT open public GitHub issues for security bugs.** Public disclosure before a fix is available puts other self-hosted users at risk.

Include in your report:

- Affected component / file / commit
- Reproduction steps
- Impact assessment
- Suggested mitigation (if any)

You will receive an acknowledgement within 7 days.

## Disclosure Timeline

- **Day 0**: Report received, acknowledged
- **Day 0–7**: Triage and severity assessment
- **Day 7–60**: Fix developed, tested, released
- **Day 90**: Coordinated public disclosure, regardless of fix status

Researchers acting in good faith within this timeline will be credited (if desired) in release notes.

## In Scope

- **Wealthsimple credential handling** — `.env` loading, OTP flow, session token storage at `~/.aifolimizer/ws_session.json`, file permissions
- **PII filter** (`backend/app/services/pii_filter.py`) — leakage of account IDs, emails, names, absolute dollar balances through MCP tool responses
- **LLM fallback prompts** — content sent to GitHub Models / Gemini / OpenRouter / Qwen when their API keys are set; specifically any inclusion of PII or absolute dollar figures
- **Postgres password file** — storage, permissions, accidental commit
- **MCP transport** — stdio framing, request handling, authentication assumptions
- **Secret-handling code paths** — logging, error messages, crash dumps, telemetry
- **Dependency vulnerabilities** in pinned versions of FastMCP, FastAPI, yfinance, ta, etc.

## Out of Scope

The following are upstream services or third-party software outside this project's control:

- **yfinance** data quality, availability, or upstream API behavior
- **FRED** (St. Louis Fed) public CSV API
- **CoinGecko** v3 public API
- **Anthropic Claude** — model behavior, prompt injection in returned data, Claude Code / Desktop transport
- **Wealthsimple** — the broker's own systems, authentication, app, or website
- **Operating system** vulnerabilities (Windows, macOS, Linux)
- Issues only reproducible on **modified forks**

Report upstream issues directly to the upstream vendor.

## Known Advisories (defense-in-depth notes)

| CVE | Package | Status | Mitigation |
|---|---|---|---|
| CVE-2025-69872 (CVSS 9.8) | `diskcache` ≤ 5.6.3 | No upstream patch as of writing | `cache_layer.py` forces `JSONDisk` serialization (no pickle on read) and tightens the cache directory to mode `0700` on POSIX. The threat model (single-user / local-machine) means a co-resident attacker is already trusted, but defense-in-depth blocks the gadget vector if the cache directory is ever shared. |

If you see other advisories surfaced by `pip-audit -r backend/requirements.txt`, evaluate against this threat model first — many remote-execution vectors require a network-exposed service this project does not run.

## Wealthsimple API Note

aifolimizer uses a **reverse-engineered, undocumented Wealthsimple API**. There is **no SLA, contract, or support relationship** with Wealthsimple. The API can change or break at any time, may violate Wealthsimple's terms of service depending on jurisdiction, and is used at your own risk. This is not a security issue in aifolimizer; it is an inherent property of the integration.

## Hardening Checklist for Self-Hosted Users

Before running aifolimizer on any machine that holds real brokerage credentials:

- [ ] **Full-disk encryption enabled** — BitLocker (Windows), FileVault (macOS), LUKS (Linux)
- [ ] **Dedicated OS user account** for aifolimizer; do not run as admin/root
- [ ] **Screen lock** with short idle timeout and password/biometric unlock
- [ ] **`.env` file permissions** restricted to owner only (`chmod 600 backend/.env` on Unix; equivalent ACL on Windows)
- [ ] **`~/.aifolimizer/ws_session.json`** confirmed at mode `0600` (owner-only)
- [ ] **Postgres password file** regenerated on first run; not the default
- [ ] **Dedicated Wealthsimple sub-account** with the minimum necessary permissions, separate from your primary login
- [ ] **2FA enabled on Wealthsimple** (mandatory; the OTP flow assumes it)
- [ ] **gitleaks** (or equivalent) configured as a pre-commit hook to block accidental secret commits
- [ ] **`.env`, `data/`, `~/.aifolimizer/`** confirmed in `.gitignore` and never staged
- [ ] **LLM fallback API keys left unset** unless you have read and accepted what is sent to those providers
- [ ] **Regular backups** of the OS user, with backup media itself encrypted
- [ ] **Host kept patched** — OS, Python runtime, Node runtime, project dependencies (`pip install -U`, `npm audit`)

If you cannot satisfy these, do not run aifolimizer against a real brokerage account.
