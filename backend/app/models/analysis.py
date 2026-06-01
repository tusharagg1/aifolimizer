from pydantic import BaseModel
from typing import Optional
from datetime import datetime


ANALYSIS_TYPES = [
    "portfolio_health",
    "risk_assessment",
    "stock_analysis",
    "macro_impact",
    "dividend_strategy",
    "earnings_analyzer",
    "sector_rotation",
]

OPUS_TYPES = {"risk_assessment", "earnings_analyzer"}
HAIKU_TYPES = {"quick_ticker_lookup", "status_check"}


class AnalysisRequest(BaseModel):
    session_id: str
    account_id: str
    analysis_type: str
    ticker: Optional[str] = None  # for stock_analysis and earnings_analyzer


class AnalysisResponse(BaseModel):
    analysis_type: str
    model_used: str
    response_text: str
    health_score: Optional[int] = None
    created_at: datetime
