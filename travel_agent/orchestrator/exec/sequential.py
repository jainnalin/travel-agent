#travel_agent/orchestrator/exec/sequential.py

from __future__ import annotations

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan, Step
from travel_agent.contracts.events import make_event, EVENT_STEP_STARTED, EVENT_STEP_COMPLETED, EVENT_STEP_FAILED
from travel_agent.orchestrator.bus.topics import STEP_STARTED, STEP_COMPLETED, STEP_FAILED
from travel_agent.orchestrator.registry import Registry


class SequentialExecutor:
    def __init__(self, registry: Registry, bus=None) -> None:
        self.registry = registry
        self.bus = bus

    def execute(self, plan: Plan, ctx: SharedContext) -> None:
        for step in plan.steps:
            self._run_step(step, ctx)

    def _run_step(self, step: Step, ctx: SharedContext) -> None:
        agent = self.registry.get_agent_for_tool(step.tool)

        if self.bus:
            self.bus.publish(STEP_STARTED, {"step_id": step.id, "tool": step.tool, "agent": agent.name})
        ctx.events.append(make_event(EVENT_STEP_STARTED, step_id=step.id, tool=step.tool, agent=agent.name))

        try:
            agent.run(ctx, step)
            if self.bus:
                self.bus.publish(STEP_COMPLETED, {"step_id": step.id, "tool": step.tool, "agent": agent.name})
            ctx.events.append(make_event(EVENT_STEP_COMPLETED, step_id=step.id, tool=step.tool, agent=agent.name))
        except Exception as e:
            if self.bus:
                self.bus.publish(STEP_FAILED, {"step_id": step.id, "tool": step.tool, "agent": agent.name, "error": str(e)})
            ctx.events.append(make_event(EVENT_STEP_FAILED, step_id=step.id, tool=step.tool, agent=agent.name, error=str(e)))
            # Fail-fast by default; you can evolve this to “continue” based on policies
            raise
