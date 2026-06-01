"""CoinGecko public adapter — crypto, free, no API key.

Public market endpoint is rate-limited to ~30 requests/min and prices
are CAD-denominated to match the Canadian portfolio context. Falls
through if the symbol is unknown to the local map (intentional — we
don't want to query 17000 coins).

Endpoint: https://api.coingecko.com/api/v3/coins/markets

Fundamentals available indirectly: market_cap, supply, ATH drawdown.
"""

from __future__ import annotations

import time

import httpx

from app.services.data_sources.base import (
    DataSource,
    PriceBar,
    Quote,
    SourceUnavailable,
)

_BASE = "https://api.coingecko.com/api/v3"

_SYMBOL_MAP: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "ADA": "cardano",
    "DOT": "polkadot", "AVAX": "avalanche-2", "LINK": "chainlink",
    "MATIC": "matic-network", "DOGE": "dogecoin", "XRP": "ripple",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "ATOM": "cosmos",
    "UNI": "uniswap", "ALGO": "algorand", "NEAR": "near", "FTM": "fantom",
    "SAND": "the-sandbox", "MANA": "decentraland", "AAVE": "aave",
    "USDC": "usd-coin", "USDT": "tether", "TON": "the-open-network",
    "TRX": "tron", "SHIB": "shiba-inu", "INJ": "injective-protocol",
    "ARB": "arbitrum", "OP": "optimism", "FIL": "filecoin", "APT": "aptos",
}


class CoinGeckoSource(DataSource):
    name = "coingecko"

    def is_configured(self) -> bool:
        return True

    def _coin_id(self, symbol: str) -> str:
        cid = _SYMBOL_MAP.get(symbol.upper())
        if not cid:
            raise SourceUnavailable(f"coingecko: unknown crypto {symbol}")
        return cid

    def get_quote(self, symbol: str) -> Quote:
        cid = self._coin_id(symbol)
        try:
            resp = httpx.get(
                f"{_BASE}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": cid,
                    "price_change_percentage": "24h",
                },
                timeout=10.0,
            )
            if resp.status_code == 429:
                raise SourceUnavailable("coingecko: rate limited")
            resp.raise_for_status()
            data = resp.json() or []
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"coingecko http {symbol}: {e}") from e

        if not data:
            raise SourceUnavailable(f"coingecko: empty for {symbol}")
        coin = data[0]
        price = float(coin.get("current_price") or 0.0)
        change_pct = coin.get("price_change_percentage_24h")
        if price <= 0:
            raise SourceUnavailable(f"coingecko: zero price for {symbol}")
        prev = price / (1 + (change_pct / 100)) if change_pct else price
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=prev,
            currency="USD",
            day_change_pct=float(change_pct) if change_pct is not None else None,
            source=self.name,
            as_of=time.time(),
        )

    def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> list[PriceBar]:
        if interval != "1d":
            raise SourceUnavailable("coingecko: daily only")
        cid = self._coin_id(symbol)
        days_map = {
            "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365,
            "2y": 730, "3y": 1095, "5y": 1825,
            "ytd": 365, "max": "max",
        }
        days = days_map.get(period, 365)
        try:
            resp = httpx.get(
                f"{_BASE}/coins/{cid}/market_chart",
                params={"vs_currency": "usd", "days": days},
                timeout=15.0,
            )
            if resp.status_code == 429:
                raise SourceUnavailable("coingecko: rate limited")
            resp.raise_for_status()
            data = resp.json() or {}
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"coingecko http {symbol}: {e}") from e

        prices = data.get("prices") or []
        volumes = {int(t): v for t, v in (data.get("total_volumes") or [])}
        if not prices:
            raise SourceUnavailable(f"coingecko: empty history for {symbol}")
        from datetime import datetime
        bars: list[PriceBar] = []
        for ts_ms, p in prices:
            try:
                d = datetime.utcfromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d")
                pf = float(p)
                bars.append(PriceBar(
                    symbol=symbol,
                    date=d,
                    open=pf, high=pf, low=pf, close=pf,
                    volume=float(volumes.get(int(ts_ms), 0.0)),
                    adj_close=pf,
                    source=self.name,
                    as_of=time.time(),
                ))
            except (TypeError, ValueError):
                continue
        if not bars:
            raise SourceUnavailable(f"coingecko: parsed zero bars for {symbol}")
        return bars
