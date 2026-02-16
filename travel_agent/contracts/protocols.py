# travel-agent/contracts/protocols.py

from __future__ import annotations

from typing import Protocol, Callable, Iterable, Optional

from .context import SharedContext
from .plan import Step, Plan


class Agent(Protocol):
    name: str

    def handles(self) -> Iterable[str]:
        ...

    def run(self, ctx: SharedContext, step: Step) -> None:
        ...


class Planner(Protocol):
    name: str

    def make_plan(self, ctx: SharedContext) -> Plan:
        ...


BusHandler = Callable[[object], None]


class Bus(Protocol):
    def subscribe(self, topic: str, handler: BusHandler) -> None:
        ...

    def publish(self, topic: str, message: object) -> None:
        ...
