# app/providers/amadeus/flight_availabilities.py

from __future__ import annotations

from typing import Any, Dict, List

from legacy.app.contracts.flight import FlightSearchRequest
from legacy.app.providers.amadeus.client import AmadeusClient


class AmadeusFlightAvailabilitiesAdapter:
    """
    Calls Amadeus Flight Availabilities.

    Endpoint:
      POST /v1/shopping/availability/flight-availabilities

    Note:
      Some Amadeus docs/examples mention using header:
        X-HTTP-Method-Override: GET
      This adapter sets that header by default.
    """

    def __init__(self, client: AmadeusClient):
        self.client = client

    def search(self, req: FlightSearchRequest) -> Dict[str, Any]:
        payload = self._build_payload(req)

        # Required header per Amadeus flights guide/examples
        headers = {"X-HTTP-Method-Override": "GET"}

        return self.client.post(
            "/v1/shopping/availability/flight-availabilities",
            json_body=payload,
            headers=headers,
        )

    @staticmethod
    def _build_payload(req: FlightSearchRequest) -> Dict[str, Any]:
        origin_destinations: List[Dict[str, Any]] = [
            {
                "id": "1",
                "originLocationCode": req.origin,
                "destinationLocationCode": req.destination,
                "departureDateTime": {"date": req.depart_date},
            }
        ]

        # For round-trip we add a second originDestination if return_date is provided.
        if req.return_date:
            origin_destinations.append(
                {
                    "id": "2",
                    "originLocationCode": req.destination,
                    "destinationLocationCode": req.origin,
                    "departureDateTime": {"date": req.return_date},
                }
            )

        travelers = [{"id": str(i + 1), "travelerType": "ADULT"} for i in range(req.adults)]

        return {
            "originDestinations": origin_destinations,
            "travelers": travelers,
            "sources": ["GDS"],
        }
