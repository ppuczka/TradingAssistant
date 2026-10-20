"""YAML configuration with explicit environment overrides; no model defaults."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_assistant.domain.analysis import AnalysisFailure


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm_provider: Literal["openai"] = "openai"
    quick_model: str = Field(min_length=1)
    deep_model: str = Field(min_length=1)
    default_horizon: str = Field(default="3-5 years", min_length=1, max_length=100)
    max_tool_rounds: int = Field(default=20, ge=1, le=20)
    max_debate_rounds: int = Field(default=1, ge=1, le=5)
    max_risk_rounds: int = Field(default=1, ge=1, le=5)
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    max_tokens: int = Field(default=8192, ge=1)


def load_analysis_settings(path: Path) -> AnalysisSettings:
    try:
        values = yaml.safe_load(path.read_text()) if path.exists() else {}
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise ValueError("Invalid configuration")
        for field, env_names in {
            "quick_model": ("TRADER_QUICK_MODEL", "TRADINGAGENTS_QUICK_THINK_LLM"),
            "deep_model": ("TRADER_DEEP_MODEL", "TRADINGAGENTS_DEEP_THINK_LLM"),
            "default_horizon": ("TRADER_TIME_HORIZON",),
            "llm_provider": ("TRADINGAGENTS_LLM_PROVIDER",),
            "max_tool_rounds": ("TRADINGAGENTS_MAX_TOOL_ROUNDS",),
            "max_debate_rounds": ("TRADINGAGENTS_MAX_DEBATE_ROUNDS",),
            "max_risk_rounds": ("TRADINGAGENTS_MAX_RISK_ROUNDS",),
            "reasoning_effort": ("TRADINGAGENTS_OPENAI_REASONING_EFFORT",),
            "max_tokens": ("TRADINGAGENTS_MAX_TOKENS",),
        }.items():
            value = next(
                (os.environ[n].strip() for n in env_names if os.environ.get(n, "").strip()), None
            )
            if value:
                values[field] = value
        return AnalysisSettings.model_validate(values)
    except (OSError, ValueError, yaml.YAMLError, ValidationError):
        raise AnalysisFailure(
            "Set quick_model and deep_model in analysis.yaml, or TRADER_QUICK_MODEL and "
            "TRADER_DEEP_MODEL in the environment. Check the analysis configuration."
        ) from None
