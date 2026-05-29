import csv
import io
import math
import time
import yfinance as yf
from app.models.portfolio import Position, PortfolioSummary, PortfolioResponse
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.market_data")


_FX_CACHE: tuple[float, float] | None = None  # (timestamp, cad_per_usd)
_FX_TTL = 300  # 5 min — forex moves during market hours

# Per-symbol price+sector cache. enrich() runs on every portfolio fetch and
# without this it makes one yf.Ticker(...).info HTTP call per holding in serial.
# 5 min TTL: matches FX freshness, beats the 10s portfolio cache by 30x.
_TICKER_CACHE: dict[str, tuple[dict, float]] = {}
_TICKER_TTL = 300


def _ticker_meta(symbol: str) -> dict:
    """Cached {currency, last_price, prev_close, sector} per symbol."""
    entry = _TICKER_CACHE.get(symbol)
    now = time.time()
    if entry and (now - entry[1]) < _TICKER_TTL:
        return entry[0]
    meta: dict = {
        "currency": None,
        "last_price": 0.0,
        "prev_close": 0.0,
        "sector": None,
    }
    try:
        ticker = yf.Ticker(symbol)
        fast_info = ticker.fast_info
        meta["last_price"] = float(fast_info.last_price or 0)
        meta["prev_close"] = float(fast_info.regular_market_previous_close or 0)
        try:
            info = ticker.info or {}
            yf_cur = str(info.get("currency") or "").upper()
            if yf_cur in ("USD", "CAD"):
                meta["currency"] = yf_cur
            meta["sector"] = info.get("sector") or info.get("category")
        except Exception:
            pass
    except Exception:
        pass
    _TICKER_CACHE[symbol] = (meta, now)
    return meta


def _get_cad_per_usd() -> float:
    """Live CAD/USD rate. Primary: yfinance USDCAD=X. Fallback: FRED DEXCAUS."""
    global _FX_CACHE
    now = time.time()
    if _FX_CACHE and now - _FX_CACHE[0] < _FX_TTL:
        return _FX_CACHE[1]

    # Primary: yfinance live tick
    try:
        rate = yf.Ticker("USDCAD=X").fast_info.last_price
        if rate and 1.0 < rate < 2.5:
            _FX_CACHE = (now, float(rate))
            return float(rate)
    except Exception:
        pass

    # Fallback: FRED DEXCAUS (CAD per USD, daily, slightly lagged)
    try:
        import httpx
        resp = httpx.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXCAUS",
            timeout=5.0,
        )
        rows = list(csv.reader(io.StringIO(resp.text)))
        for date, val in reversed(rows[1:]):
            try:
                v = float(val)
                if not math.isnan(v) and 1.0 < v < 2.5:
                    _FX_CACHE = (now, v)
                    return v
            except Exception:
                continue
    except Exception:
        pass

    return _FX_CACHE[1] if _FX_CACHE else 1.38  # use last known rate before pure fallback

_ASSET_CLASS_MAP = {
    "ETF": "etf",
    "EXCHANGE_TRADED_FUND": "etf",
    "EQUITY": "equity",
    "CRYPTOCURRENCY": "crypto",
    "MUTUAL_FUND": "etf",
    "FIXED_INCOME": "bond",
    "PRECIOUS_METAL": "commodity",
    "COMMODITY": "commodity",
}


def _classify_asset(security_type: str, symbol: str) -> str:
    if security_type:
        return _ASSET_CLASS_MAP.get(security_type.upper(), "equity")
    sym = symbol.upper()
    if any(sym.endswith(s) for s in [".TO", ".V"]):
        return "equity"
    return "equity"


def _infer_currency(symbol: str) -> str:
    """Infer currency from symbol suffix. TSX stocks end in .TO or .V."""
    sym = symbol.upper()
    if any(sym.endswith(s) for s in (".TO", ".V", ".TSX")):
        return "CAD"
    return "USD"


