"""Dated portfolio-linked market evidence and application-owned AI summaries."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_assistant.domain.news import NewsArticle

Market = Literal["US", "PL"]


class SentimentQuote(BaseModel):
    symbol: str
    market: Market
    currency: str
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    daily_change_percent: Decimal | None = Field(default=None, allow_inf_nan=False)
    as_of: AwareDatetime
    source: str


class SentimentRequest(BaseModel):
    captured_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    scope: str = "Portfolio-linked listing sample; not whole-market breadth or an index forecast"
    quotes: list[SentimentQuote] = Field(default_factory=list)
    news: list[NewsArticle] = Field(default_factory=list)
    expected_symbols: dict[Market, list[str]]
    news_markets: dict[str, list[Market]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class MarketSentiment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Market
    outlook: Literal["BULLISH", "NEUTRAL", "BEARISH", "MIXED", "UNKNOWN"]
    reasoning: list[str] = Field(min_length=1)
    limitations: list[str]
    quote_symbols: list[str]
    news_urls: list[str]


class SentimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markets: list[MarketSentiment]


class SavedSentiment(BaseModel):
    request: SentimentRequest
    result: SentimentResult
    model: str
    analyzed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_chronology(self):
        if self.request.captured_at > self.analyzed_at:
            raise ValueError("Evidence cannot be captured after analysis completion.")
        return self


class SentimentFailure(ValueError):
    pass
