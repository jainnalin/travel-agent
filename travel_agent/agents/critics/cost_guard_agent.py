#travel-agent/travel_agent/agents/critics/cost_guard_agent.py
from __future__ import annotations

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Step
from travel_agent.contracts.warnings import Warning, WarningCode


class CostGuardAgent(AgentBase):
    """
    Simple deterministic guardrail for provider-call budget.

    It does NOT emit verdicts.
    It only raises warnings + nudges confidence down if budget is exceeded.
    """
    name = "agent.cost_guard"

    def handles(self):
        return ["critic.cost_guard"]

    def run(self, ctx: SharedContext, step: Step) -> None:
        # Where budget comes from:
        budget = getattr(ctx, "max_provider_calls", None)
        if budget is None:
            try:
                budget = int((ctx.scratch or {}).get("max_provider_calls"))
            except Exception:
                budget = None

        # Where usage comes from (best-effort across ctx implementations):
        used = _total_provider_calls(ctx)

        # Store cost tracking information for audit and debugging purposes
        ctx.scratch.setdefault("cost_guard", {})
        ctx.scratch["cost_guard"]["used"] = int(used)
        ctx.scratch["cost_guard"]["budget"] = int(budget) if budget is not None else None

        if budget is None:
            # No configured budget -> do nothing
            return

        if used <= int(budget):
            return

        # Budget exceeded -> warning + degrade confidence, but don't hard-fatal.
        ctx.add_warning(
            Warning(
                code=WarningCode.RATE_LIMITED,
                title="Provider call budget exceeded",
                details={
                    "used": int(used),
                    "budget": int(budget),
                    "recommendation": "Reduce max_offers / relax passes, or increase max_provider_calls",
                },
                step_id=step.id,
                agent=self.name,
            )
        )
        ctx.confidence = min(float(getattr(ctx, "confidence", 1.0) or 0.0), 0.2)
        ctx.scratch["cost_guard"]["exceeded"] = True


def _total_provider_calls(ctx: SharedContext) -> int:
    # Common shape: ctx.provider_calls = {"amadeus": 3, ...}
    pc = getattr(ctx, "provider_calls", None)
    if isinstance(pc, dict):
        try:
            return int(sum(int(v) for v in pc.values() if v is not None))
        except Exception:
            pass

    # Fallback: some implementations keep counters in scratch
    sc = getattr(ctx, "scratch", {}) or {}
    if isinstance(sc, dict):
        for k in ("provider_calls_total", "provider_calls", "_provider_calls_total"):
            v = sc.get(k)
            try:
                if v is not None and str(v).isdigit():
                    return int(v)
            except Exception:
                pass

    return 0
