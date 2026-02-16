# travel_agent/agents/listeners/debug_listener.py

from __future__ import annotations

from travel_agent.orchestrator.bus.topics import PLAN_READY, STEP_STARTED, STEP_COMPLETED, STEP_FAILED, VERDICT


class DebugListener:
    name = "listener.debug"

    def attach(self, bus) -> None:
        bus.subscribe(PLAN_READY, self.on_plan)
        bus.subscribe(STEP_STARTED, self.on_step_started)
        bus.subscribe(STEP_COMPLETED, self.on_step_completed)
        bus.subscribe(STEP_FAILED, self.on_step_failed)
        bus.subscribe(VERDICT, self.on_verdict)

    def on_plan(self, msg):
        print(f"[plan] attempt={msg.get('attempt')} steps={msg.get('steps')}")

    def on_step_started(self, msg):
        print(f"[step.start] {msg.get('step_id')} tool={msg.get('tool')} agent={msg.get('agent')}")

    def on_step_completed(self, msg):
        print(f"[step.done]  {msg.get('step_id')}")

    def on_step_failed(self, msg):
        print(f"[step.fail]  {msg.get('step_id')} error={msg.get('error')}")

    def on_verdict(self, msg):
        print(f"[verdict] {msg.get('verdict')} reason={msg.get('reason')} conf={msg.get('confidence')}")
