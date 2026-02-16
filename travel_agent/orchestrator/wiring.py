# travel_agent/orchestrator/wiring.py
from __future__ import annotations

from travel_agent.orchestrator.registry import Registry
from travel_agent.orchestrator.planner import DefaultPlanner

from travel_agent.agents.executors.hotel_agent import HotelSearchAgent
from travel_agent.agents.executors.flight_agent import FlightSearchAgent
from travel_agent.agents.executors.bundle_agent import BundleComposeAgent
from travel_agent.agents.critics.ranker_agent import SimpleRankerAgent
from travel_agent.agents.critics.validator_agent import ValidatorAgent
from travel_agent.agents.critics.cost_guard_agent import CostGuardAgent
from travel_agent.agents.support.geo_agent import GeoAgent
from travel_agent.agents.planner.llm_based import LLMPlannerAgent

def make_registry() -> Registry:
    """
    Build a fully working registry with all agents.
    Each agent must implement .run(ctx, **kwargs)
    """
    planner = DefaultPlanner()

    # Instantiate agents directly; they will read ctx.intent internally
    agents = [
        LLMPlannerAgent(),
        HotelSearchAgent(),
        FlightSearchAgent(),
        BundleComposeAgent(),
        SimpleRankerAgent(),
        ValidatorAgent(),
        CostGuardAgent(),
        GeoAgent(),  # <-- add GeoAgent here so 'geo_enrich' steps resolve
    ]

    registry = Registry(planner=planner, agents=agents)
    return registry


# Backwards-compatible alias
build_default_registry = make_registry
build_registry = make_registry  # <-- add this line