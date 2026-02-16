# travel_agent/orchestrator/policies.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class ExecutorPolicy:
    max_workers: int = 8
    default_resource_limit: int = 1
    resource_limits: Dict[str, int] = field(default_factory=dict)
    max_provider_calls: Optional[int] = None

    def merged_resource_limits(self, *, scratch: dict) -> Dict[str, int]:
        limits = dict(self.resource_limits or {})
        ap = scratch.get("amadeus_parallelism")
        if ap is not None:
            try:
                limits["provider:amadeus"] = max(1, int(ap))
            except Exception:
                pass
        return limits


@dataclass(frozen=True)
class LoopPolicy:
    max_attempts: int = 3
    min_results: int = 5
    target_confidence: float = 0.8
    max_provider_calls: int = 200
    time_budget_ms: Optional[int] = None

    executor: ExecutorPolicy = field(default_factory=ExecutorPolicy)
    executor_by_domain: Dict[str, ExecutorPolicy] = field(default_factory=dict)

    def executor_for(self, domain: str) -> ExecutorPolicy:
        return self.executor_by_domain.get(domain, self.executor)


def default_loop_policy() -> LoopPolicy:
    """
    Single source of truth defaults:
      - bundle domain: allow hotel.search + flight.search concurrently (amadeus=2)
      - hotel_only/flight_only: amadeus=1
    """
    base_exec = ExecutorPolicy(
        max_workers=8,
        default_resource_limit=1,
        resource_limits={"provider:amadeus": 1},
    )

    return LoopPolicy(
        max_attempts=3,
        min_results=5,
        target_confidence=0.8,
        max_provider_calls=200,
        time_budget_ms=None,
        executor=base_exec,
        executor_by_domain={
            "hotel_only": ExecutorPolicy(
                max_workers=6,
                default_resource_limit=1,
                resource_limits={"provider:amadeus": 1},
            ),
            "flight_only": ExecutorPolicy(
                max_workers=6,
                default_resource_limit=1,
                resource_limits={"provider:amadeus": 1},
            ),
            "bundle": ExecutorPolicy(
                max_workers=8,
                default_resource_limit=1,
                resource_limits={"provider:amadeus": 2},
            ),
        },
    )
