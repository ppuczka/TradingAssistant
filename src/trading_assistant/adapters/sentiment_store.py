"""Ignored local sentiment history with preserved evidence and model identity."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from trading_assistant.application.sentiment import validate_sentiment
from trading_assistant.domain.sentiment import SavedSentiment, SentimentFailure


class JsonSentimentStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def save(self, saved: SavedSentiment):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{uuid4()}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            raise SentimentFailure("Summary completed but could not be saved locally.") from None

    def latest(self):
        latest = None
        for path in sorted(self.directory.glob("*.json")):
            try:
                saved = SavedSentiment.model_validate_json(path.read_text())
                validate_sentiment(saved.request, saved.result)
                if saved.analyzed_at > datetime.now(UTC):
                    continue
                if latest is None or saved.analyzed_at > latest.analyzed_at:
                    latest = saved
            except (OSError, ValueError):
                continue
        return latest
