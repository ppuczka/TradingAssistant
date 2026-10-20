"""Read boundary for saved analysis results; no engine calls."""

from typing import Protocol

from trading_assistant.domain.analysis import Recommendation, SavedAnalysis


class RecommendationReader(Protocol):
    def latest(self) -> dict[str, SavedAnalysis]: ...


class OpportunityReader(Protocol):
    def opportunities(self) -> list[SavedAnalysis]: ...


class OpportunityHistoryService:
    def __init__(self, reader: OpportunityReader):
        self.reader = reader

    async def records(self, *, history: bool = False) -> list[SavedAnalysis]:
        import asyncio

        records = await asyncio.to_thread(self.reader.opportunities)
        records.sort(key=lambda saved: saved.recommendation.analyzed_at, reverse=True)
        if history:
            return records
        latest = {}
        for saved in records:
            latest.setdefault(saved.recommendation.symbol, saved)
        return sorted(latest.values(), key=lambda saved: opportunity_priority(saved.recommendation))


def opportunity_priority(result: Recommendation) -> tuple[int, int]:
    """Order by action and the engine's own rating; never infer confidence."""
    actions = {"BUY": 0, "WATCH": 1, "IGNORE": 2}
    ratings = {"buy": 0, "overweight": 1, "hold": 2, "underweight": 3, "sell": 4}
    rating = (
        ratings.get(result.engine_rating.strip().casefold(), 5)
        if result.engine == "TradingAgents"
        else 5
    )
    return actions.get(result.action.value, 3), rating
