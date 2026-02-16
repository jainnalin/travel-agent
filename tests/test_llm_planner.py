# tests/test_llm_planner.py

import sys
from types import SimpleNamespace

# -----------------------------
# Dummy LLM output stub (optional)
# -----------------------------
def dummy_llm_response(intent):
    """
    Fake LLM response to simulate plan generation.
    Returns a list of steps.
    """
    if getattr(intent, "domain", None) == "hotel_only":
        return [{"action": "search_hotels", "city": intent.city}]
    elif getattr(intent, "domain", None) == "flight_only":
        return [{"action": "search_flights", "origin": intent.origin, "destination": intent.destination}]
    else:
        return [{"action": "noop"}]


# -----------------------------
# Patch LLMPlannerAgent for dummy run
# -----------------------------
import travel_agent.agents.planner.llm_based as llm_module

class LLMPlannerAgent(llm_module.LLMPlannerAgent):
    def run(self, ctx):
        # create empty Plan if steps missing
        from travel_agent.contracts.plan import Plan
        plan = Plan(steps=dummy_llm_response(ctx.intent))
        ctx.scratch["last_plan"] = plan
        return plan


# -----------------------------
# Test function
# -----------------------------
def main():
    # Minimal intents
    intent_hotel = SimpleNamespace(
        domain="hotel_only",
        city="Orlando"
    )
    intent_flight = SimpleNamespace(
        domain="flight_only",
        origin="JFK",
        destination="MCO"
    )

    from travel_agent.contracts.context import SharedContext

    # Shared contexts
    ctx_hotel = SharedContext(run_id="hotel-test", intent=intent_hotel)
    ctx_flight = SharedContext(run_id="flight-test", intent=intent_flight)

    # Initialize planner
    planner = LLMPlannerAgent()

    # Run hotel plan
    plan_hotel = planner.run(ctx_hotel)
    print("=== Hotel Plan Steps ===")
    for step in plan_hotel.steps:
        print(step)

    # Run flight plan
    plan_flight = planner.run(ctx_flight)
    print("\n=== Flight Plan Steps ===")
    for step in plan_flight.steps:
        print(step)


if __name__ == "__main__":
    main()