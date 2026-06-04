"""
Wealthsimple client using the ws-api package (maintained unofficial GraphQL client).

Flow:
  - login(email, password) — if MFA required raises OTPRequiredException; we cache email/password
    and return {needs_otp: True, session_id}
  - verify_otp(session_id, otp) — re-call login with otp_answer to complete auth
  - get_positions(session_id, account_id) — uses authenticated WSAPISession

Active sessions live in server RAM. The WSAPISession token is also persisted
to ~/.aifolimizer/ws_session.json (mode 0600) so a backend restart can resume
without prompting for credentials + OTP again. Password is never persisted.

Logs that previously echoed account IDs, balances, and P&L values to stdout are
now gated behind WS_DEBUG=1 — default-off matches the project's PII rule.
"""

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

from ws_api import WealthsimpleAPI, OTPRequiredException, LoginFailedException, WSAPISession

from app.models.portfolio import Account, UserContext
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.wealthsimple")


def _resolve_token_ttl_hours() -> int:
    """Absolute ceiling on persisted-session age before forced re-MFA.

    Override via WS_TOKEN_TTL_HOURS env var. Default 336h (14d) balances
    unattended scheduled-task convenience against stolen-laptop blast
    radius. ws-api auto-rotates the access token via _persist_session
    inside this window; WS-side revocation (password change, manual
    logout) still kicks in immediately on the next call regardless of
    this ceiling. Min clamped to 1h, max to 720h (30d).
    """
    raw = os.environ.get("WS_TOKEN_TTL_HOURS", "").strip()
    if not raw:
        return 336
    try:
        n = int(raw)
    except ValueError:
        return 336
    return max(1, min(720, n))


_TOKEN_TTL_HOURS = _resolve_token_ttl_hours()
_PENDING_TTL_SECONDS = 300  # OTP must be entered within 5 minutes of starting login

# {session_id: {state: "pending"|"authed", session: WSAPISession?, email, password?, profile, accounts_raw, expires_at}}
_sessions: dict[str, dict[str, Any]] = {}
# Last email seen on an authed flow. ws-api's auto-refresh path calls
# persist_session_fct WITHOUT a username, so _persist_session falls back to
# this to still write the rotated token (otherwise rotation is silently lost
# and the next restore dies on the stale refresh_token → avoidable MFA).
_last_email: Optional[str] = None
_LAST_CLEANUP_TIME = 0
_CLEANUP_INTERVAL_SECONDS = 3600  # Clean up every hour

# Disk persistence — restores the authenticated session across backend restarts.
_PERSIST_FILE = Path.home() / ".aifolimizer" / "ws_session.json"
_DEBUG = os.environ.get("WS_DEBUG", "").lower() in ("1", "true", "yes")


def _debug(msg: str) -> None:
    """PII-bearing log lines route through here so they're gated by WS_DEBUG."""
    if _DEBUG:
        print(msg, flush=True)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: temp-file in same dir, fsync, os.replace.

    Without this, concurrent readers (MCP server, FastAPI backend, scheduled
    skill runs) can observe a half-written file mid-rotation and fall back
    to a fresh login. os.replace is atomic on both POSIX and Windows when
    src and dst are on the same filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    data = json.dumps(payload).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # chmod is a no-op on Windows but try-anyway is harmless


def _persist_session(session: "WSAPISession | str", email: Optional[str] = None) -> None:
    """Write the live WSAPISession to disk so a backend restart can resume.

    File mode is set to 0600 (owner read/write only). Password is never persisted —
    only the access token, which already has WS's own server-side expiry.
    Called by ws-api whenever the access token is refreshed.

    ws-api invokes this with ``self.session.to_json()`` — i.e. the first arg is
    already a JSON *string*, not a WSAPISession. (See check_oauth_token /
    login_internal in ws_api.wealthsimple_api.) Previously this assumed an
    object and called ``.to_json()`` on the str, raising AttributeError that was
    swallowed — so refreshed tokens were NEVER persisted and every restart
    reused the dead access token, forcing avoidable MFA. Accept both forms.

    ws-api's auto-refresh path calls this WITHOUT a username (email is None).
    The refresh-token grant rotates the refresh_token, so skipping that write
    leaves a stale (server-side-invalidated) refresh_token on disk and the next
    restore dies on it → MFA. Fall back to the last authed email, then to the
    email already on disk, and only skip if we genuinely have none.
    """
    if email is None:
        email = _last_email
    if email is None:
        try:
            email = json.loads(_PERSIST_FILE.read_text(encoding="utf-8")).get("email")
        except Exception:
            email = None
    if email is None:
        _LOG.warning("[WS] persist skipped: no email (token rotation may be lost)")
        return
    try:
        session_json = session if isinstance(session, str) else session.to_json()
        _atomic_write_json(
            _PERSIST_FILE,
            {
                "email": email,
                "session_json": session_json,
                "saved_utc": time.time(),
            },
        )
    except Exception as e:
        _LOG.warning(f"[WS] persist failed: {type(e).__name__}: {e}")


