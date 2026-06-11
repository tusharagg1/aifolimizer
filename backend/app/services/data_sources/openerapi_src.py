"""open.er-api.com FX adapter - ExchangeRate-API open access, free, no key.

Endpoint: https://open.er-api.com/v6/latest/USD   # base -> {rates: {CAD: ...}}

Keyless, ~160 currencies, refreshed once per day (response carries
time_next_update). Complements Frankfurter (ECB, ~30 majors, weekday-only):
open.er-api adds breadth + weekend continuity. Quote-only - the open tier has
no historical time-series, so get_history falls through to the next source.

Symbol form: USDCAD or USDCAD=X (base=USD, target=CAD). The base is in the
URL path and the target must be present in `rates`, else SourceUnavailable.
"""

from __future__ import annotations

import time

import httpx

from app.services.data_sources.base import (
    DataSource,
    Quote,
    SourceUnavailable,
)

_BASE = "https://open.er-api.com/v6/latest"


def _split_pair(symbol: str) -> tuple[str, str]:
    s = symbol.upper().replace("=X", "")
    if len(s) != 6 or not s.isalpha():
        raise SourceUnavailable(f"openerapi: bad pair {symbol}")
    return s[:3], s[3:]


class OpenErApiSource(DataSource):
    name = "openerapi"

    def is_configured(self) -> bool:
        return True  # keyless open tier

    def get_quote(self, symbol: str) -> Quote:
        base, target = _split_pair(symbol)
        try:
            resp = httpx.get(f"{_BASE}/{base}", timeout=10.0)
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as e:
            raise SourceUnavailable(f"openerapi http {symbol}: {type(e).__name__}") from e

        if data.get("result") != "success":
            raise SourceUnavailable(f"openerapi: {data.get('error-type', 'non-success')} for {symbol}")
        rate = (data.get("rates") or {}).get(target)
        if rate is None:
            raise SourceUnavailable(f"openerapi: no rate for {symbol}")
        try:
            price = float(rate)
        except (TypeError, ValueError) as e:
            raise SourceUnavailable(f"openerapi bad rate {symbol}: {e}") from e

        # Open tier is daily-snapshot only - no prior value, so no change%.
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=price,
            currency=target,
            day_change_pct=None,
            source=self.name,
            as_of=time.time(),
        )
