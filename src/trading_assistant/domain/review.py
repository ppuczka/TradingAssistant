"""A review is a set of independent, human-facing holding recommendations."""

from typing import Literal

from pydantic import BaseModel, Field

from trading_assistant.domain.analysis import AnalysisRequest, Recommendation


class ReviewPlan(BaseModel):
    requests: list[AnalysisRequest]
    skipped: list[str] = Field(default_factory=list)


class ReviewItem(BaseModel):
    symbol: str
    status: Literal["RUNNING", "COMPLETED", "FAILED", "STOPPED"]
    recommendation: Recommendation | None = None
    error: str | None = None


class ReviewResult(BaseModel):
    items: list[ReviewItem]
    skipped: list[str]
