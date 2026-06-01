"""
Universe screener: scan TSX + S&P 500 candidates for stage-2 setups.
Filters: stage 2 (price > SMA200, SMA200 rising), RSI 35-70,
         MACD positive, ADX > 20, technical_score >= 0.5.
Results ranked by technical_score desc.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.technicals import get_technicals
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.screener")


_cache: dict[str, tuple[list, float]] = {}
_CACHE_TTL = 1800  # 30 min — screener is expensive

# Curated universe: liquid TSX + S&P 500 names suitable for Canadian retail investor
TSX_UNIVERSE = [
    "XEQT.TO",
    "VFV.TO",
    "XIU.TO",
    "XIC.TO",
    "ZEB.TO",
    "ZRE.TO",
    "TD.TO",
    "RY.TO",
    "BNS.TO",
    "BMO.TO",
    "CM.TO",
    "NA.TO",
    "CNR.TO",
    "CP.TO",
    "TRP.TO",
    "ENB.TO",
    "SU.TO",
    "CVE.TO",
    "ATD.TO",
    "MFC.TO",
    "SLF.TO",
    "GWO.TO",
    "POW.TO",
    "SHOP.TO",
    "CSU.TO",
    "KXS.TO",
    "OTEX.TO",
    "BB.TO",
    "WPM.TO",
    "ABX.TO",
    "K.TO",
    "AEM.TO",
    "L.TO",
    "MRU.TO",
    "EMP-A.TO",
    "BCE.TO",
    "T.TO",
    "TELUS.TO",
]

SPX_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AVGO",
    "JPM",
    "V",
    "MA",
    "UNH",
    "HD",
    "PG",
    "JNJ",
    "MRK",
    "ABBV",
    "XOM",
    "CVX",
    "COP",
    "WMT",
    "COST",
    "TGT",
    "AMD",
    "QCOM",
    "AMAT",
    "KLAC",
    "LRCX",
    "LLY",
    "TMO",
    "DHR",
    "ABT",
    "CAT",
    "DE",
    "HON",
    "RTX",
    "LMT",
    "BRK-B",
    "BAC",
    "WFC",
    "GS",
    "MS",
    "SPY",
    "QQQ",
    "IWM",
    "GLD",
    "TLT",
]

FULL_UNIVERSE = list(dict.fromkeys(TSX_UNIVERSE + SPX_UNIVERSE))


def _passes_filter(sym: str, t: dict) -> bool:
    if not t:
        return False
    stage = t.get("stage")
    rsi = t.get("rsi_14")
    macd_hist = t.get("macd_hist")
    adx = t.get("adx_14")
    score = t.get("technical_score", 0) or 0
    return (
        stage == 2
        and rsi is not None
        and 35 <= rsi <= 70
        and macd_hist is not None
        and macd_hist > 0
        and (adx is None or adx > 20)
        and score >= 0.45
    )


def run_screener(
    universe: list[str] | None = None,
    max_results: int = 30,
) -> list[dict]:
    syms = universe or FULL_UNIVERSE
    key = ",".join(sorted(syms))
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0][:max_results]

    # Batch technicals in chunks of 20 to avoid yfinance timeout
    chunk_size = 20
    chunks = [syms[i : i + chunk_size] for i in range(0, len(syms), chunk_size)]
    all_technicals: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(get_technicals, chunk): chunk for chunk in chunks}
        for fut in as_completed(futures):
            try:
                all_technicals.update(fut.result())
            except Exception as e:
                _LOG.warning(f"[screener] chunk error: {e}")

    results = []
    for sym, t in all_technicals.items():
        if not _passes_filter(sym, t):
            continue
        results.append(
            {
                "symbol": sym,
                "technical_score": t.get("technical_score"),
                "current_price": t.get("current_price"),
                "stage": t.get("stage"),
                "minervini_score": t.get("minervini_score"),
                "rsi_14": t.get("rsi_14"),
                "rsi_signal": t.get("rsi_signal"),
                "adx_14": t.get("adx_14"),
                "adx_signal": t.get("adx_signal"),
                "macd_hist": t.get("macd_hist"),
                "stoch_k": t.get("stoch_k"),
                "stoch_signal": t.get("stoch_signal"),
                "obv_trend": t.get("obv_trend"),
                "volume_score": t.get("volume_score"),
                "atr_pct": t.get("atr_pct"),
                "pct_from_52w_high": t.get("pct_from_52w_high"),
                "trend": t.get("trend"),
                "sma_200_slope_pct": t.get("sma_200_slope_pct"),
            }
        )

    results.sort(key=lambda r: r.get("technical_score") or 0, reverse=True)
    _cache[key] = (results, time.time())
    return results[:max_results]
