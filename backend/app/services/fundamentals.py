import json
import time
import urllib.request

import httpx
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date as date_type
import yfinance as yf

from app.services import cache_layer
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.fundamentals")


_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 6 * 3600  # 6 hours
_MAX_WORKERS = 8  # yfinance.info hits Yahoo HTTP, ~8 parallel safe
_L2_NAMESPACE = "fundamentals"


def _cached(symbol: str, fetch_fn) -> dict:
    """Two-tier cache: L1 in-process dict, L2 diskcache (cross-process)."""
    entry = _cache.get(symbol)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    # L2 lookup
    l2 = cache_layer.cache_get(_L2_NAMESPACE, symbol)
    if l2:
        _cache[symbol] = (l2, time.time())
        return l2
    result = fetch_fn(symbol)
    _cache[symbol] = (result, time.time())
    if result:
        cache_layer.cache_set(_L2_NAMESPACE, symbol, result, _CACHE_TTL)
    return result


def _dividend_growth_streak(ticker: yf.Ticker) -> int:
    try:
        hist = ticker.dividends
        if hist is None or hist.empty:
            return 0
        hist.index = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index
        annual = hist.resample("YE").sum()
        annual = annual[annual > 0]
        if len(annual) < 2:
            return 0
        streak = 0
        values = annual.values[::-1]
        for i in range(len(values) - 1):
            if values[i] >= values[i + 1]:
                streak += 1
            else:
                break
        return streak
    except Exception:
        return 0


def _coerce_date_str(val) -> str | None:
    """Normalize a yfinance calendar value (date / Timestamp / list / Series)
    to a YYYY-MM-DD string. Returns None when unparseable."""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    if val is None:
        return None
    if hasattr(val, "iloc"):
        try:
            val = val.iloc[0]
        except Exception:
            return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    return str(val)[:10]