def _clear_persisted_session() -> None:
    try:
        _PERSIST_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def restore_session() -> Optional[str]:
    """Reconstruct a logged-in session from disk. Returns session_id or None.

    Skips restore if the file is missing, malformed, older than _TOKEN_TTL_HOURS,
    or if the stored token is no longer accepted by Wealthsimple.

    Default ceiling = 14d (override via WS_TOKEN_TTL_HOURS env var, range 1..720h).
    WS-side revocation still kicks in immediately on the next call regardless of
    this ceiling, so password change / manual logout invalidate the persisted
    session right away. The ceiling only bounds the local stolen-laptop window.
    """
    if not _PERSIST_FILE.exists():
        return None
    try:
        payload = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
        saved = float(payload.get("saved_utc", 0))
        if time.time() - saved > _TOKEN_TTL_HOURS * 3600:
            _clear_persisted_session()
            return None
        email = payload["email"]
        session = WSAPISession.from_json(payload["session_json"])
    except Exception as e:
        _LOG.warning(f"[WS] restore parse failed: {type(e).__name__}")
        _clear_persisted_session()
        return None
    try:
        return _finalize_session(session, email)["session_id"]
    except Exception as e:
        _LOG.warning(f"[WS] restore validate failed, forcing token refresh: {type(e).__name__}")

    # Force the OAuth refresh-token grant. ws-api's check_oauth_token only
    # auto-refreshes when its probe returns the exact message "Not Authorized.";
    # an expired-token GraphQL response surfaces as {"errors":[...]} with no
    # top-level "message", so ws-api re-raises instead of refreshing — and the
    # session dies at WS's server-side access-token life (~hours), far short of
    # our 14d ceiling, forcing needless MFA. Blanking access_token makes
    # check_oauth_token skip the probe and mint a fresh access token straight
    # from the (longer-lived) refresh_token, then persist it.
    try:
        session.access_token = None
        return _finalize_session(session, email)["session_id"]
    except Exception as e:
        # Refresh genuinely failed (refresh_token expired/revoked, or WS down).
        # Keep the file: TTL + parse paths already prune dead/corrupt sessions,
        # and a real re-login overwrites it. Deleting here would only widen the
        # window where a transient outage forces an avoidable MFA.
        _LOG.warning(f"[WS] restore refresh failed (token kept): {type(e).__name__}")
        return None


def _new_session_id() -> str:
    return str(uuid.uuid4())


def _cleanup_expired_sessions() -> None:
    """Remove expired authed sessions + stale pending OTP states."""
    global _LAST_CLEANUP_TIME
    now = datetime.utcnow()
    pending_cutoff = time.time() - _PENDING_TTL_SECONDS
    expired = []
    for sid, sess in _sessions.items():
        if sess.get("state") == "authed":
            if now > sess.get("expires_at", now):
                expired.append(sid)
        elif sess.get("state") == "pending":
            if sess.get("pending_started", 0) < pending_cutoff:
                expired.append(sid)
    for sid in expired:
        _sessions.pop(sid, None)
    _LAST_CLEANUP_TIME = time.time()


def get_session(session_id: str) -> Optional[dict]:
    global _LAST_CLEANUP_TIME
    # Periodic cleanup trigger
    if time.time() - _LAST_CLEANUP_TIME > _CLEANUP_INTERVAL_SECONDS:
        _cleanup_expired_sessions()

    session = _sessions.get(session_id)
    if not session:
        return None
    if session.get("state") == "pending":
        return session
    if datetime.utcnow() > session.get("expires_at", datetime.utcnow()):
        _sessions.pop(session_id, None)
        return None
    return session


def _noop_persist(_sess: Any, _uname: Optional[str] = None) -> None:
    """Retained for callers that explicitly want no persistence."""
    pass


