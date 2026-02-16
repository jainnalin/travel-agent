# travel_agent/contracts/events.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import time


@dataclass(frozen=True)
class Event:
    ts: float
    kind: str
    data: Dict[str, Any]


def now_ts() -> float:
    return time.time()


def make_event(kind: str, **data: Any) -> Event:
    return Event(ts=now_ts(), kind=kind, data=dict(data))


# Common event kinds (free-form, but consistent)
EVENT_PLAN_CREATED = "plan.created"
EVENT_STEP_STARTED = "step.started"
EVENT_STEP_COMPLETED = "step.completed"
EVENT_STEP_FAILED = "step.failed"

# NEW: stable runtime signal from executor (do NOT infer from warning titles)
EVENT_STEP_RUNTIME = "step.runtime"
# data payload (recommended):
# {
#   "attempt_index": int,
#   "step_id": str,
#   "tool": str,
#   "timeout_s": float|None,
#   "elapsed_s": float,
#   "status": "OK"|"FAILED"|"TIMEOUT",
#   "timed_out": bool,
# }

EVENT_WARNING_RAISED = "warning.raised"
EVENT_DECISION = "decision"
EVENT_VERDICT = "verdict"
EVENT_TOOL_CALLED = "tool.called"
EVENT_TOOL_RESULT = "tool.result"


@dataclass(frozen=True)
class ToolCallInfo:
    tool: str
    args: Dict[str, Any]
    ok: bool
    error: Optional[str] = None