def _fetch_one(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        earnings_date = None
        ex_dividend_date = None
        dividend_pay_date = None
        try:
            cal = ticker.calendar
            if isinstance(cal, dict):
                earnings_date = _coerce_date_str(cal.get("Earnings Date"))
                ex_dividend_date = _coerce_date_str(cal.get("Ex-Dividend Date"))
                dividend_pay_date = _coerce_date_str(cal.get("Dividend Date"))
            elif cal is not None and not getattr(cal, "empty", True):
                idx = getattr(cal, "index", [])
                if "Earnings Date" in idx:
                    earnings_date = _coerce_date_str(cal.loc["Earnings Date"])
                if "Ex-Dividend Date" in idx:
                    ex_dividend_date = _coerce_date_str(cal.loc["Ex-Dividend Date"])
                if "Dividend Date" in idx:
                    dividend_pay_date = _coerce_date_str(cal.loc["Dividend Date"])
        except Exception:
            _LOG.debug("suppressed exception", exc_info=True)

        return {
            "name": info.get("longName") or info.get("shortName"),
            "quote_type": info.get("quoteType"),
            "currency": info.get("currency"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "eps_ttm": info.get("trailingEps"),
            "eps_growth_yoy": info.get("earningsGrowth"),
            "revenue_growth_yoy": info.get("revenueGrowth"),
            "profit_margin": info.get("profitMargins"),
            "dividend_yield": info.get("dividendYield"),
            "payout_ratio": info.get("payoutRatio"),
            "dividend_growth_streak": _dividend_growth_streak(ticker),
            "market_cap": info.get("marketCap"),
            "earnings_date": earnings_date,
            "ex_dividend_date": ex_dividend_date,
            "dividend_pay_date": dividend_pay_date,
            "analyst_target_price": info.get("targetMeanPrice"),
            "analyst_recommendation": info.get("recommendationKey"),
            "institutional_ownership": info.get("heldPercentInstitutions"),
            "insider_ownership": info.get("heldPercentInsiders"),
            "short_interest": info.get("shortPercentOfFloat"),
            "beta": info.get("beta"),
        }
    except Exception as e:
        _LOG.warning(f"[fundamentals] {symbol}: {type(e).__name__}: {e}")
        return {}


def get_fundamentals(symbols: list[str]) -> dict[str, dict]:
    """Parallel-fetch uncached symbols. yfinance.info is HTTP-bound - threads
    overlap network latency. Cached symbols return instantly.
    """
    now = time.time()
    out: dict[str, dict] = {}
    to_fetch: list[str] = []
    for sym in symbols:
        entry = _cache.get(sym)
        if entry and (now - entry[1]) < _CACHE_TTL:
            out[sym] = entry[0]
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return out

    workers = min(_MAX_WORKERS, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_fetch_one, to_fetch))
    for sym, result in zip(to_fetch, results):
        _cache[sym] = (result, time.time())
        out[sym] = result
    return out


_HISTORY_CACHE: dict[str, tuple[list, float]] = {}
_HISTORY_TTL = 12 * 3600  # 12h - reported quarters don't change


def _fetch_earnings_history_one(symbol: str, quarters: int) -> list[dict]:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.earnings_history
        if df is None or df.empty:
            return []
        df = df.tail(quarters).iloc[::-1]
        out = []
        for idx, row in df.iterrows():
            q = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
            eps_actual = row.get("epsActual")
            eps_estimate = row.get("epsEstimate")
            surprise = row.get("surprisePercent")
            if eps_actual is None or eps_estimate is None:
                continue
            if eps_actual > eps_estimate:
                outcome = "beat"
            elif eps_actual < eps_estimate:
                outcome = "miss"
            else:
                outcome = "meet"
            out.append(
                {
                    "quarter": q,
                    "eps_actual": float(eps_actual) if eps_actual is not None else None,
                    "eps_estimate": float(eps_estimate) if eps_estimate is not None else None,
                    "eps_difference": float(row.get("epsDifference")) if row.get("epsDifference") is not None else None,
                    "surprise_pct": float(surprise) * 100 if surprise is not None else None,
                    "outcome": outcome,
                }
            )
        return out
    except Exception as e:
        _LOG.warning(f"[earnings_history] {symbol}: {type(e).__name__}: {e}")
        return []


def get_earnings_history(symbols: list[str], quarters: int = 4) -> dict[str, list[dict]]:
    """Last N quarters of EPS estimate/actual/surprise per ticker. Cached 12h."""
    quarters = max(1, min(quarters, 12))
    now = time.time()
    out: dict[str, list[dict]] = {}
    to_fetch: list[str] = []
    for sym in symbols:
        key = f"{sym}:{quarters}"
        entry = _HISTORY_CACHE.get(key)
        if entry and (now - entry[1]) < _HISTORY_TTL:
            out[sym] = entry[0]
        else:
            to_fetch.append(sym)
    if not to_fetch:
        return out
    workers = min(_MAX_WORKERS, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda s: _fetch_earnings_history_one(s, quarters), to_fetch))
    for sym, result in zip(to_fetch, results):
        _HISTORY_CACHE[f"{sym}:{quarters}"] = (result, time.time())
        out[sym] = result
    return out


_EM_CACHE: dict[str, tuple[dict, float]] = {}
_EM_TTL = 7200  # 2h - options prices change but not rapidly


