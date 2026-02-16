# travel_agent/agents/planner/adaptive.py

from __future__ import annotations

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan


class AdaptivePlanner:
    def apply(self, ctx: SharedContext, plan: Plan) -> Plan:
        relax = bool(int(getattr(ctx, "attempt", 0)) > 0)

        for s in plan.steps:
            if s.tool in ("hotel.search", "flight.search"):
                s.args["relax"] = relax

        return plan

