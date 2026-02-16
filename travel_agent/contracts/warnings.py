# travel_agent/contracts/warnings.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class WarningCode(str, Enum):
    # Input/intent issues
    LOW_INTENT_CONFIDENCE = "LOW_INTENT_CONFIDENCE"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"

    # Search/result issues
    NO_RESULTS = "NO_RESULTS"
    PARTIAL_RESULTS = "PARTIAL_RESULTS"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"  # provider/tool timeout (generic)

    # NEW: Step runtime signals (stable, executor-level)
    STEP_TIMEOUT = "STEP_TIMEOUT"
    STEP_SLOW = "STEP_SLOW"
    STEP_COMPLETED_AFTER_TIMEOUT = "STEP_COMPLETED_AFTER_TIMEOUT"

    # Quality issues
    LOW_QUALITY_RESULTS = "LOW_QUALITY_RESULTS"


@dataclass(frozen=True)
class Warning:
    code: WarningCode
    title: str
    details: Dict[str, Any]
    step_id: Optional[str] = None
    agent: Optional[str] = None
