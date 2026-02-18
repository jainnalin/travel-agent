# travel_agent/agents/executors/bundle_agent.py
from __future__ import annotations

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.events import EVENT_TOOL_CALLED, EVENT_TOOL_RESULT, make_event
from travel_agent.contracts.plan import Step
from travel_agent.contracts.result import BundleResult, Money, FlightResult, HotelResult
from travel_agent.contracts.warnings import Warning, WarningCode


class BundleComposeAgent(AgentBase):
    name = "agent.bundle_compose"

    def handles(self):
        return ["bundle.compose", "compose_bundle"]

    def run(self, ctx: SharedContext, step: Step) -> None:
        # -----------------------
        # Config knobs (safe defaults)
        # -----------------------
        cap = int(ctx.scratch.get("bundle_cap") or 200)
        max_flights = int(ctx.scratch.get("bundle_max_flights") or 50)
        max_hotels = int(ctx.scratch.get("bundle_max_hotels") or 50)

        # Avoid weird values
        cap = max(0, cap)
        max_flights = max(0, max_flights)
        max_hotels = max(0, max_hotels)

        # Deterministic slices:
        # - we first sort to stabilize ordering across runs
        flights_sorted = _stable_sort_flights(list(ctx.flights))
        hotels_sorted = _stable_sort_hotels(list(ctx.hotels))

        flights_used = flights_sorted[:max_flights]
        hotels_used = hotels_sorted[:max_hotels]

        ctx.events.append(
            make_event(
                EVENT_TOOL_CALLED,
                tool="bundle.compose",
                args={
                    "bundle_cap": cap,
                    "max_flights": max_flights,
                    "max_hotels": max_hotels,
                },
            )
        )

        # If either side is empty, no bundles
        if not flights_used or not hotels_used or cap == 0:
            ctx.add_warning(
                Warning(
                    code=WarningCode.NO_RESULTS,
                    title="Cannot compose bundles",
                    details={
                        "flights_used": len(flights_used),
                        "hotels_used": len(hotels_used),
                        "cap": cap,
                    },
                    step_id=step.id,
                    agent=self.name,
                )
            )

            stats = _compose_stats(
                ctx=ctx,
                flights_used=len(flights_used),
                hotels_used=len(hotels_used),
                bundles_created=0,
                cap=cap,
                cap_applied=False,
                potential_cross_product=len(flights_used) * len(hotels_used),
            )
            ctx.scratch["bundle_compose_stats"] = stats
            ctx.events.append(make_event("bundle.compose.stats", **stats))
            ctx.events.append(make_event(EVENT_TOOL_RESULT, tool="bundle.compose", count=0))
            return

        # -----------------------
        # Cross-product with deterministic cap
        # Stable order: flight-major then hotel (both sorted deterministically above)
        # -----------------------
        bundles: list[BundleResult] = []
        cap_applied = False

        # Compute potential upfront for stats
        potential = len(flights_used) * len(hotels_used)

        # Compose
        for f_idx, f in enumerate(flights_used):
            for h_idx, h in enumerate(hotels_used):
                if len(bundles) >= cap:
                    cap_applied = True
                    break

                total = _sum_money(f.price, h.total_price, ctx.intent.currency)

                # Generate deterministic bundle identifier for tracking and deduplication.
                # This identifier is stored in the raw data payload and does not affect the BundleResult schema.
                bundle_key = f"{_flight_key(f)}::{_hotel_key(h)}"

                bundles.append(
                    BundleResult(
                        provider="amadeus",
                        flight=f,
                        hotel=h,
                        total_price=total,
                        raw={
                            "compose": {
                                "bundle_key": bundle_key,
                                "flight_provider": getattr(f, "provider", None),
                                "hotel_provider": getattr(h, "provider", None),
                                "flight_index": int(f_idx),
                                "hotel_index": int(h_idx),
                                "cap": int(cap),
                            }
                        },
                    )
                )

            if cap_applied:
                break

        # -----------------------
        # Stats + events
        # -----------------------
        stats = _compose_stats(
            ctx=ctx,
            flights_used=len(flights_used),
            hotels_used=len(hotels_used),
            bundles_created=len(bundles),
            cap=cap,
            cap_applied=cap_applied,
            potential_cross_product=potential,
        )

        ctx.scratch["bundle_compose_stats"] = stats
        ctx.events.append(make_event("bundle.compose.stats", **stats))
        ctx.events.append(make_event(EVENT_TOOL_RESULT, tool="bundle.compose", count=len(bundles)))

        ctx.bundles.extend(bundles)


