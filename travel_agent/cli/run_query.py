# travel_agent/cli/run_query.py
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from dateutil.parser import parse as parse_date
from tabulate import tabulate

from travel_agent.nlp.rule_parser import parse_text_to_intent
from travel_agent.orchestrator.loop import run_loop
from travel_agent.orchestrator.policies import default_loop_policy
from travel_agent.runtime.run_id import new_run_id
from travel_agent.contracts.context import SharedContext

# Import agents for registry
from travel_agent.agents.planner.llm_based import LLMPlannerAgent
from travel_agent.agents.executors.hotel_agent import HotelSearchAgent
from travel_agent.agents.executors.flight_agent import FlightSearchAgent
from travel_agent.agents.executors.bundle_agent import BundleComposeAgent
from travel_agent.agents.critics.ranker_agent import SimpleRankerAgent
from travel_agent.agents.critics.validator_agent import ValidatorAgent
from travel_agent.agents.critics.cost_guard_agent import CostGuardAgent
from travel_agent.agents.support.geo_agent import GeoAgent

# -----------------------------
# Safe helpers
# -----------------------------
def safe_price(obj):
    if obj is None:
        return None
    if hasattr(obj, "total_price"):
        tp = getattr(obj, "total_price")
        return getattr(tp, "amount", tp)
    if hasattr(obj, "price"):
        p = getattr(obj, "price")
        return getattr(p, "amount", p)
    if hasattr(obj, "amount"):
        return getattr(obj, "amount")
    return None

# -----------------------------
# Main Runner
# -----------------------------
def _print_debug(*, intent, ctx) -> None:
    """Print debug info; robust to non-serializable values."""
    try:
        print("\n--- Parsed Intent ---")
        print(f"Raw text: {intent.raw_text}")
        print(f"Domain: {intent.domain}")
        print(f"Origin: {intent.origin} Destination: {intent.destination}")
        print(f"City: {intent.city}")
        print(f"Check-in: {intent.check_in} Check-out: {intent.check_out}")
        print(f"Trip days: {getattr(intent, 'trip_days', None)}")
        print(f"Passengers: {intent.passengers}")
        print("----------------------")
        print("\n--- Raw Agent Outputs ---")
        hotels = getattr(ctx, "hotels", None)
        flights = getattr(ctx, "flights", None)
        print("Hotels raw:", len(hotels) if hotels else 0)
        print("Flights raw:", len(flights) if flights else 0)
        print("Events collected:", len(ctx.events))
        warnings = getattr(ctx, "warnings", None)
        print("Warnings:", [str(w) for w in warnings] if warnings else [])
        print("----------------------------\n")
    except Exception as e:
        print(f"[Debug print failed: {e}]\n")


def _print_results(ctx) -> None:
    """Tabulate hotels and flights; robust to malformed data."""
    try:
        if getattr(ctx, "hotels", None):
            hotel_rows = []
            # Calculate trip days for per-day pricing
            check_in = getattr(ctx.intent, "check_in", None)
            check_out = getattr(ctx.intent, "check_out", None)
            trip_days = 1  # Default to 1 day
            if check_in and check_out:
                trip_days = (check_out - check_in).days
                if trip_days <= 0:
                    trip_days = 1
            
            for h in ctx.hotels:
                total_price = safe_price(h) or 0
                per_day_price = total_price / trip_days if trip_days > 0 else total_price
                hotel_rows.append([
                    str(getattr(h, "hotel_name", "N/A") or "N/A"),
                    str(total_price),
                    str(f"{per_day_price:.2f}"),
                    str(getattr(h, "stars", "N/A") or "N/A"),
                    str(getattr(h, "rating", "N/A") or "N/A"),
                    str(getattr(h, "area", "N/A") or "N/A"),
                ])
            print("\nHotels found:")
            print(tabulate(hotel_rows, headers=["Hotel", "Price (USD)", "Price Per Day (USD)", "Stars", "Rating", "Area"], tablefmt="grid"))
        else:
            print("No hotels found.\n")
    except Exception as e:
        print(f"[Hotel tabulate failed: {e}]\n")

    try:
        if getattr(ctx, "flights", None):
            flight_rows = []
            flights = getattr(ctx, "flights", [])
            for f in flights:
                flight_rows.append([
                    str(getattr(f, "origin", "N/A") or "N/A"),
                    str(getattr(f, "destination", "N/A") or "N/A"),
                    str(getattr(f, "depart_time", "N/A") or "N/A"),
                    str(getattr(f, "arrive_time", "N/A") or "N/A"),
                    str(safe_price(f) or "N/A"),
                ])
            print("\nFlights found:")
            print(tabulate(flight_rows, headers=["Origin", "Destination", "Depart", "Arrive", "Price (USD)"], tablefmt="grid"))
        else:
            print("No flights found.\n")
    except Exception as e:
        print(f"[Flight tabulate failed: {e}]\n")


