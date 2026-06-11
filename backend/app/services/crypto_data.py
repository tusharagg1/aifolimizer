import time
import httpx

_BASE = "https://api.coingecko.com/api/v3"

_SYMBOL_MAP: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "DOGE": "dogecoin",
    "XRP": "ripple",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "ALGO": "algorand",
    "NEAR": "near",
    "FTM": "fantom",
    "SAND": "the-sandbox",
    "MANA": "decentraland",
    "AAVE": "aave",
}

_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 300  # 5 minutes - crypto prices move fast


def get_crypto_data(symbols: list[str]) -> dict[str, dict]:
    crypto_syms = [s.upper() for s in symbols if s.upper() in _SYMBOL_MAP]
    if not crypto_syms:
        return {}

    cache_key = ",".join(sorted(crypto_syms))
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return data

    coin_ids = [_SYMBOL_MAP[s] for s in crypto_syms]
    params = {
        "vs_currency": "cad",
        "ids": ",".join(coin_ids),
        "price_change_percentage": "24h,7d,30d",
    }

    try:
        resp = httpx.get(f"{_BASE}/coins/markets", params=params, timeout=10)
        resp.raise_for_status()
        coins = resp.json()
    except Exception as e:
        return {s: {"error": str(e)} for s in crypto_syms}

    id_to_sym = {v: k for k, v in _SYMBOL_MAP.items()}
    result: dict[str, dict] = {}
    for coin in coins:
        sym = id_to_sym.get(coin.get("id", ""))
        if sym and sym in crypto_syms:
            result[sym] = {
                "current_price_cad": coin.get("current_price"),
                "market_cap_cad": coin.get("market_cap"),
                "market_cap_rank": coin.get("market_cap_rank"),
                "change_24h_pct": coin.get("price_change_percentage_24h"),
                "change_7d_pct": coin.get("price_change_percentage_7d_in_currency"),
                "change_30d_pct": coin.get("price_change_percentage_30d_in_currency"),
                "all_time_high_cad": coin.get("ath"),
                "ath_drawdown_pct": coin.get("ath_change_percentage"),
                "circulating_supply": coin.get("circulating_supply"),
                "total_volume_cad": coin.get("total_volume"),
            }

    _cache[cache_key] = (result, time.time())
    return result


def crypto_symbols_from_portfolio(symbols: list[str]) -> list[str]:
    return [s for s in symbols if s.upper() in _SYMBOL_MAP]
