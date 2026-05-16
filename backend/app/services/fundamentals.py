import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date as date_type
import yfinance as yf

_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 6 * 3600  # 6 hours
_MAX_WORKERS = 8  # yfinance.info hits Yahoo HTTP, ~8 parallel safe


def _cached(symbol: str, fetch_fn) -> dict:
    entry = _cache.get(symbol)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    result = fetch_fn(symbol)
    _cache[symbol] = (result, time.time())
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


def _fetch_one(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        earnings_date = None
        try:
            cal = ticker.calendar
            if cal is not None and not cal.empty:
                row = cal.T.iloc[0] if hasattr(cal, "T") else cal.iloc[0]
                val = row.get("Earnings Date") if isinstance(row, dict) else None
                if val is None and "Earnings Date" in cal.index:
                    val = cal.loc["Earnings Date"]
                if val is not None:
                    if hasattr(val, "iloc"):
                        val = val.iloc[0]
                    if hasattr(val, "strftime"):
                        earnings_date = val.strftime("%Y-%m-%d")
                    else:
                        earnings_date = str(val)
        except Exception:
            pass

        return {
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
            "analyst_target_price": info.get("targetMeanPrice"),
            "analyst_recommendation": info.get("recommendationKey"),
            "institutional_ownership": info.get("heldPercentInstitutions"),
            "insider_ownership": info.get("heldPercentInsiders"),
            "short_interest": info.get("shortPercentOfFloat"),
            "beta": info.get("beta"),
        }
    except Exception as e:
        print(f"[fundamentals] {symbol}: {type(e).__name__}: {e}")
        return {}


def get_fundamentals(symbols: list[str]) -> dict[str, dict]:
    """Parallel-fetch uncached symbols. yfinance.info is HTTP-bound — threads
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
_HISTORY_TTL = 12 * 3600  # 12h — reported quarters don't change


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
            out.append({
                "quarter": q,
                "eps_actual": float(eps_actual) if eps_actual is not None else None,
                "eps_estimate": float(eps_estimate) if eps_estimate is not None else None,
                "eps_difference": float(row.get("epsDifference")) if row.get("epsDifference") is not None else None,
                "surprise_pct": float(surprise) * 100 if surprise is not None else None,
                "outcome": outcome,
            })
        return out
    except Exception as e:
        print(f"[earnings_history] {symbol}: {type(e).__name__}: {e}")
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
_EM_TTL = 7200  # 2h — options prices change but not rapidly


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
        if days_to < 0 or days_to > 60:   # only flag upcoming earnings within 60 days
            return {}

        ticker = yf.Ticker(symbol)
        expiries = ticker.options          # list of "YYYY-MM-DD" strings
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