def enrich(
    raw_positions: list[dict],
    cash_balance: float,
    ws_account_total: float = 0.0,
    unrealized_pnl_cad: float = 0.0,
    usd_cash_balance: float = 0.0,
    net_deposits_cad: float = 0.0,
    simple_return_pct: float | None = None,
) -> PortfolioResponse:
    """
    market_value / book_cost: native currency (USD or CAD) for per-position display.
    market_value_cad, weights, summary totals: all in CAD.
    ws_account_total: WS account-level NLV (includes crypto + all assets). When > 0,
    used as total_value in summary (matches WS app); position sum used for weights.
    unrealized_pnl_cad: WS account-level unrealized P&L in CAD (includes crypto).
    When provided, used for total_cost and total_return_pct instead of position sum.
    """
    cad_per_usd = _get_cad_per_usd()
    positions = []
    total_market_value_cad = cash_balance  # WS cash is always CAD

    for raw in raw_positions:
        security = raw.get("security", {})
        symbol = security.get("symbol", "UNKNOWN")
        quantity = float(raw.get("quantity", 0))
        security_type = security.get("type", "")

        mv_dict = raw.get("market_value", {})
        bv_dict = raw.get("book_value", {})
        ws_market_value = float(mv_dict.get("amount", 0))
        ws_book_cost = float(bv_dict.get("amount", 0))
        ws_market_currency = str(mv_dict.get("currency") or "").upper()
        ws_book_currency = str(bv_dict.get("currency") or "").upper()

        # WS currency is authoritative; fall back to symbol-suffix inference
        currency = ws_market_currency if ws_market_currency in ("USD", "CAD") else _infer_currency(symbol)
        # WS market_value is authoritative — yfinance only supplements day_change + sector
        market_value = ws_market_value
        book_cost = ws_book_cost
        day_change_pct = 0.0
        sector = None

        meta = _ticker_meta(symbol)
        # Only use yfinance currency when WS didn't provide one.
        # WS already converts market_value to account base currency (CAD).
        if currency not in ("USD", "CAD") and meta["currency"]:
            currency = meta["currency"]
        yf_price = meta["last_price"]
        prev = meta["prev_close"]
        if prev and yf_price:
            day_change_pct = round(((yf_price - prev) / prev) * 100, 2)
        sector = meta["sector"]

        fx = cad_per_usd if currency == "USD" else 1.0

        # Book cost: normalize to position's native currency for return calc.
        # WS may return book_value in CAD regardless of security currency.
        bk_cur = ws_book_currency if ws_book_currency in ("USD", "CAD") else currency
        if currency == "USD" and bk_cur == "CAD" and ws_book_cost > 0:
            book_cost = round(ws_book_cost / cad_per_usd, 2)

        market_value_cad = round(market_value * fx, 2)
        total_return_pct = round(((market_value - book_cost) / book_cost) * 100, 2) if book_cost else 0.0
        total_market_value_cad += market_value_cad

        # Live per-share price: prefer yfinance quote (fresher than WS),
        # fall back to derived (market_value / quantity).
        if yf_price and yf_price > 0:
            current_price = round(float(yf_price), 4)
        elif quantity:
            current_price = round(market_value / quantity, 4)
        else:
            current_price = 0.0
        current_price_cad = round(current_price * fx, 4)

        positions.append({
            "symbol": symbol,
            "name": security.get("name", symbol),
            "quantity": quantity,
            "currency": currency,
            "book_cost": book_cost,
            "book_cost_cad": round(book_cost * fx, 2),
            "market_value": market_value,
            "market_value_cad": market_value_cad,
            "current_price": current_price,
            "current_price_cad": current_price_cad,
            "day_change_pct": day_change_pct,
            "total_return_pct": total_return_pct,
            "asset_class": _classify_asset(security_type, symbol),
            "sector": sector,
        })

    # Use WS account NLV as weight denominator when available — includes crypto etc.
    weight_base = max(ws_account_total, total_market_value_cad)
    enriched = []
    for p in positions:
        weight = round((p["market_value_cad"] / weight_base) * 100, 2) if weight_base else 0.0
        enriched.append(Position(**p, weight=weight))

    equity_cost_cad = sum(
        p.book_cost * (cad_per_usd if p.currency == "USD" else 1.0)
        for p in enriched
    )
    # NLV already includes all cash — do not add cash_balance again.
    reported_total = ws_account_total if ws_account_total > 0 else total_market_value_cad

    # Prefer WS unrealized P&L for equity-only return — covers all positions
    # incl. crypto. cash_balance already holds CAD + USD-converted total
    # (wealthsimple.py:369 sets acc["cash"] = cad_cash + usd_cash * fx before
    # storing); usd_cash_balance is raw USD kept for per-currency display
    # only. Adding it here would double-count the USD money.
    total_cash_cad = cash_balance
    if unrealized_pnl_cad and ws_account_total > 0:
        equity_nlv = ws_account_total - total_cash_cad
        total_cost_cad = round(equity_nlv - unrealized_pnl_cad, 2)
        total_return_pct = round(
            (unrealized_pnl_cad / total_cost_cad) * 100, 2
        ) if total_cost_cad > 0 else 0.0
    else:
        total_cost_cad = equity_cost_cad
        equity_invested = total_market_value_cad - total_cash_cad
        total_return_pct = round(
            ((equity_invested - total_cost_cad) / total_cost_cad) * 100, 2
        ) if total_cost_cad else 0.0

    # Account-wide return: (NLV - lifetime deposits) / deposits. Includes cash
    # interest + realized gains + dividends, not just unrealized PnL. Matches
    # what users see in the WS app top-line return.
    account_return_pct = 0.0
    if net_deposits_cad and ws_account_total > 0:
        account_return_pct = round(
            ((ws_account_total - net_deposits_cad) / net_deposits_cad) * 100, 2
        )

    day_change_cad = round(
        sum(p.market_value_cad * p.day_change_pct / 100 for p in enriched), 2
    )

    return PortfolioResponse(
        positions=enriched,
        summary=PortfolioSummary(
            total_value=round(reported_total, 2),
            total_cost=round(total_cost_cad, 2),
            total_return_pct=total_return_pct,
            cash_available=round(cash_balance, 2),
            cash_available_usd=round(usd_cash_balance, 2),
            day_change_cad=day_change_cad,
            net_deposits_cad=round(net_deposits_cad, 2),
            account_return_pct=account_return_pct,
            simple_return_pct=(
                round(simple_return_pct, 2)
                if simple_return_pct is not None else None
            ),
        ),
    )


