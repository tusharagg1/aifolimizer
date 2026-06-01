"""Symbol -> (asset_class, currency, exchange) classifier.

Foundation for currency-aware routing. Each Quote / fetch path picks its
provider chain based on classify_asset(symbol).

Asset classes:
  us_equity     — NYSE/Nasdaq listings, USD
  ca_equity     — TSX/TSXV/NEO/CSE listings, CAD
  uk_equity     — LSE listings, GBP/GBp
  eu_equity     — Paris/Frankfurt/Milan/Amsterdam/Madrid/SIX, EUR/CHF
  crypto        — known crypto tickers (BTC, ETH, ...) — USD-denominated upstream
  fx            — currency pairs (yfinance =X form, or 6-char pair)
  index         — ^-prefixed indices
  unknown       — fallback

The classifier is deterministic and cheap. Used by data_router to select
chains and staleness budgets without per-call lookups.
"""

from __future__ import annotations

from dataclasses import dataclass

CRYPTO_TICKERS = frozenset({
    "BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "LINK", "MATIC", "DOGE", "XRP",
    "LTC", "BCH", "ATOM", "UNI", "ALGO", "NEAR", "FTM", "SAND", "MANA", "AAVE",
    "USDC", "USDT", "TON", "TRX", "SHIB", "INJ", "ARB", "OP", "FIL", "APT",
})

KNOWN_CANADIAN = frozenset({
    "XEQT", "VFV", "XIC", "XIU", "ZSP", "XUS", "ZEB", "VDY", "CDZ", "XRE",
    "VCN", "ZCN", "XSP", "HXT", "HGRO", "VEQT", "VGRO", "VBAL", "VCIP",
    "XBAL", "ZGQ", "ZAG", "XBB", "ZSB", "XLB", "VAB", "ZGB", "VSB",
})

CA_SUFFIXES = (".TO", ".V", ".TSX", ".NE", ".CN")
UK_SUFFIXES = (".L",)
EU_SUFFIXES = (".PA", ".DE", ".MI", ".AS", ".MC", ".SW", ".BR", ".LS", ".VI", ".HE", ".CO", ".OL", ".ST")

EU_CCY_BY_SUFFIX = {
    ".PA": "EUR", ".DE": "EUR", ".MI": "EUR", ".AS": "EUR",
    ".MC": "EUR", ".SW": "CHF", ".BR": "EUR", ".LS": "EUR",
    ".VI": "EUR", ".HE": "EUR", ".CO": "DKK", ".OL": "NOK", ".ST": "SEK",
}

INDEX_CCY = {
    "^GSPC": "USD", "^IXIC": "USD", "^DJI": "USD", "^VIX": "USD", "^RUT": "USD",
    "^GSPTSE": "CAD", "^GSPTSE60": "CAD",
    "^FTSE": "GBP", "^FCHI": "EUR", "^GDAXI": "EUR", "^STOXX50E": "EUR",
    "^N225": "JPY", "^HSI": "HKD",
}


@dataclass(frozen=True)
class AssetInfo:
    symbol: str
    asset_class: str  # us_equity | ca_equity | uk_equity | eu_equity | crypto | fx | index | unknown
    currency: str | None
    exchange: str | None  # NYSE/NASDAQ/TSX/TSXV/LSE/XETRA/...

    def is_equity(self) -> bool:
        return self.asset_class.endswith("_equity")

    def is_north_american(self) -> bool:
        return self.asset_class in ("us_equity", "ca_equity")


def _is_crypto(s: str) -> bool:
    return s.upper() in CRYPTO_TICKERS


def _is_fx(s: str) -> bool:
    u = s.upper()
    if u.endswith("=X"):
        return True
    if len(u) == 6 and u.isalpha():
        # eg USDCAD, EURUSD
        return True
    return False


def _is_index(s: str) -> bool:
    return s.startswith("^")


def classify_asset(symbol: str) -> AssetInfo:
    """Classify symbol -> AssetInfo. Idempotent, no network."""
    if not symbol:
        return AssetInfo(symbol, "unknown", None, None)
    s = symbol.strip()
    upper = s.upper()

    if _is_index(upper):
        return AssetInfo(s, "index", INDEX_CCY.get(upper), None)

    if _is_crypto(upper):
        return AssetInfo(s, "crypto", "USD", None)

    if _is_fx(upper):
        # symbol form: USDCAD=X or USDCAD
        return AssetInfo(s, "fx", None, "FX")

    for suf in CA_SUFFIXES:
        if upper.endswith(suf):
            ex = {".TO": "TSX", ".V": "TSXV", ".NE": "NEO",
                  ".CN": "CSE", ".TSX": "TSX"}[suf]
            return AssetInfo(s, "ca_equity", "CAD", ex)

    if upper in KNOWN_CANADIAN:
        return AssetInfo(s, "ca_equity", "CAD", "TSX")

    for suf in UK_SUFFIXES:
        if upper.endswith(suf):
            return AssetInfo(s, "uk_equity", "GBP", "LSE")

    for suf in EU_SUFFIXES:
        if upper.endswith(suf):
            return AssetInfo(s, "eu_equity", EU_CCY_BY_SUFFIX.get(suf, "EUR"), suf[1:].upper())

    return AssetInfo(s, "us_equity", "USD", None)


def staleness_budget_s(asset_class: str, market_open: bool = True) -> float:
    """Per-asset max age before a cached quote is considered stale.

    market_open=False relaxes equity budgets since EOD prices are stable.
    """
    if asset_class == "crypto":
        return 60.0
    if asset_class == "fx":
        return 300.0
    if asset_class == "index":
        return 600.0 if market_open else 86400.0
    if asset_class.endswith("_equity"):
        return 300.0 if market_open else 86400.0
    return 600.0


def expected_currencies(asset_class: str) -> tuple[str, ...]:
    """Currencies a payload's quote.currency may legitimately carry.

    Used by router to reject obvious mis-routes (eg. USD coming back for .TO).
    """
    return {
        "us_equity": ("USD",),
        "ca_equity": ("CAD",),
        "uk_equity": ("GBP", "GBp"),
        "eu_equity": ("EUR", "CHF", "DKK", "NOK", "SEK"),
        "crypto": ("USD", "CAD"),
        "fx": ("USD", "CAD", "EUR", "GBP", "JPY"),
        "index": ("USD", "CAD", "GBP", "EUR", "JPY", "HKD"),
    }.get(asset_class, ())
