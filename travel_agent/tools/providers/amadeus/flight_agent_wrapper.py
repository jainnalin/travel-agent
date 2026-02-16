# travel_agent/tools/providers/amadeus/flight_agent_wrapper.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from travel_agent.contracts.result import Money

AMADEUS_API_BASE = os.getenv("AMADEUS_API_BASE", "https://test.api.amadeus.com")
AMADEUS_API_KEY = os.getenv("AMADEUS_API_KEY", "")


def _build_payload(intent: Any) -> Dict[str, Any]:
    """
    Convert FlightSearch intent into Amadeus API payload
    """
    origin_destinations: List[Dict[str, Any]] = [
        {
            "id": "1",
            "originLocationCode": intent.origin,
            "destinationLocationCode": intent.destination,
            "departureDateTime": {"date": intent.depart_date},
        }
    ]
    if getattr(intent, "return_date", None):
        origin_destinations.append(
            {
                "id": "2",
                "originLocationCode": intent.destination,
                "destinationLocationCode": intent.origin,
                "departureDateTime": {"date": intent.return_date},
            }
        )

    travelers = [{"id": str(i + 1), "travelerType": "ADULT"} for i in range(getattr(intent, "adults", 1))]

    return {
        "originDestinations": origin_destinations,
        "travelers": travelers,
        "sources": ["GDS"],
    }


def _call_amadeus(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call the Amadeus flight availabilities endpoint
    """
    url = f"{AMADEUS_API_BASE}/v1/shopping/availability/flight-availabilities"
    headers = {
        "Authorization": f"Bearer {AMADEUS_API_KEY}",
        "Content-Type": "application/json",
        "X-HTTP-Method-Override": "GET",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def search_flights(intent: Any, **kwargs) -> Dict[str, Any]:
    """
    Main entrypoint for FlightSearchAgent.

    Handles:
    - Relax variants (origin_alts, destination_alts)
    - Mode switching (offers <-> availabilities)
    """
    variants: List[Dict[str, Any]] = []

    base_payload = _build_payload(intent)
    variants.append(base_payload)

    # origin/destination alternates
    origin_alts = getattr(intent, "origin_alts", []) or []
    destination_alts = getattr(intent, "destination_alts", []) or []

    if origin_alts:
        alt_payload = _build_payload(intent)
        alt_payload["originDestinations"][0]["originLocationCode"] = origin_alts[0]
        variants.append(alt_payload)

    if destination_alts:
        alt_payload = _build_payload(intent)
        alt_payload["originDestinations"][0]["destinationLocationCode"] = destination_alts[0]
        variants.append(alt_payload)

    # iterate variants until results found
    last_result: Optional[Dict[str, Any]] = None
    for variant_payload in variants:
        try:
            result = _call_amadeus(variant_payload)
            if isinstance(result, dict) and result.get("data"):
                return result
            last_result = result
        except requests.HTTPError as e:
            # fatal if 400-level bad request
            if 400 <= e.response.status_code < 500:
                raise ValueError(f"Bad flight input: {e}")
            last_result = {"error": str(e)}

    # fallback: return last attempted result
    return last_result or {}