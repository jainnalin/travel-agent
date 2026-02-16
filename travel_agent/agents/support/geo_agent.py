# travel_agent/agents/support/geo_agent.py
from __future__ import annotations

import csv
import math
import os
from typing import Optional, Tuple, Dict

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.tools.geo.resolver import resolve_to_codes


class GeoAgent(AgentBase):
    """
    Geo intelligence agent.

    Responsibilities:
        - Resolve coordinates for airports/cities from airports.csv
        - Compute distance attributes
        - Mutate artifacts only (never score)
    """

    name = "geo_agent"

    # Class-level caches for performance
    _city_to_coords: Dict[str, Tuple[float, float]] = {}
    _airport_to_coords: Dict[str, Tuple[float, float]] = {}
    _loaded = False

    def __init__(self):
        super().__init__()
        if not GeoAgent._loaded:
            self._load_airports_csv()
            GeoAgent._loaded = True

    def handles(self):
        # Support interface: this agent doesn't "handle" steps explicitly
        return ["geo_enrich", "enrich_results"]

    def _load_airports_csv(self):
        """
        Load airports.csv and populate city -> coords and airport -> coords mappings.
        Expect CSV columns: iata, name, city, lat, lon
        """
        path = os.path.join(os.path.dirname(__file__), "../../tools/geo/airports.csv")
        path = os.path.abspath(path)

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                iata = row.get("iata", "").strip().upper()
                city = row.get("city", "").strip().upper()
                try:
                    lat = float(row.get("lat") or 0.0)
                    lon = float(row.get("lon") or 0.0)
                except ValueError:
                    continue

                if iata:
                    GeoAgent._airport_to_coords[iata] = (lat, lon)
                if city and city not in GeoAgent._city_to_coords:
                    # take first city occurrence
                    GeoAgent._city_to_coords[city] = (lat, lon)

    def run(self, ctx: SharedContext, step=None) -> None:
        intent = ctx.intent
        if not intent:
            return

        geo_cache = ctx.scratch.setdefault("geo_cache", {})

        # Resolve city coordinates
        center_lat, center_lon = geo_cache.get("center_lat"), geo_cache.get("center_lon")
        if center_lat is None and getattr(intent, "city", None):
            city_name = intent.city.upper()
            coords = GeoAgent._city_to_coords.get(city_name)
            if coords:
                center_lat, center_lon = coords
                geo_cache["center_lat"], geo_cache["center_lon"] = center_lat, center_lon

        # Resolve airport coordinates
        airport_lat, airport_lon = geo_cache.get("airport_lat"), geo_cache.get("airport_lon")
        if airport_lat is None and getattr(intent, "destination", None):
            # normalize input
            primary_code, _ = resolve_to_codes(intent.destination)
            if primary_code:
                coords = GeoAgent._airport_to_coords.get(primary_code.upper())
                if coords:
                    airport_lat, airport_lon = coords
                    geo_cache["airport_lat"], geo_cache["airport_lon"] = airport_lat, airport_lon

        # Enrich hotels with distance attributes
        for hotel in getattr(ctx, "hotels", []) or []:
            lat = getattr(hotel, "latitude", None)
            lon = getattr(hotel, "longitude", None)

            if lat is None or lon is None:
                continue

            if center_lat is not None and center_lon is not None:
                hotel.distance_to_center_km = self._haversine(lat, lon, center_lat, center_lon)

            if airport_lat is not None and airport_lon is not None:
                hotel.distance_to_airport_km = self._haversine(lat, lon, airport_lat, airport_lon)

    # -------------------------------------------------

    def _haversine(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        """
        Returns distance in KM.
        """
        R = 6371.0

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c