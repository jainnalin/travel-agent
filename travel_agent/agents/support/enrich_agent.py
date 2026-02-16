# travel_agent/agents/support/enrich_agent.py

from __future__ import annotations

from typing import List

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.result import FlightResult, HotelResult, BundleResult
from travel_agent.tools.ranking import flight as flight_ranking
from travel_agent.tools.ranking import hotel as hotel_ranking
from travel_agent.tools.ranking import bundle as bundle_ranking
from travel_agent.tools.ranking import scoring

DEFAULT_ENRICH_TOP_N = 5


class EnrichAgent(AgentBase):
    """
    Post-processing intelligence agent.

    Responsibilities:
        - Re-score top-N results using contextual signals
        - Apply trip-type boosts (family/business/luxury/cheap)
        - Keep enrichment bounded (no unbounded provider calls)
    """

    name = "enrich_agent"

    def handles(self):
        # Support interface: this agent doesn't "handle" steps explicitly
        return []

    def run(self, ctx: SharedContext, step=None) -> None:
        intent = ctx.intent
        constraints = getattr(intent, "constraints", None)

        top_n = getattr(constraints, "enrich_top_n", None) or DEFAULT_ENRICH_TOP_N

        if getattr(ctx, "flights", None):
            ctx.flights = self._enrich_flights(ctx.flights, top_n, intent)

        if getattr(ctx, "hotels", None):
            ctx.hotels = self._enrich_hotels(ctx.hotels, top_n, intent)

        if getattr(ctx, "bundles", None):
            ctx.bundles = self._enrich_bundles(ctx.bundles, top_n, intent)

    # ---------------------------------------------------------------------
    # Flight enrichment
    # ---------------------------------------------------------------------

    def _enrich_flights(
        self,
        flights: List[FlightResult],
        top_n: int,
        intent,
    ) -> List[FlightResult]:
        top = flights[:top_n]
        rest = flights[top_n:]

        enriched = []
        for f in top:
            base_score = flight_ranking.score(f)
            contextual_score = self._apply_flight_context_boost(base_score, f, intent)
            f.score = scoring.normalize(contextual_score)
            enriched.append(f)

        enriched.sort(key=lambda x: x.score, reverse=True)
        return enriched + rest

    def _apply_flight_context_boost(self, score, flight, intent):
        if not intent:
            return score

        trip_type = getattr(intent, "trip_type", None)

        if trip_type == "family":
            if flight.stops == 0:
                score *= 1.10
        elif trip_type == "business":
            if getattr(flight, "duration_minutes", 0) < 240:
                score *= 1.08
        elif trip_type == "cheap":
            score *= 1.15 if getattr(flight, "price_rank", 1) == 1 else 1.0

        return score

    # ---------------------------------------------------------------------
    # Hotel enrichment
    # ---------------------------------------------------------------------

    def _enrich_hotels(
        self,
        hotels: List[HotelResult],
        top_n: int,
        intent,
    ) -> List[HotelResult]:
        top = hotels[:top_n]
        rest = hotels[top_n:]

        enriched = []
        for h in top:
            base_score = hotel_ranking.score(h)
            contextual_score = self._apply_hotel_context_boost(base_score, h, intent)
            h.score = scoring.normalize(contextual_score)
            enriched.append(h)

        enriched.sort(key=lambda x: x.score, reverse=True)
        return enriched + rest

    def _apply_hotel_context_boost(self, score, hotel, intent):
        if not intent:
            return score

        trip_type = getattr(intent, "trip_type", None)

        if trip_type == "family":
            if getattr(hotel, "rating", 0) >= 4:
                score *= 1.12
            if getattr(hotel, "free_cancellation", False):
                score *= 1.05
        elif trip_type == "business":
            if getattr(hotel, "board_type", None) == "BREAKFAST":
                score *= 1.07
            if getattr(hotel, "distance_to_center_km", None) is not None and hotel.distance_to_center_km < 3:
                score *= 1.08
        elif trip_type == "luxury":
            if getattr(hotel, "rating", 0) >= 4.5:
                score *= 1.15

        return score

    # ---------------------------------------------------------------------
    # Bundle enrichment
    # ---------------------------------------------------------------------

    def _enrich_bundles(
        self,
        bundles: List[BundleResult],
        top_n: int,
        intent,
    ) -> List[BundleResult]:
        top = bundles[:top_n]
        rest = bundles[top_n:]

        enriched = []
        for b in top:
            base_score = bundle_ranking.score(b)
            contextual_score = self._apply_bundle_context_boost(base_score, b, intent)
            b.score = scoring.normalize(contextual_score)
            enriched.append(b)

        enriched.sort(key=lambda x: x.score, reverse=True)
        return enriched + rest

    def _apply_bundle_context_boost(self, score, bundle, intent):
        if not intent:
            return score

        if getattr(intent, "trip_type", None) == "family":
            if getattr(bundle.flight, "stops", 1) == 0:
                score *= 1.08

        return score