def _fetch_expected_move(symbol: str, earnings_date_str: str | None, current_price: float | None) -> dict:
    """Options-implied expected move around next earnings.

    Returns: {expected_move_pct, expected_move_dollars, days_to_earnings, expiry_used}
    """
    if not earnings_date_str or not current_price or current_price <= 0:
        return {}
    try:
        earnings_dt = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        today = date_type.today()
        days_to = (earnings_dt - today).days
        if days_to < 0 or days_to > 60:  # only flag upcoming earnings within 60 days
            return {}

        ticker = yf.Ticker(symbol)
        expiries = ticker.options  # list of "YYYY-MM-DD" strings
        if not expiries:
            return {}

        # Find nearest expiry on or after earnings date
        target = earnings_date_str
        post = [e for e in expiries if e >= target]
        expiry = post[0] if post else expiries[-1]

        chain = ticker.option_chain(expiry)
        calls = chain.calls
        puts = chain.puts
        if calls.empty or puts.empty:
            return {}

        # ATM strike = closest to current price
        strikes = calls["strike"].tolist()
        atm = min(strikes, key=lambda x: abs(x - current_price))

        call_row = calls[calls["strike"] == atm]
        put_row = puts[puts["strike"] == atm]
        if call_row.empty or put_row.empty:
            return {}

        call_price = float(call_row["lastPrice"].iloc[0])
        put_price = float(put_row["lastPrice"].iloc[0])
        straddle = call_price + put_price
        move_pct = round(straddle / current_price * 100, 1)
        move_dollars = round(straddle, 2)

        return {
            "expected_move_pct": move_pct,
            "expected_move_dollars": move_dollars,
            "days_to_earnings": days_to,
            "earnings_expiry": expiry,
        }
    except Exception:
        return {}


_INSIDER_TTL = 6 * 3600  # 6h - insider filings update infrequently
_insider_cache: dict[str, tuple[dict, float]] = {}


def get_insider_activity(symbol: str) -> dict:
    """
    Insider transactions + top institutional holders for a symbol.

    Uses yfinance (free, no key). Returns:
    - recent_transactions: last 10 insider buys/sells with name, shares, value
    - top_holders: top 5 institutional holders with % held
    - insider_buy_sell_ratio: buys / (buys + sells) over last 6 months
    - net_insider_signal: BULLISH / BEARISH / NEUTRAL
    Cached 6h.
    """
    symbol = symbol.upper()
    entry = _insider_cache.get(symbol)
    if entry and time.time() - entry[1] < _INSIDER_TTL:
        return entry[0]

    result: dict = {
        "symbol": symbol,
        "recent_transactions": [],
        "top_holders": [],
        "insider_buy_sell_ratio": None,
        "net_insider_signal": "NEUTRAL",
    }

    try:
        ticker = yf.Ticker(symbol)

        # ── Insider transactions ──────────────────────────────────────────
        try:
            tx = ticker.insider_transactions
            if tx is not None and not tx.empty:
                buys = 0
                sells = 0
                rows = []
                for _, row in tx.head(20).iterrows():
                    txn_type = str(row.get("Transaction") or "")
                    shares = int(row.get("Shares") or 0)
                    value = float(row.get("Value") or 0)
                    name = str(row.get("Insider") or "")
                    title = str(row.get("Position") or "")
                    date_val = row.get("Start Date") or row.get("Date")
                    date_str = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)
                    is_buy = any(kw in txn_type.lower() for kw in ("purchase", "buy", "acquisition"))
                    is_sell = any(kw in txn_type.lower() for kw in ("sale", "sell", "disposition"))
                    if is_buy:
                        buys += 1
                    elif is_sell:
                        sells += 1
                    rows.append(
                        {
                            "date": date_str,
                            "name": name,
                            "title": title,
                            "type": ("BUY" if is_buy else "SELL" if is_sell else txn_type),
                            "shares": shares,
                            "value_usd": round(value, 2),
                        }
                    )
                result["recent_transactions"] = rows[:10]
                total = buys + sells
                if total > 0:
                    ratio = round(buys / total, 2)
                    result["insider_buy_sell_ratio"] = ratio
                    if ratio >= 0.6:
                        result["net_insider_signal"] = "BULLISH"
                    elif ratio <= 0.3:
                        result["net_insider_signal"] = "BEARISH"
        except Exception:
            _LOG.debug("suppressed exception", exc_info=True)

        # ── Institutional holders ─────────────────────────────────────────
        try:
            holders = ticker.institutional_holders
            if holders is not None and not holders.empty:
                top: list[dict] = []
                for _, row in holders.head(5).iterrows():
                    name = str(row.get("Holder") or "")
                    shares = int(row.get("Shares") or 0)
                    pct = float(row.get("% Out") or 0)
                    top.append(
                        {
                            "holder": name,
                            "shares": shares,
                            "pct_held": round(pct * 100, 2),
                        }
                    )
                result["top_holders"] = top
        except Exception:
            _LOG.debug("suppressed exception", exc_info=True)

    except Exception as e:
        result["error"] = str(e)

    _insider_cache[symbol] = (result, time.time())
    return result


