#travel-agent/travel_agent/tools/ranking/scoring.py
from __future__ import annotations

from typing import Optional

from travel_agent.contracts.result import BundleResult, FlightResult, HotelResult, Money


def money_amount(m: Money | None) -> Optional[float]:
    try:
        if m is None:
            return None
        return float(m.amount)
    except Exception:
        return None


def clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def flight_quality_score(f: FlightResult | None) -> float:
    """
    Deterministic quality proxy for flights:
    - Nonstop bonus
    - Fewer stops better
    """
    if not f:
        return 0.0
    score = 0.0
    stops = f.stops
    if stops == 0:
        score += 10.0
    elif stops is not None:
        score += max(0.0, 8.0 - float(stops) * 3.0)
    else:
        score += 1.0
    return score


def _hotel_rating_from_raw(h: HotelResult | None) -> float | None:
    if not h:
        return None
    raw = getattr(h, "raw", None)
    if not isinstance(raw, dict):
        return None
    v = raw.get("rating")
    try:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            v = float(s)
        if isinstance(v, (int, float)):
            fv = float(v)
            # normalize 0-10 to 0-5 if needed
            if fv > 5.0 and fv <= 10.0:
                fv = fv / 2.0
            if 0.0 <= fv <= 5.0:
                return fv
    except Exception:
        return None
    return None


def hotel_quality_score(h: HotelResult | None) -> float:
    """
    Deterministic quality proxy for hotels:
    - Prefer stars when present
    - Prefer rating when present
    - Small bonus if refundable
    """
    if not h:
        return 0.0
    score = 0.0
    if h.stars is not None:
        score += float(h.stars) * 10.0
    rating = _hotel_rating_from_raw(h)
    if rating is not None:
        score += float(rating) * 8.0
    if h.refundable is True:
        score += 2.0
    return score


def bundle_cheapest_score(b: BundleResult) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    amt = money_amount(b.total_price)
    if amt is not None:
        score += max(0.0, 20000.0 - amt)
        reasons.append("cheap-total")
    else:
        reasons.append("missing-total-price")

    fqs = flight_quality_score(b.flight)
    if fqs > 0:
        score += fqs * 50.0
        if b.flight and b.flight.stops == 0:
            reasons.append("nonstop-bonus")
        else:
            reasons.append("fewer-stops")

    return score, reasons


def bundle_highest_rated_score(b: BundleResult) -> tuple[float, list[str]]:
    """
    Higher is better.
    Primary: hotel quality proxy (stars/rating/refundable).
    Secondary: flight quality proxy.
    Tertiary: price tiebreak.
    """
    reasons: list[str] = []
    score = 0.0

    hqs = hotel_quality_score(b.hotel)
    if hqs > 0:
        score += hqs * 100.0
        reasons.append("hotel-quality")
    else:
        reasons.append("missing-hotel-quality")

    fqs = flight_quality_score(b.flight)
    if fqs > 0:
        score += fqs * 30.0
        reasons.append("flight-quality")

    amt = money_amount(b.total_price)
    if amt is not None:
        score += max(0.0, 5000.0 - amt) * 0.2
        reasons.append("price-tiebreak")
    else:
        reasons.append("missing-total-price")

    return score, reasons

def normalize(score, min_val=0, max_val=1):
    # simple linear normalization: clamp to [0,1]
    if score is None:
        return 0.5
    if score < 0:
        score = 0
    return min(max(score / 1000, min_val), max_val)
