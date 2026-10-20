"""Ignored local snapshot of discovery results; no recommendations or secrets."""

from pathlib import Path

from trading_assistant.domain.candidates import CandidateBatch, CandidateUnavailableError


class JsonCandidateStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, batch: CandidateBatch) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(batch.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            raise CandidateUnavailableError(
                "Candidates fetched but local snapshot could not be saved."
            ) from None
