# travel_agent/orchestrator/exec/sequential.py
"""
Sequential Executor for Travel Agent Orchestrator.

This module provides a simple sequential execution engine that processes plan steps
in order, publishing events and handling failures with a fail-fast strategy.
"""

from __future__ import annotations

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan, Step
from travel_agent.contracts.events import make_event, EVENT_STEP_STARTED, EVENT_STEP_COMPLETED, EVENT_STEP_FAILED
from travel_agent.orchestrator.bus.topics import STEP_STARTED, STEP_COMPLETED, STEP_FAILED
from travel_agent.orchestrator.registry import Registry


class SequentialExecutor:
    """
    Executes plan steps sequentially in the order they appear in the plan.
    
    Each step is executed by its corresponding agent, with events published
    to both the message bus (if available) and the context event log.
    """
    
    def __init__(self, registry: Registry, bus=None) -> None:
        self.registry = registry
        self.bus = bus

    def execute(self, plan: Plan, ctx: SharedContext) -> None:
        """Execute all steps in the plan sequentially."""
        for step in plan.steps:
            self._run_step(step, ctx)

    def _run_step(self, step: Step, ctx: SharedContext) -> None:
        """Execute a single step with event publishing and error handling."""
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
            # Fail-fast by default; this can be evolved to continue based on policies
            raise
