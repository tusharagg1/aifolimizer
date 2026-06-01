from pydantic import BaseModel
from typing import Optional


class Account(BaseModel):
    type: str  # "TFSA", "RRSP", "Non-Reg", "Crypto"
    currency: str
    cash_balance: float
    invested_value: float


class UserContext(BaseModel):
    accounts: list[Account]
    total_cash: float
    total_invested: float
    account_types: list[str]


class Position(BaseModel):
    symbol: str
    name: str
    quantity: float
    currency: str = "CAD"  # native currency of the position (USD or CAD)
    book_cost: float  # native currency
    book_cost_cad: float  # CAD equivalent
    market_value: float  # native currency
    market_value_cad: float  # CAD equivalent
    current_price: float = 0.0  # native currency, per share
    current_price_cad: float = 0.0  # CAD equivalent per share
    day_change_pct: float
    total_return_pct: float  # (market_value - book_cost) / book_cost in native currency
    weight: float  # % of total portfolio (CAD-based)
    asset_class: str  # "equity", "etf", "crypto", "bond", "commodity", "cash"
    sector: Optional[str] = None


class PortfolioSummary(BaseModel):
    total_value: float
    total_cost: float
    total_return_pct: float  # equity-only return (PnL / book_cost)
    cash_available: float
    cash_available_usd: float = 0.0
    day_change_cad: float = 0.0
    net_deposits_cad: float = 0.0  # lifetime contributions (WS net deposits)
    account_return_pct: float = 0.0  # (NLV - net_deposits) / net_deposits
    simple_return_pct: float | None = None  # WS-reported account-wide return


class PortfolioResponse(BaseModel):
    positions: list[Position]
    summary: PortfolioSummary
