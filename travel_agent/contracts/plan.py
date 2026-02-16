# travel-agent/contracts/plan.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Step:
    """
    A unit of work that an agent can execute.
    - tool: identifies which agent should handle this step (via registry)
    - depends_on: enables DAG execution later (parallel upgrade)
    - resources: enables coarse resource locks later (parallel upgrade)
    """
    id: str
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    timeout_s: Optional[int] = None


@dataclass
class Plan:
    steps: List[Step]
    notes: List[str] = field(default_factory=list)
    stop_if: Dict[str, Any] = field(default_factory=dict)

