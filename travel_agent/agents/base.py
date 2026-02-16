# travel-agent/agents/base.py

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Step


class AgentBase(ABC):
    name: str = "agent"

    @abstractmethod
    def handles(self) -> Iterable[str]:
        ...

    @abstractmethod
    def run(self, ctx: SharedContext, step: Step) -> None:
        ...
