# travel-agent/contracts/result.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List


@dataclass
class Money:
    amount: float
    currency: str = "USD"


@dataclass
class FlightResult:
    provider: str
    carrier: Optional[str] = None
    flight_number: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    depart_time: Optional[str] = None
    arrive_time: Optional[str] = None
    duration: Optional[str] = None
    stops: Optional[int] = None
    price: Optional[Money] = None
    refundable: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HotelResult:
    provider: str
    hotel_name: str
    city: Optional[str] = None
    nightly_price: Optional[Money] = None
    total_price: Optional[Money] = None
    refundable: Optional[bool] = None
    board: Optional[str] = None
    pay_type: Optional[str] = None
    stars: Optional[int] = None
    rating: Optional[float] = None
    area: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BundleResult:
    provider: str
    flight: Optional[FlightResult] = None
    hotel: Optional[HotelResult] = None
    total_price: Optional[Money] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RankedItem:
    score: float
    item_type: str  # "flight" | "hotel" | "bundle"
    item_index: int
    reasons: List[str] = field(default_factory=list)
