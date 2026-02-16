#travel_agent/orchestrator/bus/base.py

from __future__ import annotations
from typing import Callable

BusHandler = Callable[[object], None]


class BusBase:
    def subscribe(self, topic: str, handler: BusHandler) -> None:
        raise NotImplementedError

    def publish(self, topic: str, message: object) -> None:
        raise NotImplementedError
