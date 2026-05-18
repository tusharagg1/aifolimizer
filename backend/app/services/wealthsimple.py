"""
Wealthsimple client using the ws-api package (maintained unofficial GraphQL client).

Flow:
  - login(email, password) — if MFA required raises OTPRequiredException; we cache email/password
    and return {needs_otp: True, session_id}
  - verify_otp(session_id, otp) — re-call login with otp_answer to complete auth
  - get_positions(session_id, account_id) — uses authenticated WSAPISession

Sessions and credentials live in server RAM only. Nothing persisted to disk.
"""

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Any

from ws_api import WealthsimpleAPI, OTPRequiredException, LoginFailedException, WSAPISession

from app.models.portfolio import Account, UserContext

_TOKEN_TTL_HOURS = 8

# {session_id: {state: "pending"|"authed", session: WSAPISession?, email, password?, profile, accounts_raw, expires_at}}
_sessions: dict[str, dict[str, Any]] = {}
_LAST_CLEANUP_TIME = 0
_CLEANUP_INTERVAL_SECONDS = 3600  # Clean up every hour


def _new_session_id() -> str:
    return str(uuid.uuid4())


def _cleanup_expired_sessions() -> None:
    """Remove expired sessions from memory. Called periodically."""
    global _LAST_CLEANUP_TIME
    now = datetime.utcnow()
    expired = [sid for sid, sess in _sessions.items() if sess.get("state") == "authed" and now > sess.get("expires_at", now)]
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
    pass


_NON_INVESTMENT_TYPES = {"PORTFOLIO_LINE_OF_CREDIT", "LINE_OF_CREDIT", "MORTGAGE", "LOAN", "CREDIT_CARD"}


def _is_investment_account(acc: dict) -> bool:
    raw = str(
        acc.get("unifiedAccountType")
        or acc.get("type")
        or acc.get("account_type")
        or ""
    ).upper()
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
            acc.get("market_value")
            or acc.get("net_liquidation_value")
            or acc.get("total_value")
            or nlv
            or 0
        )
        accounts.append(Account(
            type=acc_type,
            currency=currency,
            cash_balance=cash,
            invested_value=market_value,
        ))

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
            persist_session_fct=_noop_persist,
        )
    except OTPRequiredException:
        session_id = _new_session_id()
        _sessions[session_id] = {
            "state": "pending",
            "email": email,
            "password": password,
        }
        return {"needs_otp": True, "session_id": session_id}
    except LoginFailedException as e:
        raise ValueError(f"Wealthsimple login failed: {e}")

    return _finalize_session(session, email)


def verify_otp(session_id: str, otp: str) -> dict:
    pending = _sessions.get(session_id)
    if not pending or pending.get("state") != "pending":
        raise ValueError("Invalid or expired session")

    email = pending["email"]
    password = pending["password"]

    try:
        session: WSAPISession = WealthsimpleAPI.login(
            username=email,
            password=password,
            otp_answer=otp.strip(),
            persist_session_fct=_noop_persist,
        )
    except OTPRequiredException:
        raise ValueError("OTP code rejected — try again")
    except LoginFailedException as e:
        raise ValueError(f"Login failed: {e}")

    # Reuse the existing session_id when finalizing
    return _finalize_session(session, email, session_id=session_id)


