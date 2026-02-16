# travel_agent/orchestrator/exec/locks.py
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, Semaphore
from typing import Dict, Iterable, Iterator, List, Optional


@dataclass
class ResourcePolicy:
    """
    Controls concurrency for resources.

    Example resource strings used by planner:
      - "provider:amadeus"
      - "provider:sabre"
      - "db:cache"
      - "net:geocoder"
    """
    default_limit: int = 1
    limits: Optional[Dict[str, int]] = None


class ResourceLocks:
    """
    Named resource locks with configurable parallelism per resource.

    Key behavior:
      - Acquire multiple resources in sorted order to avoid deadlocks.
      - Release in reverse order.
    """

    def __init__(self, policy: Optional[ResourcePolicy] = None):
        self._policy = policy or ResourcePolicy()
        self._guard = Lock()
        self._semaphores: Dict[str, Semaphore] = {}

    def _limit_for(self, resource: str) -> int:
        if self._policy.limits and resource in self._policy.limits:
            return max(1, int(self._policy.limits[resource]))
        return max(1, int(self._policy.default_limit))

    def _get_sem(self, resource: str) -> Semaphore:
        with self._guard:
            sem = self._semaphores.get(resource)
            if sem is None:
                sem = Semaphore(self._limit_for(resource))
                self._semaphores[resource] = sem
            return sem

    @contextmanager
    def acquire(self, resources: Optional[Iterable[str]] = None) -> Iterator[None]:
        res_list: List[str] = sorted([r for r in (resources or []) if r])

        sems: List[Semaphore] = []
        try:
            for r in res_list:
                sem = self._get_sem(r)
                sem.acquire()   # blocks cleanly (no busy spin)
                sems.append(sem)
            yield
        finally:
            for sem in reversed(sems):
                sem.release()
