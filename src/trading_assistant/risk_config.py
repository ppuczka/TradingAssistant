"""Risk warning thresholds from YAML and environment variables."""

import os
from pathlib import Path

import yaml

from trading_assistant.domain.risk import RiskSettings


def load_risk_settings(path: Path) -> RiskSettings:
    try:
        values = yaml.safe_load(path.read_text()) if path.exists() else {}
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise ValueError
        for field in RiskSettings.model_fields:
            value = os.environ.get("TRADER_RISK_" + field.upper())
            if value is not None:
                values[field] = value
        return RiskSettings.model_validate(values)
    except (OSError, ValueError, yaml.YAMLError):
        raise ValueError(
            "Invalid risk configuration; thresholds must be percentages from 0 to 100."
        ) from None
