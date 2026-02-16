# travel_agent/orchestrator/planner.py
from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional, Tuple

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan, Step
from travel_agent.agents.planner.rule_based import RuleBasedPlanner


def _domain(ctx: SharedContext) -> str:
    try:
        return str(getattr(getattr(ctx, "intent", None), "domain", None) or "hotel_only")
    except Exception:
        return "hotel_only"


def _get_steps(plan: Plan) -> List[Step]:
    s = getattr(plan, "steps", None)
    return list(s or [])


def _set_steps_in_place(plan: Plan, steps: List[Step]) -> bool:
    """
    Best-effort: if Plan is mutable, update it in place and return True.
    """
    try:
        setattr(plan, "steps", steps)
        return True
    except Exception:
        return False


def _rebuild_plan_with_steps(plan: Plan, steps: List[Step]) -> Plan:
    """
    Defensive rebuild:
      - Try Plan(steps=..., <copy other ctor fields if present>)
      - If that fails, return original plan after best-effort in-place set (or original)
    """
    if _set_steps_in_place(plan, steps):
        return plan

    # Try reconstructing with signature filtering
    try:
        sig = inspect.signature(Plan)
        allowed = set(sig.parameters.keys())
        kwargs: Dict[str, Any] = {}

        # If Plan is a dataclass, vars(plan) usually works; otherwise fallback to getattr
        try:
            attrs = vars(plan)
        except Exception:
            attrs = {}

        for k in allowed:
            if k == "steps":
                continue
            if k in attrs:
                kwargs[k] = attrs[k]
            else:
                try:
                    kwargs[k] = getattr(plan, k)
                except Exception:
                    pass

        kwargs["steps"] = steps
        return Plan(**kwargs)  # type: ignore[arg-type]
    except Exception:
        # Last resort: return original plan untouched
        return plan


def _priority(step_id: str) -> int:
    """
    Lower is earlier. Unknown steps keep relative order after known ones.
    """
    order = {
        # For bundle: make sure we search before compose/rank/validate
        "flight.search": 10,
        "hotel.search": 20,
        "bundle.compose": 30,
        "bundle.rank": 40,
        "critic.cost_guard": 80,
        "critic.validate": 90,
    }
    return order.get(step_id, 1000)


def _stable_reorder(steps: List[Step]) -> List[Step]:
    indexed = list(enumerate(steps))
    indexed.sort(key=lambda t: (_priority(getattr(t[1], "id", "")), t[0]))
    return [s for _, s in indexed]


def _filter_for_bundle_impossible(steps: List[Step]) -> List[Step]:
    """
    When previous attempt determined 'bundle_impossible' (no flights),
    skip expensive hotel/bundle work and focus on flight search + cheap critics.
    """
    keep_ids = {
        "flight.search",
        "critic.cost_guard",
        "critic.validate",
    }
    out: List[Step] = []
    for s in steps:
        sid = str(getattr(s, "id", "") or "")
        if sid in keep_ids:
            out.append(s)
    # If for some reason flight.search is missing, fall back to original (don't brick the run)
    if any(getattr(s, "id", "") == "flight.search" for s in out):
        return out
    return steps


class DefaultPlanner:
    name = "planner.default"

    def __init__(self) -> None:
        self._base = RuleBasedPlanner()

    def make_plan(self, ctx: SharedContext) -> Plan:
        plan = self._base.make_plan(ctx)

        # Only adapt ordering/selection for bundle domain
        if _domain(ctx) != "bundle":
            return plan

        steps = _get_steps(plan)

        # 1) Always reorder so we don't compose/validate before searches
        steps = _stable_reorder(steps)

        # 2) If previous attempt proved bundle is impossible (no flights),
        #    short-circuit expensive hotel retries in the next attempt.
        #    (flight_agent sets ctx.scratch['bundle_impossible']=True on NO_RESULTS)
        try:
            bundle_impossible = bool((ctx.scratch or {}).get("bundle_impossible") is True)
        except Exception:
            bundle_impossible = False

        if bundle_impossible:
            steps = _filter_for_bundle_impossible(steps)

        return _rebuild_plan_with_steps(plan, steps)