def get_earnings_expected_moves(
    symbols: list[str],
    fundamentals: dict[str, dict],
    technicals: dict[str, dict],
) -> dict[str, dict]:
    """Expected move for symbols with earnings in next 60 days. Cached 2h."""
    result: dict[str, dict] = {}
    for sym in symbols:
        cache_key = f"em:{sym}"
        entry = _EM_CACHE.get(cache_key)
        if entry and time.time() - entry[1] < _EM_TTL:
            result[sym] = entry[0]
            continue
        fund = fundamentals.get(sym) or {}
        tech = technicals.get(sym) or {}
        em = _fetch_expected_move(
            sym,
            fund.get("earnings_date"),
            tech.get("current_price"),
        )
        _EM_CACHE[cache_key] = (em, time.time())
        result[sym] = em
    return result


# ── SEC EDGAR XBRL (free, no key) ────────────────────────────────────────────

_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SEC_HEADERS = {"User-Agent": "aifolimizer/1.0 (open-source portfolio analytics)"}
_CIK_MAP: dict[str, str] = {}  # ticker → zero-padded CIK; session-scoped
_SEC_CACHE: dict[str, tuple[dict, float]] = {}
_SEC_TTL = 24 * 3600  # EDGAR filings don't change intraday


def _load_cik_map() -> dict[str, str]:
    """Fetch SEC ticker→CIK mapping once per process lifetime (~300 KB JSON)."""
    global _CIK_MAP
    if _CIK_MAP:
        return _CIK_MAP
    try:
        # httpx not urllib: www.sec.gov's Akamai WAF 403s urllib's fingerprint
        # (data.sec.gov used by _fetch_facts is more permissive). See lessons.md.
        resp = httpx.get(_SEC_TICKERS_URL, headers=_SEC_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.values():
            sym = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if sym:
                _CIK_MAP[sym] = cik
    except Exception as exc:
        _LOG.warning(f"[sec] CIK map load failed: {exc}")
    return _CIK_MAP


def _extract_annual_series(facts: dict, concept: str, unit: str = "USD") -> list[dict]:
    """Pull annual 10-K values for a US-GAAP concept from an XBRL facts blob."""
    try:
        entries = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {}).get(unit, [])
        annual = [e for e in entries if e.get("form") == "10-K" and e.get("fp") == "FY"]
        by_year: dict[int, dict] = {}
        for e in annual:
            end = e.get("end", "")
            year = int(end[:4]) if len(end) >= 4 else 0
            filed = e.get("filed", "")
            if year and (year not in by_year or filed > by_year[year].get("filed", "")):
                by_year[year] = {"year": year, "value": e.get("val")}
        return sorted(by_year.values(), key=lambda x: x["year"])[-4:]
    except Exception:
        return []


def _series_cagr(series: list[dict]) -> float | None:
    """Annualised growth rate from first to last data point in a series."""
    if len(series) < 2:
        return None
    first = series[0].get("value") or 0
    last = series[-1].get("value") or 0
    years = series[-1]["year"] - series[0]["year"]
    if first <= 0 or years <= 0:
        return None
    return round((last / first) ** (1 / years) - 1, 4)