_NON_INVESTMENT_TYPES = {"PORTFOLIO_LINE_OF_CREDIT", "LINE_OF_CREDIT", "MORTGAGE", "LOAN", "CREDIT_CARD"}


def _is_investment_account(acc: dict) -> bool:
    raw = str(acc.get("unifiedAccountType") or acc.get("type") or acc.get("account_type") or "").upper()
    return not any(skip in raw for skip in _NON_INVESTMENT_TYPES)


def _build_profile(accounts_raw: list[dict]) -> UserContext:
    accounts: list[Account] = []
    for acc in accounts_raw:
        if not _is_investment_account(acc):
            continue
        acc_type = _detect_account_type(acc)
        currency = str(acc.get("currency") or acc.get("base_currency") or "CAD").upper()
        cash = _money(acc.get("cash") or acc.get("available_to_trade") or acc.get("available_balance") or 0)
        nlv = _nested(acc, "financials", "currentCombined", "netLiquidationValue")
        market_value = _money(
            acc.get("market_value") or acc.get("net_liquidation_value") or acc.get("total_value") or nlv or 0
        )
        accounts.append(
            Account(
                type=acc_type,
                currency=currency,
                cash_balance=cash,
                invested_value=market_value,
            )
        )

    total_cash = sum(a.cash_balance for a in accounts)
    total_invested = sum(a.invested_value for a in accounts)
    account_types = list({a.type for a in accounts})

    return UserContext(
        accounts=accounts,
        total_cash=total_cash,
        total_invested=total_invested,
        account_types=account_types,
    )


def _detect_account_type(account: dict) -> str:
    raw = str(
        account.get("unifiedAccountType")
        or account.get("type")
        or account.get("account_type")
        or account.get("accountType")
        or account.get("category")
        or ""
    ).upper()
    if "TFSA" in raw:
        return "TFSA"
    if "RRSP" in raw:
        return "RRSP"
    if "RESP" in raw:
        return "RESP"
    if "LIRA" in raw:
        return "LIRA"
    if "FHSA" in raw:
        return "FHSA"
    if "CRYPTO" in raw:
        return "Crypto"
    if "NON_REGISTERED" in raw or "NONREG" in raw or "PERSONAL" in raw:
        return "Non-Reg"
    if "CASH" in raw:
        return "Cash"
    return raw or "Unknown"


def _money(value: Any) -> float:
    """Normalize money to dollars. Handles dict {amount}, strings, and cents (large integers)."""
    if value is None:
        return 0.0
    if isinstance(value, dict):
        inner = value.get("amount")
        if inner is None:
            inner = value.get("value")
        if inner is None:
            inner = value.get("cents")
        return _money(inner if inner is not None else 0)
    try:
        raw = float(str(value).replace(",", "").replace("$", ""))
    except Exception:
        return 0.0
    # ws-api position amounts come back in cents (e.g. 23111194 = $231,111.94).
    # Heuristic: integer value >= 1,000,000 likely cents.
    if abs(raw) >= 1_000_000 and float(raw).is_integer():
        return raw / 100.0
    return raw