_RETURNS_CACHE: dict[tuple, tuple[dict, float]] = {}
_RETURNS_TTL = 3600  # 1h — matches technicals cache


def fetch_returns(symbols: list[str], period: str = "1y") -> dict[str, list[float]]:
    """Daily simple returns per symbol over given period. Period e.g. '1y', '6mo', '3mo'."""
    cache_key = (tuple(sorted(symbols)), period)
    entry = _RETURNS_CACHE.get(cache_key)
    if entry and (time.time() - entry[1]) < _RETURNS_TTL:
        return entry[0]

    if not symbols:
        return {}
    try:
        data = yf.download(symbols, period=period, progress=False, auto_adjust=True, group_by="ticker")
    except Exception as e:
        _LOG.warning(f"[market_data] yfinance download error: {e}")
        return {}

    out: dict[str, list[float]] = {}
    for sym in symbols:
        try:
            if len(symbols) == 1:
                series = data["Close"] if "Close" in data.columns else data.iloc[:, 0]
            else:
                series = data[sym]["Close"] if (sym, "Close") in data.columns or sym in data.columns.get_level_values(0) else None
            if series is None or series.empty:
                out[sym] = []
                continue
            series = series.dropna()
            returns = series.pct_change().dropna().tolist()
            out[sym] = [float(r) for r in returns]
        except Exception:
            out[sym] = []
    _RETURNS_CACHE[cache_key] = (out, time.time())
    return out


def fetch_benchmark_returns(symbol: str = "SPY", period: str = "1y") -> list[float]:
    """Daily simple returns for a benchmark ETF (default SPY)."""
    series = fetch_returns([symbol], period=period)
    return series.get(symbol, [])
