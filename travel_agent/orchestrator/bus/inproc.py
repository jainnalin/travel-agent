#travel_agent/orchestrator/bus/inproc.py

from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, List

from .base import BusBase, BusHandler


class InProcBus(BusBase):
    """
    Minimal in-process pub/sub bus.
    - synchronous delivery
    """
    def __init__(self) -> None:
        self._subs: DefaultDict[str, List[BusHandler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: BusHandler) -> None:
        self._subs[topic].append(handler)

    def publish(self, topic: str, message: object) -> None:
        for h in list(self._subs.get(topic, [])):
            h(message)