def _compose_stats(
    *,
    ctx: SharedContext,
    flights_used: int,
    hotels_used: int,
    bundles_created: int,
    cap: int,
    cap_applied: bool,
    potential_cross_product: int,
) -> dict:
    return {
        "flights_total": len(ctx.flights),
        "hotels_total": len(ctx.hotels),
        "flights_used": int(flights_used),
        "hotels_used": int(hotels_used),
        "bundles_created": int(bundles_created),
        "cap": int(cap),
        "cap_applied": bool(cap_applied),
        "potential_cross_product": int(potential_cross_product),
    }


def _stable_sort_flights(flights: list[FlightResult]) -> list[FlightResult]:
    # Sort by: total price (asc), stops (asc), duration (asc best-effort), then stable key (asc)
    def k(f: FlightResult):
        price = getattr(f, "price", None)
        amt = float(price.amount) if price and price.amount is not None else 10**18
        stops = getattr(f, "stops", None)
        stops_v = int(stops) if stops is not None else 10**9
        dur_m = _duration_to_minutes(getattr(f, "duration", None))
        dur_v = float(dur_m) if dur_m is not None else 10**18
        return (amt, stops_v, dur_v, _flight_key(f))

    return sorted(flights, key=k)


def _stable_sort_hotels(hotels: list[HotelResult]) -> list[HotelResult]:
    # Sort by: total price (asc), nightly price (asc), name (asc), then stable key (asc)
    def k(h: HotelResult):
        total = getattr(h, "total_price", None)
        total_amt = float(total.amount) if total and total.amount is not None else 10**18
        nightly = getattr(h, "nightly_price", None)
        nightly_amt = float(nightly.amount) if nightly and nightly.amount is not None else 10**18
        name = (getattr(h, "hotel_name", None) or "").strip().lower()
        return (total_amt, nightly_amt, name, _hotel_key(h))

    return sorted(hotels, key=k)


def _flight_key(f: FlightResult) -> str:
    """
    Generate a stable flight identifier for bundle key generation.
    This creates consistent identifiers across multiple attempts and runs
    without using random IDs, ensuring reproducible bundle keys.
    """
    carrier = getattr(f, "carrier", None) or ""
    num = getattr(f, "flight_number", None) or ""
    dep = getattr(f, "depart_time", None) or ""
    arr = getattr(f, "arrive_time", None) or ""
    o = getattr(f, "origin", None) or ""
    d = getattr(f, "destination", None) or ""
    return f"{o}-{d}:{carrier}{num}:{dep}->{arr}"


def _hotel_key(h: HotelResult) -> str:
    name = (getattr(h, "hotel_name", None) or "").strip().lower()
    city = (getattr(h, "city", None) or "").strip().lower()
    area = (getattr(h, "area", None) or "").strip().lower()
    return f"{city}:{area}:{name}"


def _sum_money(a: Money | None, b: Money | None, fallback_currency: str) -> Money | None:
    if a is None and b is None:
        return None

    cur = fallback_currency
    amount = 0.0

    if a is not None:
        cur = a.currency or cur
        amount += float(a.amount)

    if b is not None:
        cur = b.currency or cur
        amount += float(b.amount)

    return Money(amount=amount, currency=cur)


def _duration_to_minutes(s: str | None) -> float | None:
    if not s:
        return None

    ss = str(s).strip().upper()

    # ISO8601 PT#H#M
    if ss.startswith("PT"):
        h = 0
        m = 0
        try:
            t = ss[2:]
            if "H" in t:
                part, t = t.split("H", 1)
                h = int(part)
            if "M" in t:
                part = t.split("M", 1)[0]
                m = int(part) if part else 0
            return float(h * 60 + m)
        except Exception:
            return None

    # 5H30M style
    try:
        tmp = ss.replace(" ", "")
        h = 0
        m = 0
        if "H" in tmp:
            part, rest = tmp.split("H", 1)
            h = int(part)
            tmp = rest
        if "M" in tmp:
            part = tmp.split("M", 1)[0]
            m = int(part) if part else 0
        if h or m:
            return float(h * 60 + m)
    except Exception:
        pass

    # raw minutes
    try:
        return float(int(ss))
    except Exception:
        return None
