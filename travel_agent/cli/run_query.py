# travel_agent/cli/run_query.py
from __future__ import annotations

import sys
from datetime import datetime, timedelta
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
        # Always process hotels for hotel-only and bundle queries
        if ctx.intent.domain in ["hotel_only", "bundle"]:
            hotel_rows = []
            hotels = getattr(ctx, "hotels", [])
            
            # Calculate trip days for per-day pricing
            check_in = getattr(ctx.intent, "check_in", None)
            check_out = getattr(ctx.intent, "check_out", None)
            trip_days = 1  # Default to 1 day
            if check_in and check_out:
                trip_days = (check_out - check_in).days
                if trip_days <= 0:
                    trip_days = 1
            
            for h in hotels:
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
            if hotel_rows:
                print("\nHotels found:")
                print(tabulate(hotel_rows, headers=["Hotel", "Price (USD)", "Price Per Day (USD)", "Stars", "Rating", "Area"], tablefmt="grid"))
            else:
                print("No hotels found.\n")
    except Exception as e:
        print(f"[Hotel tabulate failed: {e}]\n")

    try:
        # Always process flights for flight-only and bundle queries
        if ctx.intent.domain in ["flight_only", "bundle"]:
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
            if flight_rows:
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
                # Simple ISO date parsing
                from datetime import datetime as dt
                return dt.fromisoformat(v)
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
    
    # Only set defaults if dates are actually missing
    if not intent.depart_date:
        intent.depart_date = default_depart
        # Only set return date for non-flight-only domains when trip_days is available
        if not intent.return_date and intent.domain != "flight_only":
            trip_days = getattr(intent, "trip_days", None)
            if trip_days and trip_days > 0:
                intent.return_date = intent.depart_date + timedelta(days=trip_days)
    
    # Set check_in/check_out based on depart_date/return_date, not defaults
    if not intent.check_in:
        intent.check_in = intent.depart_date
    if not intent.check_out:
        # For flight-only, only set check_out if return_date exists
        if intent.domain == "flight_only":
            intent.check_out = intent.return_date
        else:
            # For hotels/bundles, use return_date or trip_days if available
            if intent.return_date:
                intent.check_out = intent.return_date
            else:
                trip_days = getattr(intent, "trip_days", None)
                if trip_days and trip_days > 0:
                    intent.check_out = intent.check_in + timedelta(days=trip_days)
                else:
                    # If no trip_days can be parsed, set a reasonable default (1 day for hotels)
                    if intent.domain == "hotel_only":
                        intent.check_out = intent.check_in + timedelta(days=1)
                    elif intent.domain == "bundle":
                        intent.check_out = intent.check_in + timedelta(days=3)

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
    
    def _format_processed_input(intent):
        """Format processed input parameters in a user-friendly way"""
        domain = intent.domain
        
        # Extract constraints/enrichments
        constraints = getattr(intent, "constraints", None)
        enrichments = []
        
        if constraints:
            if hasattr(constraints, "budget_usd") and constraints.budget_usd:
                enrichments.append(f"Budget: ${constraints.budget_usd}")
            if hasattr(constraints, "nonstop") and constraints.nonstop:
                enrichments.append("Non-stop")
            if hasattr(constraints, "cabin") and constraints.cabin:
                enrichments.append(f"Cabin: {constraints.cabin}")
        
        enrichment_str = f" ({', '.join(enrichments)})" if enrichments else ""
        
        # Helper to get city name from IATA code
        def get_city_from_iata(iata_code):
            if not iata_code or iata_code == "Unknown":
                return "Unknown"
            # Simple reverse lookup - could be enhanced with a proper mapping
            city_mappings = {
                "BOS": "Boston", "BWI": "Baltimore", "SAN": "San Diego", 
                "MIA": "Miami", "LAX": "Los Angeles", "NYC": "New York",
                "JFK": "New York", "SFO": "San Francisco", "ORD": "Chicago",
                "ATL": "Atlanta", "DFW": "Dallas", "DEN": "Denver"
            }
            return city_mappings.get(iata_code, iata_code)
        
        if domain == "hotel_only":
            city = intent.city or "Unknown"
            check_in = intent.check_in
            check_out = intent.check_out
            return f"Hotel Only - City: {city}, Check-in: {check_in}, Check-out: {check_out}{enrichment_str}"
        
        elif domain == "flight_only":
            origin = intent.origin or "Unknown"
            destination = intent.destination or "Unknown"
            depart_date = intent.check_in  # For flights, check_in is depart_date
            return_date = intent.check_out  # For flights, check_out is return_date
            
            origin_city = get_city_from_iata(origin)
            dest_city = get_city_from_iata(destination)
            
            result = f"Flight Only - From: {origin_city} ({origin}), To: {dest_city} ({destination}), Departure: {depart_date}"
            if return_date:
                result += f", Return: {return_date}"
            result += enrichment_str
            return result
        
        elif domain == "bundle":
            origin = intent.origin or "Unknown"
            destination = intent.destination or "Unknown"
            city = intent.city or "Unknown"
            check_in = intent.check_in
            check_out = intent.check_out
            depart_date = check_in  # For bundles, check_in is also depart_date
            return_date = intent.check_out  # For bundles, check_out is also return_date
            
            origin_city = get_city_from_iata(origin)
            dest_city = get_city_from_iata(destination)
            
            result = f"Bundle - From: {origin_city} ({origin}), To: {dest_city} ({destination}), Hotel's City: {city}, Check-in: {check_in}, Check-out: {check_out}"
            if return_date and return_date != check_out:
                result += f", Return: {return_date}"
            result += enrichment_str
            return result
        
        else:
            return f"Processed Input - Domain: {domain}, Parameters: {vars(intent)}"

    from travel_agent.orchestrator.registry import Registry
    registry = Registry(planner=None, agents=agents)

    # -------------------------------
    # INTENT NORMALIZATION PATCH
    # -------------------------------

    # Patch ctx.intent directly
    ctx.intent = intent
    
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

    # Print final processed input in user-friendly format
    print("Processed Input Parameters")
    print(_format_processed_input(intent))

    # -----------------------------
    # Validate input before processing
    # -----------------------------
    validation_errors = []
    
    # Validate airports for flight-only and bundle queries
    if intent.domain in ["flight_only", "bundle"]:
        origin = intent.origin
        destination = intent.destination
        
        # Check if origin is missing or invalid
        if not origin or origin == "Unknown":
            validation_errors.append("Invalid or missing origin airport")
        # Check if destination is missing or invalid  
        if not destination or destination == "Unknown":
            validation_errors.append("Invalid or missing destination airport")
    
    # Validate city for hotel-only and bundle queries
    if intent.domain in ["hotel_only", "bundle"]:
        city = intent.city
        if city and city == "Unknown":
            validation_errors.append("Invalid city")
    
    # If validation errors exist, stop processing and show error
    if validation_errors:
        print("\n❌ Input Validation Error:")
        for error in validation_errors:
            print(f"   • {error}")
        print("\nPlease check your input and try again.")
        print("Examples:")
        print("   • 'flights from Boston to San Diego'")
        print("   • 'hotels in New York for 3 days'")
        print("   • 'flights and hotels in Chicago'")
        return

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