def run_text_query(text: str, *, debug: bool = False) -> None:
    text = text.strip()
    if not text:
        print("Error: empty query")
        return

    # Parse user query into intent
    intent = parse_text_to_intent(text)
    policy = default_loop_policy()

    # -----------------------------
    # Convert NL date strings to datetime
    # -----------------------------
    from datetime import date as date_type

    def _to_datetime(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, date_type):
            return datetime(v.year, v.month, v.day)
        if isinstance(v, str):
            try:
                return parse_date(v, fuzzy=True)
            except Exception:
                return None
        return None

    for attr in ["depart_date", "return_date", "check_in", "check_out"]:
        parsed = _to_datetime(getattr(intent, attr, None))
        if parsed is not None:
            setattr(intent, attr, parsed)
        elif getattr(intent, attr, None) is not None and not isinstance(getattr(intent, attr), (datetime, date_type)):
            setattr(intent, attr, None)  # clear unparseable string

    today = date_type.today()
    default_depart = datetime(today.year, today.month, today.day)
    if not intent.depart_date:
        intent.depart_date = default_depart
    if not intent.return_date:
        intent.return_date = intent.depart_date + timedelta(days=4)
    if not intent.check_in:
        intent.check_in = intent.depart_date
    if not intent.check_out:
        intent.check_out = intent.check_in + timedelta(days=4)

    # -----------------------------
    # Fix destination & city for bundle trips
    # -----------------------------
    if intent.domain == "bundle":
        if not intent.destination:
            if "destination_city" in intent.meta:
                intent.destination = intent.meta["destination_city"]
            elif "to" in intent.raw_text.lower():
                parts = intent.raw_text.lower().split("to")
                intent.destination = parts[-1].strip().split()[0].capitalize()
        if not intent.city and intent.destination:
            intent.city = intent.destination

    # -----------------------------
    # Initialize shared context
    # -----------------------------
    ctx = SharedContext(
        run_id=new_run_id(prefix="travel"),
        intent=intent,
        attempt=0,
        max_attempts=policy.max_attempts,
        max_provider_calls=policy.max_provider_calls,
        time_budget_ms=policy.time_budget_ms,
        scratch={"_debug": debug},
        events=[],
        warnings=[],
        confidence=0.0,
    )

    # -----------------------------
    # Build agents registry
    # -----------------------------
    agents = [
        LLMPlannerAgent(),
        HotelSearchAgent(),
        FlightSearchAgent(),
        BundleComposeAgent(),
        SimpleRankerAgent(),
        ValidatorAgent(),
        CostGuardAgent(),
        GeoAgent(),
    ]
    from travel_agent.orchestrator.registry import Registry
    registry = Registry(planner=None, agents=agents)

    print("=== INTENT BEFORE LOOP ===")
    print(f"domain={ctx.intent.domain}, city={ctx.intent.city}, destination={ctx.intent.destination}, "
          f"check_in={ctx.intent.check_in}, check_out={ctx.intent.check_out}")

    # -------------------------------
    # INTENT NORMALIZATION PATCH
    # -------------------------------

    # Patch ctx.intent directly
    ctx.intent = intent
    original_intent = intent  # Store original intent for hotel agent
    
    # Preserve constraints during normalization
    original_constraints = getattr(intent, 'constraints', None)
    
    # Normalize hotel-only queries
    if ctx.intent.domain == "hotel_only":
        if not ctx.intent.city and ctx.intent.destination:
            ctx.intent.city = ctx.intent.destination
    # Normalize flight-only queries
    if ctx.intent.domain == "flight_only":
        # Ensure destination exists
        if not ctx.intent.destination and ctx.intent.city:
            ctx.intent.destination = ctx.intent.city
    
    # Normalize bundle queries (already mostly correct, but safe)
    if ctx.intent.domain == "bundle":
        if not ctx.intent.city and ctx.intent.destination:
            ctx.intent.city = ctx.intent.destination
    
    # Restore constraints after normalization
    if original_constraints:
        ctx.intent.constraints = original_constraints

    # Final safety check logging
    print("=== NORMALIZED INTENT BEFORE LOOP ===")
    print(
        f"domain={intent.domain}, "
        f"origin={intent.origin}, "
        f"destination={intent.destination}, "
        f"city={intent.city}, "
        f"check_in={intent.check_in}, "
        f"check_out={intent.check_out}"
    )

    # -------------------------------
    # Normalize flight_only: extract "from X to Y" when NLP missed it
    # -------------------------------
    if intent.domain == "flight_only" and (not intent.origin or not intent.destination):
        import re
        m = re.search(r"\bfrom\s+([A-Za-z\s]+?)\s+to\s+([A-Za-z\s]+?)(?:\s+on|\s+for|\b|$)", intent.raw_text or "", re.I)
        if m:
            orig, dest = m.group(1).strip(), m.group(2).strip()
            if orig and dest:
                if not intent.origin:
                    intent.origin = orig.split()[0] if orig else None
                    intent.meta["origin_city"] = orig
                if not intent.destination:
                    intent.destination = dest.split()[0] if dest else None
                    intent.meta["destination_city"] = dest

    # -------------------------------
    # Run orchestrator loop
    # -------------------------------
    run_loop(ctx=ctx, registry=registry, policy=policy)

    # -----------------------------
    # Print debug (optional, robust)
    # -----------------------------
    debug = (ctx.scratch or {}).get("_debug", False)
    if debug:
        _print_debug(intent=intent, ctx=ctx)

    # -----------------------------
    # Tabulate results (robust)
    # -----------------------------
    _print_results(ctx)

# -----------------------------
# CLI Entry
# -----------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py 'your natural language query here'")
        sys.exit(1)

    query_text = " ".join(sys.argv[1:])
    run_text_query(query_text)