"""Application-owned analysis telemetry, never model output or prompts."""

from pydantic import BaseModel, ConfigDict, Field


class AnalysisProgress(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: str
    completed_calls: int = Field(default=0, ge=0)
