# travel-agent/contracts/context.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .intent import UserIntent
from .result import FlightResult, HotelResult, BundleResult, RankedItem
from .warnings import Warning
from .events import Event, make_event, EVENT_WARNING_RAISED, EVENT_DECISION


@dataclass
class SharedContext:
    run_id: str
    intent: UserIntent

    # Loop control
    attempt: int = 0
    max_attempts: int = 3

    # Limits/budgets (soft enforcement)
    max_provider_calls: int = 200
    time_budget_ms: Optional[int] = None

    # Artifacts
    flights: List[FlightResult] = field(default_factory=list)
    hotels: List[HotelResult] = field(default_factory=list)
    bundles: List[BundleResult] = field(default_factory=list)

    ranked: List[RankedItem] = field(default_factory=list)

    # Quality signals
    warnings: List[Warning] = field(default_factory=list)
    confidence: float = 0.0

    # Traceability
    events: List[Event] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)

    # Observability counters
    provider_calls: Dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0

    # Planner scratchpad (replan hints)
    scratch: Dict[str, Any] = field(default_factory=dict)

    def add_warning(self, warning: Warning) -> None:
        self.warnings.append(warning)
        self.events.append(make_event(
            EVENT_WARNING_RAISED,
            code=str(warning.code),
            title=warning.title,
            details=warning.details,
            step_id=warning.step_id,
            agent=warning.agent,
        ))

    def decide(self, reason: str, **data: Any) -> None:
        item = {"reason": reason, **data}
        self.decisions.append(item)
        self.events.append(make_event(EVENT_DECISION, **item))

    def inc_provider_call(self, provider: str, n: int = 1) -> None:
        self.provider_calls[provider] = self.provider_calls.get(provider, 0) + n
