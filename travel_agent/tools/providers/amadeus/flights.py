#travel-agent/travel_agent/tools/providers/amadeus/flights.py
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Dict, List, Optional

from travel_agent.tools.providers.amadeus.client import make_client

PROVIDER = "amadeus"

_IATA_RE = re.compile(r"^[A-Z]{3}$")


def _validate_iata(label: str, code: str) -> str:
    c = (code or "").strip().upper()
    if not _IATA_RE.match(c):
        raise ValueError(f"{label} must be a 3-letter IATA code (e.g., JFK, ORD). Got: {code!r}")
    return c


def _validate_dates(depart_date: str, return_date: Optional[str]) -> None:
    try:
        depart_dt = date.fromisoformat(depart_date)
    except Exception:
        raise ValueError(f"invalid depart_date (expected YYYY-MM-DD): {depart_date!r}")

    if return_date:
        try:
            return_dt = date.fromisoformat(return_date)
        except Exception:
            raise ValueError(f"invalid return_date (expected YYYY-MM-DD): {return_date!r}")

        if return_dt < depart_dt:
            raise ValueError(f"return_date {return_date} is before depart_date {depart_date}")


def search_flights(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
    currency: Optional[str] = None,
    cabin: Optional[str] = None,
    nonstop_only: Optional[bool] = None,
    max_stops: Optional[int] = None,
    mode: str = "offers",  # "offers" | "availabilities"
    max_offers: int = 50,
    relax: bool = False,
) -> Dict[str, Any]:
    """
    Returns raw provider payload (dict) so normalize/rank layers can evolve.

    IMPORTANT: returns dict (raw) not list.
    """

    o = _validate_iata("origin", origin)
    d = _validate_iata("destination", destination)

    if not depart_date:
        raise ValueError("search_flights() requires depart_date")
    if int(adults) <= 0:
        raise ValueError("search_flights() requires adults >= 1")

    _validate_dates(depart_date, return_date)

    # Relax strategy: loosen constraints on retry
    eff_nonstop = nonstop_only
    eff_max_stops = max_stops
    eff_cabin = cabin

    if relax:
        # If user demanded nonstop, relax can drop it to broaden results
        if eff_nonstop is True:
            eff_nonstop = None
        # Optional: cabin relax if needed
        # eff_cabin = None

    client = make_client()

    if mode == "availabilities":
        payload = _build_availabilities_post_payload(
            origin=o,
            destination=d,
            depart_date=depart_date,
            return_date=return_date,
            adults=int(adults),
        )
        return _post_with_141_retry(
            client=client,
            path="/v1/shopping/availability/flight-availabilities",
            payload=payload,
        )

    # default mode: offers
    payload = _build_offers_post_payload(
        origin=o,
        destination=d,
        depart_date=depart_date,
        return_date=return_date,
        adults=int(adults),
        currency=currency,
        cabin=eff_cabin,
        nonstop_only=eff_nonstop,
        max_stops=eff_max_stops,
        max_offers=int(max_offers),
    )

    # 1) Try POST with transient retry
    try:
        return _post_with_141_retry(
            client=client,
            path="/v2/shopping/flight-offers",
            payload=payload,
        )
    except Exception as last_err:
        # 2) Fallback GET if POST failed
        params = _build_offers_get_params(
            origin=o,
            destination=d,
            depart_date=depart_date,
            return_date=return_date,
            adults=int(adults),
            currency=currency,
            cabin=eff_cabin,
            nonstop_only=eff_nonstop,
            max_offers=int(max_offers),
        )
        try:
            return client.get("/v2/shopping/flight-offers", params=params)
        except Exception:
            raise last_err


# -------------------------
# Payload builders (400-safe)
# -------------------------

def _build_offers_post_payload(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: Optional[str],
    adults: int,
    currency: Optional[str],
    cabin: Optional[str],
    nonstop_only: Optional[bool],
    max_stops: Optional[int],
    max_offers: int,
) -> Dict[str, Any]:
    travelers = [{"id": str(i + 1), "travelerType": "ADULT"} for i in range(adults)]

    origin_destinations: List[Dict[str, Any]] = [
        {
            "id": "1",
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDateTimeRange": {"date": depart_date},
        }
    ]
    if return_date:
        origin_destinations.append(
            {
                "id": "2",
                "originLocationCode": destination,
                "destinationLocationCode": origin,
                "departureDateTimeRange": {"date": return_date},
            }
        )

    search_criteria: Dict[str, Any] = {
        "maxFlightOffers": int(max_offers),
    }

    flight_filters: Dict[str, Any] = {}

    # connection restriction
    if nonstop_only is True:
        flight_filters["connectionRestriction"] = {"maxNumberOfConnections": 0}
    elif max_stops is not None:
        flight_filters["connectionRestriction"] = {"maxNumberOfConnections": int(max_stops)}

    # cabin restriction only if set
    if cabin:
        cabin_restriction = {
            "cabin": cabin,
            "coverage": "MOST_SEGMENTS",
            "originDestinationIds": [od["id"] for od in origin_destinations],
        }
        flight_filters["cabinRestrictions"] = [cabin_restriction]

    if flight_filters:
        search_criteria["flightFilters"] = flight_filters

    payload: Dict[str, Any] = {
        "originDestinations": origin_destinations,
        "travelers": travelers,
        "sources": ["GDS"],
        "searchCriteria": search_criteria,
    }

    if currency:
        payload["currencyCode"] = currency

    return payload


def _build_offers_get_params(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: Optional[str],
    adults: int,
    currency: Optional[str],
    cabin: Optional[str],
    nonstop_only: Optional[bool],
    max_offers: int,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": depart_date,
        "adults": int(adults),
        "max": int(max_offers),
    }
    if return_date:
        params["returnDate"] = return_date

    if nonstop_only is True:
        params["nonStop"] = "true"

    if currency:
        params["currencyCode"] = currency

    if cabin:
        params["travelClass"] = cabin

    return params


def _build_availabilities_post_payload(
    *,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: Optional[str],
    adults: int,
) -> Dict[str, Any]:
    travelers = [{"id": str(i + 1), "travelerType": "ADULT"} for i in range(adults)]

    origin_destinations: List[Dict[str, Any]] = [
        {
            "id": "1",
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDateTime": {"date": depart_date},
        }
    ]
    if return_date:
        origin_destinations.append(
            {
                "id": "2",
                "originLocationCode": destination,
                "destinationLocationCode": origin,
                "departureDateTime": {"date": return_date},
            }
        )

    return {
        "originDestinations": origin_destinations,
        "travelers": travelers,
        "sources": ["GDS"],
    }


# -------------------------
# Retry helpers
# -------------------------

def _post_with_141_retry(client, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            return client.post(path, json=payload)
        except Exception as e:
            last_err = e
            time.sleep(0.6 * attempt)
            continue
    if last_err:
        raise last_err
    raise RuntimeError("Amadeus POST failed unexpectedly")


def search(**kwargs) -> Dict[str, Any]:
    return search_flights(**kwargs)
