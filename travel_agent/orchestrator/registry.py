# travel_agent/orchestrator/registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from travel_agent.agents.base import AgentBase


@dataclass
class Registry:
    """
    Registry holds planner + agents and provides a stable step_id -> agent lookup.

    IMPORTANT: This file must preserve the public API expected by wiring.py
    (e.g. build_registry()).
    """
    planner: Any
    agents: List[AgentBase]

    # internal mapping: step_id -> agent
    _agent_by_step: Dict[str, AgentBase] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._rebuild_agent_index()

    def _rebuild_agent_index(self) -> None:
        m: Dict[str, AgentBase] = {}
        for a in self.agents or []:
            try:
                keys = a.handles()  # expected list[str]
            except Exception:
                keys = []
            for k in keys or []:
                if not k:
                    continue
                k = str(k)
                # first-wins to avoid surprising overrides
                if k not in m:
                    m[k] = a
        self._agent_by_step = m

    # ---- Stable lookup used by executors ----

    def get_agent_for_step(self, step_id: str) -> AgentBase:
        step_id = str(step_id)
        a = (self._agent_by_step or {}).get(step_id)
        if a is None:
            # match your current error shape closely
            raise KeyError(f"Registry cannot resolve agent for step_id={step_id!r}")
        return a

    # common aliases (defensive, prevents future breakage)
    def agent_for(self, step_id: str) -> AgentBase:
        return self.get_agent_for_step(step_id)

    def resolve(self, step_id: str) -> AgentBase:
        return self.get_agent_for_step(step_id)

    def list_steps(self) -> List[str]:
        return sorted(list((self._agent_by_step or {}).keys()))

    def add_agent(self, agent: AgentBase) -> None:
        self.agents.append(agent)
        # incremental update; rebuild on any unexpected behavior
        try:
            for k in agent.handles() or []:
                if k and str(k) not in self._agent_by_step:
                    self._agent_by_step[str(k)] = agent
        except Exception:
            self._rebuild_agent_index()


# ---- Backward-compatible constructor expected by wiring.py ----

def build_registry(*, planner: Any, agents: List[AgentBase]) -> Registry:
    """
    Preserve existing import path:
      from travel_agent.orchestrator.registry import build_registry
    """
    return Registry(planner=planner, agents=agents)