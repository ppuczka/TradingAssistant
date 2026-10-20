"""Optional progress reporting without changing the AnalysisEngine contract."""

from collections.abc import Callable

from trading_assistant.domain.progress import AnalysisProgress

ProgressCallback = Callable[[AnalysisProgress], None]


async def analyze_with_progress(service, request, progress: ProgressCallback):
    reporter = getattr(service, "analyze_with_progress", None)
    if reporter is not None:
        return await reporter(request, progress)
    progress(AnalysisProgress(stage="Analyzing"))
    return await service.analyze(request)
