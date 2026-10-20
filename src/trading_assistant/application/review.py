"""Sequential review with explicit per-symbol failures and safe stopping boundaries."""

from collections.abc import Callable

from trading_assistant.application.analysis import AnalysisService
from trading_assistant.application.progress import ProgressCallback, analyze_with_progress
from trading_assistant.application.recommendations import opportunity_priority
from trading_assistant.domain.analysis import AnalysisFailure
from trading_assistant.domain.review import ReviewItem, ReviewPlan, ReviewResult


class PortfolioReviewService:
    def __init__(self, analysis: AnalysisService):
        self.analysis = analysis

    async def run(
        self,
        plan: ReviewPlan,
        *,
        progress: Callable | None = None,
        stop_requested: Callable[[], bool] | None = None,
        stage_progress: ProgressCallback | None = None,
    ) -> ReviewResult:
        items = []
        for index, request in enumerate(plan.requests, 1):
            if stop_requested and stop_requested():
                item = ReviewItem(symbol=request.symbol, status="STOPPED")
            else:
                if progress:
                    progress(
                        index,
                        len(plan.requests),
                        ReviewItem(symbol=request.symbol, status="RUNNING"),
                    )
                try:
                    result = (
                        await analyze_with_progress(self.analysis, request, stage_progress)
                        if stage_progress
                        else await self.analysis.analyze(request)
                    )
                    item = ReviewItem(
                        symbol=request.symbol, status="COMPLETED", recommendation=result
                    )
                except AnalysisFailure as exc:
                    item = ReviewItem(symbol=request.symbol, status="FAILED", error=str(exc))
                except Exception:
                    item = ReviewItem(
                        symbol=request.symbol,
                        status="FAILED",
                        error="Analysis failed; no recommendation substituted.",
                    )
            items.append(item)
            if progress:
                progress(index, len(plan.requests), item)
        return ReviewResult(items=items, skipped=plan.skipped)


def ordered_opportunities(items):
    """Order by action and engine rating, preserving order for equal ratings."""
    return sorted(
        items,
        key=lambda item: (
            opportunity_priority(item.recommendation) if item.recommendation else (4, 5)
        ),
    )
