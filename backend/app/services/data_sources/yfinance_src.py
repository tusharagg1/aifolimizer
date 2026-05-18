"""yfinance adapter.

Wraps the existing yfinance.Ticker calls into the DataSource interface
so the router can mix it with other providers. yfinance is the primary
free source — fast, broad coverage, but rate-limited / sometimes stale.
"""

from __future__ import annotations

import time

import yfinance as yf

from app.services.data_sources.base import (
    DataSource,
    Fundamentals,
    PriceBar,
    Quote,
    SourceUnavailable,
)


class YFinanceSource(DataSource):
    name = "yfinance"

    def is_configured(self) -> bool:
        return True  # no key required

    def get_quote(self, symbol: str) -> Quote:
        try:
            t = yf.Ticker(symbol)
            fast = t.fast_info
            price = float(fast.last_price or 0.0)
            prev = float(fast.regular_market_previous_close or 0.0)
            if price <= 0:
                raise SourceUnavailable(f"yfinance returned no price for {symbol}")
            change_pct = ((price - prev) / prev * 100) if prev else None
            currency = None
            try:
                info = t.info or {}
                cur = str(info.get("currency") or "").upper()
                currency = cur if cur in ("USD", "CAD") else None
            except Exception:
                pass
            return Quote(
                symbol=symbol,
                price=price,
                prev_close=prev,
                currency=currency,
                day_change_pct=change_pct,
                source=self.name,
                as_of=time.time(),
            )
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"yfinance quote {symbol}: {e}") from e

    def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> list[PriceBar]:
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
            if df is None or df.empty:
                raise SourceUnavailable(f"yfinance: empty history for {symbol}")
            bars: list[PriceBar] = []
            for ts, row in df.iterrows():
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        date=ts.strftime("%Y-%m-%d"),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0.0),
                        adj_close=float(row["Adj Close"]) if "Adj Close" in row else None,
                        source=self.name,
                        as_of=time.time(),
                    )
                )
            return bars
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"yfinance history {symbol}: {e}") from e

    def get_fundamentals(self, symbol: str) -> Fundamentals:
        try:
            info = yf.Ticker(symbol).info or {}
            if not info:
                raise SourceUnavailable(f"yfinance: empty info for {symbol}")
            div_yield = info.get("dividendYield")
            if div_yield is not None:
                div_yield = float(div_yield) * 100 if div_yield < 1 else float(div_yield)
            payout = info.get("payoutRatio")
            if payout is not None:
                payout = float(payout) * 100
            return Fundamentals(
                symbol=symbol,
                pe_ratio=_f(info.get("trailingPE")),
                eps=_f(info.get("trailingEps")),
                dividend_yield_pct=div_yield,
                payout_ratio_pct=payout,
                market_cap=_f(info.get("marketCap")),
                beta=_f(info.get("beta")),
                analyst_target=_f(info.get("targetMeanPrice")),
                earnings_date=_iso(info.get("earningsTimestamp")),
                sector=info.get("sector"),
                industry=info.get("industry"),
                institutional_pct=_pct(info.get("heldPercentInstitutions")),
                short_pct_float=_pct(info.get("shortPercentOfFloat")),
                source=self.name,
                as_of=time.time(),
            )
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"yfinance fundamentals {symbol}: {e}") from e


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _pct(x) -> float | None:
    f = _f(x)
    return f * 100 if f is not None and f < 1 else f


def _iso(ts) -> str | None:
    if ts is None:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None
