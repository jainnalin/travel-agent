# travel_agent/contracts/intent.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Literal

Domain = Literal["hotel_only", "flight_only", "bundle"]
Objective = Literal["cheapest", "highest_rated"]


@dataclass
class PassengerInfo:
    adults: int = 1
    children: int = 0
    infants: int = 0


@dataclass
class Constraints:
    # Flights
    nonstop: Optional[bool] = None
    max_stops: Optional[int] = None
    budget_usd: Optional[float] = None
    cabin: Optional[str] = None

    # Hotels
    hotel_stars_min: Optional[int] = None
    hotel_area: Optional[str] = None

    notes: List[str] = field(default_factory=list)


@dataclass
class UserIntent:
    """
    Canonical output of NLP parsing + normalization.

    Notes:
    - For "bundle": expect both flight dates (depart/return) and hotel dates (check_in/check_out).
    - origin/destination should be airport codes where possible (DAL/DFW, MCO/SFB).
    - city is primarily for hotel_only and can be set for bundle as the destination city (e.g., Orlando).
    """

    domain: Domain  # "hotel_only" | "flight_only" | "bundle"

    # Flight routing (prefer airport codes)
    origin: Optional[str] = None
    destination: Optional[str] = None
    origin_alts: List[str] = field(default_factory=list)
    destination_alts: List[str] = field(default_factory=list)

    # Hotel city (used for hotel_only; may be set for bundle as destination city)
    city: Optional[str] = None

    # Trip dates
    depart_date: Optional[str] = None  # YYYY-MM-DD
    return_date: Optional[str] = None  # YYYY-MM-DD
    check_in: Optional[str] = None     # YYYY-MM-DD
    check_out: Optional[str] = None    # YYYY-MM-DD

    # User expressed duration signal (useful for deterministic inference/replay)
    trip_days: Optional[int] = None  # e.g., "for 3 days" => 3

    # Bundle ranking objective (only meaningful for domain="bundle")
    objective: Optional[Objective] = None  # "cheapest" | "highest_rated"

    passengers: PassengerInfo = field(default_factory=PassengerInfo)
    currency: str = "USD"
    constraints: Constraints = field(default_factory=Constraints)

    raw_text: str = ""
    confidence: float = 1.0

    # storage for parser artifacts
    meta: Dict[str, Any] = field(default_factory=dict)

    def normalized_objective(self) -> Objective:
        """
        Default objective if not provided.
        For bundle, default to cheapest.
        """
        if self.objective in ("cheapest", "highest_rated"):
            return self.objective
        return "cheapest"

    def add_origin_alt(self, code: str) -> None:
        if code and code != self.origin and code not in self.origin_alts:
            self.origin_alts.append(code)

    def add_destination_alt(self, code: str) -> None:
        if code and code != self.destination and code not in self.destination_alts:
            self.destination_alts.append(code)
