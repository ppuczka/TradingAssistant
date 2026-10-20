"""Local, ignored analysis records with exact request context and normalized output."""

import json
from pathlib import Path
from uuid import uuid4

from trading_assistant.analysis_config import AnalysisSettings
from trading_assistant.application.price_levels import with_report_levels
from trading_assistant.domain.analysis import AnalysisFailure, AnalysisRequest, Recommendation


class JsonRecommendationStore:
    def __init__(self, directory: Path, settings: AnalysisSettings):
        self.directory, self.settings = directory, settings

    def save(self, request: AnalysisRequest, result: Recommendation) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{uuid4()}.json"
            path.write_text(
                json.dumps(
                    {
                        "request": request.model_dump(mode="json"),
                        "recommendation": result.model_dump(mode="json"),
                        "settings": self.settings.model_dump(mode="json"),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            raise AnalysisFailure(
                "Analysis completed but its local recommendation record could not be saved."
            ) from None


class JsonRecommendationReader:
    def __init__(self, directory: Path, legacy_reports=None):
        self.directory = directory
        self.legacy_reports = legacy_reports

    def _records(self, *, owned: bool):
        from datetime import UTC, datetime

        from pydantic import ValidationError

        from trading_assistant.domain.analysis import SavedAnalysis

        records: list[SavedAnalysis] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                saved = SavedAnalysis.model_validate(json.loads(path.read_text(encoding="utf-8")))
                result = saved.recommendation
                if result.owned != owned or result.analyzed_at > datetime.now(UTC):
                    continue
                records.append(saved)
            except (OSError, ValueError, ValidationError):
                continue
        if self.legacy_reports:
            for saved in records:
                if not saved.recommendation.reports:
                    saved.recommendation.reports = self.legacy_reports.reports_for(saved)
        for saved in records:
            saved.recommendation = with_report_levels(saved.request, saved.recommendation)
        return records

    def latest(self):
        latest = {}
        for saved in self._records(owned=True):
            symbol = saved.recommendation.symbol
            previous = latest.get(symbol)
            if (
                previous is None
                or saved.recommendation.analyzed_at > previous.recommendation.analyzed_at
            ):
                latest[symbol] = saved
        return latest

    def opportunities(self):
        return self._records(owned=False)
