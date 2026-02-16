# tests/test_agents.py
import sys
import types
from types import SimpleNamespace

# -------------------------
# Patch Geo resolver stubs
# -------------------------
resolver_stub = types.SimpleNamespace(
    resolve_city=lambda city: (28.5383, -81.3792),      # Orlando city coords
    resolve_airport=lambda code: (28.4312, -81.3081)    # MCO airport coords
)

import travel_agent.tools.geo
travel_agent.tools.geo.resolver = resolver_stub

# -------------------------
# Patch ranking stubs
# -------------------------
import travel_agent.tools.ranking.flight as flight_ranking
import travel_agent.tools.ranking.hotel as hotel_ranking
import travel_agent.tools.ranking.bundle as bundle_ranking
import travel_agent.tools.ranking.scoring as scoring

flight_ranking.score = lambda f: 1.0
hotel_ranking.score = lambda h: 1.0
bundle_ranking.score = lambda b: 1.0
scoring.normalize = lambda x: x

# -------------------------
# Imports under patched environment
# -------------------------
from travel_agent.agents.support.geo_agent import GeoAgent
from travel_agent.agents.support.enrich_agent import EnrichAgent
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.result import HotelResult, FlightResult, BundleResult

# -------------------------
# Dummy artifact creators
# -------------------------
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

# -------------------------
# Main test
# -------------------------
def main():
    # Minimal intent stub
    intent = SimpleNamespace(
        city="Orlando",
        destination="MCO",
        trip_type="family",
        constraints={"enrich_top_n": 2},
    )

    # SharedContext
    ctx = SharedContext(run_id="test-run-1", intent=intent)

    # Add dummy artifacts
    ctx.hotels = make_dummy_hotels()
    ctx.flights = make_dummy_flights()
    ctx.bundles = make_dummy_bundles(ctx.hotels, ctx.flights)

    # --- Run GeoAgent ---
    geo_agent = GeoAgent()
    geo_agent.run(ctx)
    print("=== GeoAgent Enriched Hotels ===")
    for h in ctx.hotels:
        print(
            f"{h.hotel_name}: distance_to_center_km={getattr(h, 'distance_to_center_km', None):.2f}, "
            f"distance_to_airport_km={getattr(h, 'distance_to_airport_km', None):.2f}"
        )

    # --- Run EnrichAgent ---
    enrich_agent = EnrichAgent()
    enrich_agent.run(ctx)
    print("\n=== EnrichAgent Scores ===")
    for h in ctx.hotels:
        print(f"Hotel {h.hotel_name} score={h.score:.3f}")
    for f in ctx.flights:
        print(f"Flight {f.origin}->{f.destination} score={f.score:.3f}")
    for b in ctx.bundles:
        print(
            f"Bundle {b.flight.origin}->{b.flight.destination} + {b.hotel.hotel_name} score={b.score:.3f}"
        )


if __name__ == "__main__":
    main()