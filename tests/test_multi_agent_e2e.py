# tests/test_multi_agent_e2e.py

import sys
import types
from types import SimpleNamespace

# --------------------------------------------------
# Stub external dependencies
# --------------------------------------------------

# Geo resolver stub
resolver_stub = types.SimpleNamespace(
    resolve_city=lambda city: (28.5383, -81.3792),
    resolve_airport=lambda code: (28.4312, -81.3081),
)

import travel_agent.tools.geo
travel_agent.tools.geo.resolver = resolver_stub

# Flight ranking stub
import travel_agent.tools.ranking.flight
travel_agent.tools.ranking.flight.score = (
    lambda f: 1.0 + getattr(f, "price_rank", 0) * 0.1
)

# Hotel ranking stub
import travel_agent.tools.ranking.hotel
travel_agent.tools.ranking.hotel.score = (
    lambda h: 1.0 + getattr(h, "rating", 0) / 5.0
)

# Bundle ranking stub
import travel_agent.tools.ranking.bundle
travel_agent.tools.ranking.bundle.score = (
    lambda b: (getattr(b.hotel, "score", 1.0) + getattr(b.flight, "score", 1.0)) / 2
)

# ✅ Scoring normalize stub (REQUIRED)
import travel_agent.tools.ranking.scoring
travel_agent.tools.ranking.scoring.normalize = lambda x: x

# --------------------------------------------------
# Imports
# --------------------------------------------------
from travel_agent.agents.support.geo_agent import GeoAgent
from travel_agent.agents.support.enrich_agent import EnrichAgent
from travel_agent.agents.planner.llm_based import LLMPlannerAgent
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.result import HotelResult, FlightResult, BundleResult

# --------------------------------------------------
# Dummy artifacts
# --------------------------------------------------
def make_dummy_hotels():
    h1 = HotelResult(provider="dummy", hotel_name="Hotel A")
    h1.city = "Orlando"
    h1.latitude = 28.5383
    h1.longitude = -81.3792
    h1.rating = 4.2
    h1.free_cancellation = True
    h1.board_type = "BREAKFAST"
    h1.total_price = SimpleNamespace(amount=150.0)

    h2 = HotelResult(provider="dummy", hotel_name="Hotel B")
    h2.city = "Orlando"
    h2.latitude = 28.5400
    h2.longitude = -81.3800
    h2.rating = 3.8
    h2.free_cancellation = False
    h2.board_type = "ROOM_ONLY"
    h2.total_price = SimpleNamespace(amount=120.0)

    return [h1, h2]


def make_dummy_flights():
    f1 = FlightResult(provider="dummy", flight_number="F100")
    f1.origin = "JFK"
    f1.destination = "MCO"
    f1.price_rank = 1
    f1.stops = 0
    f1.duration_minutes = 180

    f2 = FlightResult(provider="dummy", flight_number="F200")
    f2.origin = "JFK"
    f2.destination = "MCO"
    f2.price_rank = 2
    f2.stops = 1
    f2.duration_minutes = 240

    return [f1, f2]


def make_dummy_bundles(hotels, flights):
    b1 = BundleResult(provider="dummy")
    b1.hotel = hotels[0]
    b1.flight = flights[0]

    b2 = BundleResult(provider="dummy")
    b2.hotel = hotels[1]
    b2.flight = flights[1]

    return [b1, b2]


# --------------------------------------------------
# Main E2E test
# --------------------------------------------------
def main():
    # Intent stub (IMPORTANT: include domain)
    intent = SimpleNamespace(
        domain="bundle",
        city="Orlando",
        origin="JFK",
        destination="MCO",
        trip_type="family",
        constraints={"enrich_top_n": 2},
    )

    ctx = SharedContext(run_id="test-e2e", intent=intent)

    # Attach artifacts
    ctx.hotels = make_dummy_hotels()
    ctx.flights = make_dummy_flights()
    ctx.bundles = make_dummy_bundles(ctx.hotels, ctx.flights)

    # -------------------------------
    # LLM Planner
    # -------------------------------
    planner = LLMPlannerAgent()
    plan = planner.run(ctx)

    print("=== Plan Steps ===")
    for step in plan.steps:
        print(f"id={step.id}, tool={step.tool}, args={step.args}, depends_on={step.depends_on}")

    # -------------------------------
    # GeoAgent
    # -------------------------------
    geo_agent = GeoAgent()
    geo_agent.run(ctx)

    print("\n=== GeoAgent Enriched Hotels ===")
    for h in ctx.hotels:
        print(
            f"{h.hotel_name}: "
            f"center={getattr(h, 'distance_to_center_km', 0):.2f}km, "
            f"airport={getattr(h, 'distance_to_airport_km', 0):.2f}km"
        )

    # -------------------------------
    # EnrichAgent
    # -------------------------------
    enrich_agent = EnrichAgent()
    enrich_agent.run(ctx)

    print("\n=== EnrichAgent Scores ===")
    for h in ctx.hotels:
        print(f"Hotel {h.hotel_name} score={h.score:.3f}")
    for f in ctx.flights:
        print(f"Flight {f.origin}->{f.destination} score={f.score:.3f}")
    for b in ctx.bundles:
        print(
            f"Bundle {b.flight.origin}->{b.flight.destination} + "
            f"{b.hotel.hotel_name} score={b.score:.3f}"
        )


if __name__ == "__main__":
    main()