def _series_trend(series: list[dict]) -> str:
    if len(series) < 2:
        return "insufficient_data"
    v0 = series[-2].get("value") or 0
    v1 = series[-1].get("value") or 0
    if v1 > v0 * 1.05:
        return "improving"
    if v1 < v0 * 0.95:
        return "declining"
    return "stable"


def _fetch_facts(symbol: str) -> dict | None:
    """Fetch the SEC XBRL companyfacts blob for a US ticker. None on failure.

    Cached 24 h under the raw symbol so get_sec_financials and the DCF
    cash-flow extractor share one network round-trip.
    """
    symbol = symbol.upper()
    if "." in symbol:
        return None
    key = f"__facts__{symbol}"
    entry = _SEC_CACHE.get(key)
    if entry and time.time() - entry[1] < _SEC_TTL:
        return entry[0]
    cik = _load_cik_map().get(symbol)
    if not cik:
        return None
    try:
        url = _SEC_FACTS_URL.format(cik=cik)
        req = urllib.request.Request(url, headers=_SEC_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            facts = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _LOG.warning(f"[sec] {symbol}: {exc}")
        return None
    _SEC_CACHE[key] = (facts, time.time())
    return facts


def get_sec_cashflow(symbol: str) -> dict:
    """SEC EDGAR XBRL: annual operating cash flow, capex, and free cash flow.

    free_cash_flow = operating_cash_flow - |capex|. Returns up to 4 fiscal
    years plus FCF CAGR. Empty dict for non-US tickers or on fetch failure.
    Feeds the deterministic DCF model.
    """
    facts = _fetch_facts(symbol)
    if not facts:
        return {}
    ocf = _extract_annual_series(facts, "NetCashProvidedByUsedInOperatingActivities") or _extract_annual_series(
        facts, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"
    )
    capex = _extract_annual_series(facts, "PaymentsToAcquirePropertyPlantAndEquipment")
    capex_by_year = {e["year"]: abs(e.get("value") or 0) for e in capex}
    fcf = []
    for e in ocf:
        yr = e["year"]
        o = e.get("value") or 0
        c = capex_by_year.get(yr, 0)
        fcf.append({"year": yr, "value": o - c})
    return {
        "symbol": symbol.upper(),
        "source": "SEC EDGAR XBRL",
        "operating_cash_flow_annual": ocf,
        "capex_annual": capex,
        "free_cash_flow_annual": fcf,
        "fcf_cagr": _series_cagr(fcf),
    }


def get_sec_financials(symbol: str) -> dict:
    """SEC EDGAR XBRL: annual revenue, net income, EPS for last 4 fiscal years.

    Supplements yfinance fundamentals with authoritative multi-year trends.
    Returns empty dict for non-US tickers (e.g. .TO) or on fetch failure.
    Cached 24 h.
    """
    symbol = symbol.upper()
    if "." in symbol:
        return {}
    entry = _SEC_CACHE.get(symbol)
    if entry and time.time() - entry[1] < _SEC_TTL:
        return entry[0]

    facts = _fetch_facts(symbol)
    if not facts:
        return {}

    revenue = (
        _extract_annual_series(facts, "Revenues")
        or _extract_annual_series(
            facts,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        )
        or _extract_annual_series(facts, "SalesRevenueNet")
    )
    net_income = _extract_annual_series(facts, "NetIncomeLoss")
    eps = _extract_annual_series(facts, "EarningsPerShareBasic", unit="USD/shares") or _extract_annual_series(
        facts, "EarningsPerShareDiluted", unit="USD/shares"
    )

    result = {
        "symbol": symbol,
        "source": "SEC EDGAR XBRL",
        "revenue_annual": revenue,
        "net_income_annual": net_income,
        "eps_annual": eps,
        "revenue_cagr_3yr": _series_cagr(revenue),
        "income_cagr_3yr": _series_cagr(net_income),
        "revenue_trend": _series_trend(revenue),
        "income_trend": _series_trend(net_income),
    }
    _SEC_CACHE[symbol] = (result, time.time())
    return result