def _nested(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def login(email: str, password: str) -> dict:
    """Synchronous — ws-api is a sync library. Called from async route via run_in_executor implicit (FastAPI threadpool)."""
    email = email.strip()
    if not email or not password:
        raise ValueError("Email and password are required")

    try:
        session: WSAPISession = WealthsimpleAPI.login(
            username=email,
            password=password,
            otp_answer=None,
            persist_session_fct=_persist_session,
        )
    except OTPRequiredException:
        session_id = _new_session_id()
        _sessions[session_id] = {
            "state": "pending",
            "email": email,
            "password": password,
            "pending_started": time.time(),
        }
        return {"needs_otp": True, "session_id": session_id}
    except LoginFailedException as e:
        raise ValueError(f"Wealthsimple login failed: {e}")

    return _finalize_session(session, email)


def verify_otp(session_id: str, otp: str) -> dict:
    pending = _sessions.get(session_id)
    if not pending or pending.get("state") != "pending":
        raise ValueError("Invalid or expired session")
    # Pending TTL — abandon any half-finished OTP flow older than 5 minutes
    if time.time() - pending.get("pending_started", 0) > _PENDING_TTL_SECONDS:
        _sessions.pop(session_id, None)
        raise ValueError("OTP timed out — start login again")

    email = pending.get("email")
    password = pending.get("password")
    if not email or not password:
        _sessions.pop(session_id, None)
        raise ValueError("Session state lost — start login again")

    try:
        session: WSAPISession = WealthsimpleAPI.login(
            username=email,
            password=password,
            otp_answer=otp.strip(),
            persist_session_fct=_persist_session,
        )
    except OTPRequiredException:
        raise ValueError("OTP code rejected — try again")
    except LoginFailedException as e:
        _sessions.pop(session_id, None)
        raise ValueError(f"Login failed: {e}")
    finally:
        # Drop plaintext password from RAM regardless of outcome.
        if session_id in _sessions:
            _sessions[session_id].pop("password", None)

    # Reuse the existing session_id when finalizing
    return _finalize_session(session, email, session_id=session_id)


def _finalize_session(session: WSAPISession, email: str, session_id: Optional[str] = None) -> dict:
    global _last_email
    _last_email = email
    sid = session_id or _new_session_id()
    ws = WealthsimpleAPI.from_token(session, persist_session_fct=_persist_session, username=email)

    try:
        accounts_raw = ws.get_accounts()
        if not isinstance(accounts_raw, list):
            _LOG.warning(f"[WS] get_accounts() returned non-list: {type(accounts_raw).__name__}")
            _debug(f"[WS] payload: {json.dumps(accounts_raw, default=str)[:500]}")
            accounts_raw = []
    except Exception as e:
        raise ValueError(f"Could not fetch Wealthsimple accounts: {e}")

    # Enrich accounts with cash + unrealized P&L from per-account API calls.
    # Each account needs `get_account_balances` + `get_account_unrealized_pnl`
    # — independent HTTPs, ~200ms each. Run them in a small pool so total
    # cost is roughly one account's latency rather than N sequential calls.
    investment_accounts = [
        acc for acc in accounts_raw if isinstance(acc, dict) and _is_investment_account(acc) and acc.get("id")
    ]

    # Hoist FX out of the per-account loop — FX cache is process-wide; calling
    # it once here avoids repeating the lookup if multiple accounts hold USD.
    cad_per_usd: float | None = None

    def _enrich_account(acc: dict) -> tuple[str, dict, float]:
        nonlocal cad_per_usd
        acc_id = str(acc.get("id") or "")
        acc_type = _detect_account_type(acc)
        acc_pnl = 0.0
        usd_cash_balance = 0.0
        if not acc.get("cash") and not acc.get("available_to_trade"):
            try:
                balances = ws.get_account_balances(acc_id)
                _debug(f"[WS] balances({acc_type}): {balances}")
                if isinstance(balances, dict):
                    cad_cash = float(balances.get("sec-c-cad") or 0)
                    usd_cash_balance = float(balances.get("sec-c-usd") or 0)
                    if usd_cash_balance > 0:
                        if cad_per_usd is None:
                            from app.services.market_data import (
                                _get_cad_per_usd,
                            )

                            cad_per_usd = _get_cad_per_usd()
                        total_cash_cad = round(cad_cash + usd_cash_balance * cad_per_usd, 2)
                    else:
                        total_cash_cad = cad_cash
                    acc["cash"] = total_cash_cad
                    _debug(f"[WS] cash loaded for {acc_type} (cad+usd_converted={total_cash_cad})")
            except Exception as e:
                _LOG.warning(f"[WS] balances failed {acc_type}: {type(e).__name__}")
        try:
            pnl = ws.get_account_unrealized_pnl(acc_id, "CAD")
            _debug(f"[WS] pnl({acc_type}): {pnl}")
            if isinstance(pnl, dict):
                acc_pnl = _money(pnl.get("amount") or 0)
                _debug(f"[WS] pnl_amt for {acc_type}: {acc_pnl}")
        except Exception as e:
            _LOG.warning(f"[WS] pnl failed {acc_type}: {type(e).__name__}")

        nlv = _money(_nested(acc, "financials", "currentCombined", "netLiquidationValue") or 0)
        entry = {
            "cash_balance": float(acc.get("cash") or 0),
            "usd_cash_balance": usd_cash_balance,
            "invested_value": nlv,
            "unrealized_pnl_cad": acc_pnl,
        }
        return acc_type, entry, acc_pnl

    total_unrealized_pnl_cad = 0.0
    per_account: dict[str, dict] = {}
    if investment_accounts:
        max_workers = min(8, len(investment_accounts))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for acc_type, entry, acc_pnl in pool.map(_enrich_account, investment_accounts):
                per_account[acc_type] = entry
                total_unrealized_pnl_cad += acc_pnl

    _debug(f"[WS] total_unrealized_pnl_cad={total_unrealized_pnl_cad}")

    # Identity-level lifetime net deposits + simple return (account-wide P&L
    # incl. cash interest, dividends, realized gains — NOT just unrealized).
    net_deposits_cad = 0.0
    simple_return_pct = None
    try:
        fin = ws.get_identity_current_financials("CAD")
        if isinstance(fin, dict):
            nd = fin.get("netDeposits") or {}
            if isinstance(nd, dict):
                net_deposits_cad = _money(nd.get("amount") or 0)
            sr = fin.get("simpleReturns") or fin.get("simpleReturn")
            if isinstance(sr, dict):
                rate = sr.get("rate")
                if rate is not None:
                    simple_return_pct = float(rate) * 100
        _debug(f"[WS] net_deposits_cad={net_deposits_cad} simple_return_pct={simple_return_pct}")
    except Exception as e:
        _LOG.warning(f"[WS] identity financials failed: {type(e).__name__}: {e}")

    profile = _build_profile(accounts_raw)
    _debug(f"[WS] profile cash/invested: {[(a.cash_balance, a.invested_value) for a in profile.accounts]}")

    _sessions[sid] = {
        "state": "authed",
        "session": session,
        "email": email,
        "profile": profile,
        "accounts_raw": accounts_raw,
        "unrealized_pnl_cad": total_unrealized_pnl_cad,
        "net_deposits_cad": net_deposits_cad,
        "simple_return_pct": simple_return_pct,
        "per_account": per_account,
        "expires_at": datetime.utcnow() + timedelta(hours=_TOKEN_TTL_HOURS),
    }
    return {"session_id": sid, "profile": profile.model_dump()}


def _fetch_identity_positions(ws, currency: str = "CAD") -> list[dict]:
    """Single GraphQL call that returns positions across all accounts."""
    try:
        identity_id = ws.get_token_info().get("identity_canonical_id")
        result = ws.do_graphql_query(
            "FetchIdentityPositions",
            {
                "identityId": identity_id,
                "currency": currency,
                "first": 250,
                "filter": {"securityIds": None},
                "includeAccountData": True,
                "includeSecurity": True,
                "includeOneDayReturnsBaseline": True,
            },
            "identity.financials.current.positions.edges",
            "array",
            load_all_pages=True,
        )
        if isinstance(result, list):
            return [_normalize_node(r) for r in result]
    except Exception as e:
        _LOG.warning(f"[WS] FetchIdentityPositions error: {e}")
    return []


def get_positions(session_id: str, account_id: str) -> list[dict]:
    """Returns positions for a single account (filters identity-level positions)."""
    session = get_session(session_id)
    if not session or session.get("state") != "authed":
        raise ValueError("Session expired — please log in again")

    ws_session: WSAPISession = session["session"]
    email = session["email"]
    accounts_raw: list[dict] = session.get("accounts_raw", [])

    target_id = _resolve_account_id(accounts_raw, account_id)
    if not target_id:
        raise ValueError(f"Account '{account_id}' not found")

    ws = WealthsimpleAPI.from_token(ws_session, persist_session_fct=_noop_persist, username=email)
    all_positions = _fetch_identity_positions(ws)
    filtered = [p for p in all_positions if _position_account_id(p) == target_id]
    _debug(f"[WS] get_positions: {len(all_positions)} total, {len(filtered)} filtered")
    return [_to_position_dict(p) for p in filtered if isinstance(p, dict)]


def _position_account_id(pos: dict) -> str:
    if not isinstance(pos, dict):
        return ""
    acc = pos.get("account") or pos.get("accountInfo") or {}
    if isinstance(acc, dict):
        for key in ("id", "account_id", "uuid"):
            if acc.get(key):
                return str(acc[key])
    for key in ("account_id", "accountId"):
        if pos.get(key):
            return str(pos[key])
    return ""


def _normalize_node(item: Any) -> dict:
    if isinstance(item, dict) and isinstance(item.get("node"), dict):
        return item["node"]
    return item if isinstance(item, dict) else {}


def _resolve_account_id(accounts_raw: list[dict], account_id: str) -> str:
    if not account_id:
        if accounts_raw:
            return str(accounts_raw[0].get("id") or accounts_raw[0].get("account_id") or "")
        return ""

    # Try exact id match first
    for acc in accounts_raw:
        for key in ("id", "account_id", "uuid", "canonical_id"):
            if str(acc.get(key, "")) == account_id:
                return str(acc.get("id") or acc.get("account_id") or "")

    # Try by account type label
    upper = account_id.upper()
    for acc in accounts_raw:
        if _detect_account_type(acc).upper() == upper:
            return str(acc.get("id") or acc.get("account_id") or "")

    return ""


def _extract_currency(raw: Any, default: str = "CAD") -> str:
    """Pull currency code from a WS money dict, e.g. {'amount': 646.68, 'currency': 'USD'}."""
    if isinstance(raw, dict):
        return str(raw.get("currency") or raw.get("currencyCode") or default).upper()
    return default


def _to_position_dict(item: dict) -> dict:
    """Convert ws-api position node into the shape market_data.enrich expects."""
    if "legs" in item and isinstance(item["legs"], list) and item["legs"]:
        leg = item["legs"][0]
        item = {**leg, **{k: v for k, v in item.items() if k not in leg}}

    security = item.get("security") or item.get("stock") or item.get("asset") or {}
    stock = security.get("stock") if isinstance(security, dict) else {}
    stock = stock or {}

    symbol = (
        item.get("symbol")
        or (security.get("symbol") if isinstance(security, dict) else "")
        or stock.get("symbol")
        or item.get("ticker")
        or ""
    )
    symbol = str(symbol).upper().replace("TSX:", "").replace("NASDAQ:", "").replace("NYSE:", "")

    name = (
        stock.get("name") or (security.get("name") if isinstance(security, dict) else "") or item.get("name") or symbol
    )

    try:
        quantity = float(item.get("quantity") or item.get("shares") or item.get("stock_quantity") or 0)
    except Exception:
        quantity = 0.0

    mv_raw = item.get("totalValue") or item.get("marketValue") or item.get("market_value") or item.get("value") or 0
    market_currency = _extract_currency(mv_raw)
    market_value = _money(mv_raw)

    ap_raw = (
        item.get("averagePrice")
        or item.get("marketAveragePrice")
        or item.get("average_price")
        or item.get("cost_basis")
        or 0
    )
    avg_price = _money(ap_raw)

    bv_raw = item.get("bookValue") or item.get("marketBookValue") or item.get("book_value") or 0
    book_currency = _extract_currency(bv_raw, default=market_currency)
    book_value = _money(bv_raw)

    if book_value == 0 and avg_price > 0 and quantity > 0:
        book_value = avg_price * quantity
        book_currency = _extract_currency(ap_raw, default=market_currency)

    sec_type = ""
    if isinstance(security, dict):
        sec_type = str(security.get("securityType") or security.get("type") or "").upper()

    return {
        "security": {
            "symbol": symbol,
            "name": name,
            "type": sec_type or "EQUITY",
        },
        "quantity": quantity,
        "book_value": {"amount": book_value, "currency": book_currency},
        "market_value": {"amount": market_value, "currency": market_currency},
    }


def get_all_positions(session_id: str) -> list[dict]:
    """Return all positions across investment accounts in one GraphQL call."""
    session = get_session(session_id)
    if not session or session.get("state") != "authed":
        raise ValueError("Session expired — please log in again")

    ws_session: WSAPISession = session["session"]
    email = session["email"]
    accounts_raw: list[dict] = session.get("accounts_raw", [])

    # IDs of accounts we want to include (skip credit / loan accounts)
    investment_ids = {str(acc.get("id") or "") for acc in accounts_raw if _is_investment_account(acc) and acc.get("id")}

    ws = WealthsimpleAPI.from_token(ws_session, persist_session_fct=_noop_persist, username=email)
    raw_positions = _fetch_identity_positions(ws)

    if investment_ids:
        raw_positions = [
            p for p in raw_positions if not _position_account_id(p) or _position_account_id(p) in investment_ids
        ]

    _debug(f"[WS] get_all_positions: {len(raw_positions)} positions across {len(investment_ids)} accounts")
    return [_to_position_dict(p) for p in raw_positions if isinstance(p, dict)]


def get_all_cash(session_id: str) -> float:
    session = get_session(session_id)
    if not session:
        return 0.0
    profile = session.get("profile")
    if not profile:
        return 0.0
    return sum(a.cash_balance for a in profile.accounts)


async def shutdown() -> None:
    _sessions.clear()