def _finalize_session(session: WSAPISession, email: str, session_id: Optional[str] = None) -> dict:
    sid = session_id or _new_session_id()
    ws = WealthsimpleAPI.from_token(session, persist_session_fct=_noop_persist, username=email)

    try:
        accounts_raw = ws.get_accounts()
        if not isinstance(accounts_raw, list):
            print(f"[WS] get_accounts() returned non-list: {type(accounts_raw).__name__}")
            print(f"[WS] payload: {json.dumps(accounts_raw, default=str)[:500]}")
            accounts_raw = []
    except Exception as e:
        raise ValueError(f"Could not fetch Wealthsimple accounts: {e}")

    # Enrich accounts with cash + unrealized P&L from per-account API calls.
    # get_accounts() doesn't expose cash directly; get_account_balances returns
    # {"sec-c-cad": <amount>, ...} where "sec-c-cad" is the CAD cash balance.
    total_unrealized_pnl_cad = 0.0
    per_account: dict[str, dict] = {}  # keyed by account type label e.g. "TFSA"
    for acc in accounts_raw:
        if not isinstance(acc, dict) or not _is_investment_account(acc):
            continue
        acc_id = str(acc.get("id") or "")
        if not acc_id:
            continue
        acc_type = _detect_account_type(acc)
        acc_pnl = 0.0
        if not acc.get("cash") and not acc.get("available_to_trade"):
            try:
                balances = ws.get_account_balances(acc_id)
                print(f"[WS] balances({acc_id[:8]}): {balances}", flush=True)
                if isinstance(balances, dict):
                    cad_cash = float(balances.get("sec-c-cad") or 0)
                    usd_cash = float(balances.get("sec-c-usd") or 0)
                    if usd_cash > 0:
                        from app.services.market_data import _get_cad_per_usd
                        total_cash_cad = round(
                            cad_cash + usd_cash * _get_cad_per_usd(), 2
                        )
                    else:
                        total_cash_cad = cad_cash
                    acc["cash"] = total_cash_cad
                    print(
                        f"[WS] cash={total_cash_cad}"
                        f" (cad={cad_cash}, usd={usd_cash})"
                        f" acc={acc_id[:8]}",
                        flush=True,
                    )
            except Exception as e:
                print(f"[WS] balances failed {acc_id[:8]}: {e}", flush=True)
        try:
            pnl = ws.get_account_unrealized_pnl(acc_id, "CAD")
            print(f"[WS] pnl({acc_id[:8]}): {pnl}", flush=True)
            if isinstance(pnl, dict):
                acc_pnl = _money(pnl.get("amount") or 0)
                total_unrealized_pnl_cad += acc_pnl
                print(f"[WS] pnl_amt={acc_pnl} acc={acc_id[:8]}", flush=True)
        except Exception as e:
            print(f"[WS] pnl failed {acc_id[:8]}: {e}", flush=True)

        nlv = _money(
            _nested(acc, "financials", "currentCombined", "netLiquidationValue")
            or 0
        )
        per_account[acc_type] = {
            "cash_balance": float(acc.get("cash") or 0),
            "invested_value": nlv,
            "unrealized_pnl_cad": acc_pnl,
        }

    print(f"[WS] total_unrealized_pnl_cad={total_unrealized_pnl_cad}", flush=True)
    profile = _build_profile(accounts_raw)
    print(
        f"[WS] profile cash/invested:"
        f" {[(a.cash_balance, a.invested_value) for a in profile.accounts]}",
        flush=True,
    )

    _sessions[sid] = {
        "state": "authed",
        "session": session,
        "email": email,
        "profile": profile,
        "accounts_raw": accounts_raw,
        "unrealized_pnl_cad": total_unrealized_pnl_cad,
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
        print(f"[WS] FetchIdentityPositions error: {e}")
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
    print(f"[WS] get_positions: {len(all_positions)} total, {len(filtered)} for account {target_id}")
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
        stock.get("name")
        or (security.get("name") if isinstance(security, dict) else "")
        or item.get("name")
        or symbol
    )

    try:
        quantity = float(item.get("quantity") or item.get("shares") or item.get("stock_quantity") or 0)
    except Exception:
        quantity = 0.0

    mv_raw = (
        item.get("totalValue")
        or item.get("marketValue")
        or item.get("market_value")
        or item.get("value")
        or 0
    )
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

    bv_raw = (
        item.get("bookValue")
        or item.get("marketBookValue")
        or item.get("book_value")
        or 0
    )
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
    investment_ids = {
        str(acc.get("id") or "")
        for acc in accounts_raw
        if _is_investment_account(acc) and acc.get("id")
    }

    ws = WealthsimpleAPI.from_token(ws_session, persist_session_fct=_noop_persist, username=email)
    raw_positions = _fetch_identity_positions(ws)

    if investment_ids:
        raw_positions = [
            p for p in raw_positions
            if not _position_account_id(p) or _position_account_id(p) in investment_ids
        ]

    print(f"[WS] get_all_positions: {len(raw_positions)} positions across {len(investment_ids)} accounts")
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
