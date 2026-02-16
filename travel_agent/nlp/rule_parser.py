# travel_agent/nlp/rule_parser.py
from __future__ import annotations

import os
import csv
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from datetime import date as dt_date
from typing import Optional, Tuple

from travel_agent.contracts.intent import Domain, UserIntent, Constraints, PassengerInfo
from travel_agent.contracts.messages import Message

# -------------------------
# Month mapping
# -------------------------
_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# -------------------------
# Airport type priority for sorting
# -------------------------
_AIRPORT_PRIORITY = {
    'large_airport': 1,
    'medium_airport': 2, 
    'small_airport': 3,
    'heliport': 4,
    'seaplane_base': 5,
    'closed': 6
}

# -------------------------
# Dynamic airport loader with prioritization
# -------------------------
_CITY_TO_AIRPORTS: dict[str, tuple[str, ...]] = {}
# Use geo airports.csv; it has "municipality" (city) and "iata_code"
_nlp_dir = os.path.dirname(__file__)
_airports_file = os.path.join(_nlp_dir, "airports.csv")
if not os.path.exists(_airports_file):
    _airports_file = os.path.join(_nlp_dir, "..", "tools", "geo", "airports.csv")
if os.path.exists(_airports_file):
    # Collect all airports for each city first
    city_airports: dict[str, list[dict]] = {}
    
    with open(_airports_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # geo file uses "municipality", not "city"
            city = (row.get("municipality") or row.get("city") or "").strip().lower()
            code = (row.get("iata_code") or "").strip().upper()
            airport_type = row.get("type", "").strip()
            scheduled_service = row.get("scheduled_service", "").strip().lower() == "yes"
            
            if city and code and len(code) == 3:  # IATA codes are 3 chars
                if city not in city_airports:
                    city_airports[city] = []
                    
                city_airports[city].append({
                    'code': code,
                    'type': airport_type,
                    'scheduled_service': scheduled_service,
                    'priority': _AIRPORT_PRIORITY.get(airport_type, 999)
                })
    
    # Now build prioritized city to airports mapping
    for city, airports in city_airports.items():
        # Sort by priority (lower number = higher priority), then by scheduled service
        airports.sort(key=lambda x: (x['priority'], not x['scheduled_service']))
        
        # Create tuple with prioritized codes
        codes = tuple(a['code'] for a in airports)
        _CITY_TO_AIRPORTS[city] = codes
# -------------------------
# Parser entrypoint
# -------------------------
def parse_text_to_intent(text: str, default_year: Optional[int] = None) -> UserIntent:
    raw = text or ""
    t = raw.strip()
    tl = t.lower()
    if default_year is None:
        default_year = date.today().year

    domain = _detect_domain(tl)
    objective = _detect_objective(tl) if domain == "bundle" else None

    origin_city, dest_city = _extract_route_cities(tl)
    start_dt = _extract_start_date(tl, default_year=default_year)
    return_dt = _extract_return_date(tl, default_year=default_year)
    trip_days = _extract_trip_days(tl)

    depart_date = start_dt.isoformat() if start_dt else None
    
    # Use explicit return date if provided, otherwise calculate
    if return_dt:
        return_date = return_dt.isoformat()
    else:
        end_dt = (start_dt + timedelta(days=trip_days)) if (start_dt and trip_days) else None
        return_date = end_dt.isoformat() if end_dt else None

    # If no dates provided, default to today + trip_days
    if not depart_date and trip_days:
        import datetime
        today = datetime.date.today()
        depart_date = today.isoformat()
        end_date = (today + datetime.timedelta(days=trip_days)).isoformat()
        return_date = end_date

    check_in = depart_date
    check_out = return_date
    passengers = PassengerInfo(adults=_extract_adults(tl) or 1)
    constraints = Constraints(
        nonstop=_extract_nonstop_only(tl),
        max_stops=_extract_max_stops(tl),
        cabin=_extract_cabin(tl),
        budget_usd=_extract_budget_usd(tl),
    )

    intent = UserIntent(
        domain=domain,
        origin=None,
        destination=None,
        city=None,
        depart_date=depart_date,
        return_date=return_date,
        check_in=check_in,
        check_out=check_out,
        trip_days=trip_days,
        objective=objective,
        passengers=passengers,
        currency="USD",
        constraints=constraints,
        raw_text=raw,
        confidence=0.55,
        meta={},
    )

    # map origin city to airport codes
    if origin_city:
        codes = _CITY_TO_AIRPORTS.get(origin_city, ())
        intent.origin = codes[0] if codes else None
        for alt in codes[1:]:
            intent.add_origin_alt(alt)
        intent.meta["origin_city"] = origin_city

    # map destination city to airport codes
    if dest_city:
        codes = _CITY_TO_AIRPORTS.get(dest_city, ())
        intent.destination = codes[0] if codes else None
        for alt in codes[1:]:
            intent.add_destination_alt(alt)
        if intent.domain in ("hotel_only", "bundle"):
            intent.city = dest_city.title()
        intent.meta["destination_city"] = dest_city

    # hotel-only fallback for "in <city>"
    if intent.domain == "hotel_only" and not intent.city:
        maybe_city = _extract_in_city(tl) or _extract_single_city(tl)
        if maybe_city:
            intent.city = maybe_city.title()
            intent.meta["city"] = maybe_city

    # breadcrumbs
    intent.meta.update({
        "parser": "rule_parser",
        "objective_detected": intent.objective,
        "domain_detected": intent.domain,
        "constraints_detected": {
            "nonstop": intent.constraints.nonstop,
            "max_stops": intent.constraints.max_stops,
            "cabin": intent.constraints.cabin,
            "adults": intent.passengers.adults,
        },
    })

    return intent

# -------------------------
# Domain & objective detection
# -------------------------
def _detect_domain(tl: str) -> str:
    bundle_markers = ["travel plan", "package", "bundle", "flight and hotel", "flights and hotels"]
    flight_markers = ["flight", "flights", "airfare"]
    hotel_markers = ["hotel", "hotels", "stay", "accommodation"]

    if any(m in tl for m in bundle_markers):
        return "bundle"
    if any(m in tl for m in flight_markers) and any(m in tl for m in hotel_markers):
        return "bundle"
    if any(m in tl for m in flight_markers):
        return "flight_only"
    return "hotel_only"

def _detect_objective(tl: str) -> Optional[str]:
    if "cheapest" in tl or "lowest price" in tl or "low cost" in tl:
        return "cheapest"
    if "highest rated" in tl or "highly rated" in tl or "best rated" in tl or "top rated" in tl:
        return "highest_rated"
    if re.search(r"\bbest\b", tl) and "price" not in tl and "cheap" not in tl:
        return "highest_rated"
    return None

# -------------------------
# Trip info extraction
# -------------------------
def _extract_trip_days(tl: str) -> Optional[int]:
    # Handle "for X days", "X days", "X-day" patterns
    patterns = [
        r"\bfor\s+(\d+)\s+days?\b",
        r"\b(\d+)\s+days?\b",
        r"\b(\d+)\s*-\s*day\b",
        r"\b(\d+)\s+day\b"
    ]
    
    for pattern in patterns:
        m = re.search(pattern, tl)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                continue
    return None

def _extract_return_date(tl: str, default_year: int) -> Optional[date]:
    """Extract return date from text like 'returning July 2nd' or 'return July 2nd'."""
    def _year(v: Optional[str]) -> int:
        if not v:
            return default_year
        try:
            y = int(v.strip())
            if y < 100:
                y += 2000
            return y
        except Exception:
            return default_year

    # formats: "returning July 2nd" / "return July 2nd" / "returning July 2nd 2026"
    m = re.search(
        r"\b(?:returning|return)\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{2,4}))?\b",
        tl,
        re.IGNORECASE,
    )
    if m:
        mo_name, d_s, y_s = m.group(1), m.group(2), m.group(3)
        mo = _MONTHS.get(mo_name.lower())
        if mo:
            return date(_year(y_s), mo, int(d_s))

    # formats: "returning on 7/2" or "return on 7/2/2026"
    m = re.search(r"\b(?:returning|return)\s+(?:on\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", tl, re.IGNORECASE)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        y = _year(m.group(3))
        return date(y, mo, d)

    return None

def _extract_start_date(tl: str, default_year: int) -> Optional[date]:
    def _year(v: Optional[str]) -> int:
        if not v:
            return default_year
        try:
            y = int(v.strip())
            if y < 100:
                y += 2000
            return y
        except Exception:
            return default_year

    # formats: YYYY-MM-DD (with "starting" or "on")
    m = re.search(r"\b(?:on|starting)\s+(\d{4})-(\d{2})-(\d{2})\b", tl)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", tl)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # formats: MM/DD or MM/DD/YYYY
    m = re.search(r"\b(?:on|starting)\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", tl)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        y = _year(m.group(3))
        return date(y, mo, d)

    # formats: "on July 1st" / "on May 15" / "on May 15th 2026" / "starting July 1st"
    m = re.search(
        r"\b(?:on|starting)\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{2,4}))?\b",
        tl,
    )
    if m:
        mo_name, d_s, y_s = m.group(1), m.group(2), m.group(3)
        mo = _MONTHS.get(mo_name.lower())
        if mo:
            return date(_year(y_s), mo, int(d_s))

    # formats: "July 1st" / "May 15th" / "May 15 2026" (month name + day anywhere)
    m = re.search(
        r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{2,4}))?\b",
        tl,
    )
    if m:
        mo_name, d_s, y_s = m.group(1), m.group(2), m.group(3)
        mo = _MONTHS.get(mo_name.lower())
        if mo:
            return date(_year(y_s), mo, int(d_s))

    return None

def _extract_route_cities(tl: str) -> Tuple[Optional[str], Optional[str]]:
    m = re.search(r"\bfrom\s+([a-z ]+?)\s+to\s+([a-z ]+?)(?:\s+(?:on|for|in|at|departing|leaving)\b|$)", tl)
    if not m:
        return None, None
    origin_text, dest_text = m.group(1).strip().lower(), m.group(2).strip().lower()
    
    # Find cities - prefer exact matches, then longest substring matches
    def find_best_city(text: str) -> Optional[str]:
        # First try exact match
        if text in _CITY_TO_AIRPORTS:
            return text
        
        # Then find the longest matching city name
        matches = [(c, len(c)) for c in _CITY_TO_AIRPORTS if c in text]
        if matches:
            # Return the city with the longest name (prefer more specific matches)
            return max(matches, key=lambda x: x[1])[0]
        return None
    
    origin_city = find_best_city(origin_text)
    dest_city = find_best_city(dest_text)
    return origin_city, dest_city

def _extract_in_city(tl: str) -> Optional[str]:
    """Try to detect 'in <city>' pattern, fallback to substring match in known cities."""
    tl = tl.lower()
    m = re.search(r"\bin\s+([a-z ]+?)(?:\b|[.,;])", tl)
    candidate = m.group(1).strip() if m else ""
    
    # First try exact match
    if candidate in _CITY_TO_AIRPORTS:
        return candidate
    
    # Then find longest matching city name
    matches = [(c, len(c)) for c in _CITY_TO_AIRPORTS.keys() if c in candidate]
    if matches:
        # Return the city with longest name (prefer more specific matches)
        return max(matches, key=lambda x: x[1])[0]
    
    return None

def _extract_single_city(tl: str) -> Optional[str]:
    """Fallback: scan text for any known city name substring."""
    tl = tl.lower()
    
    # Find all matching cities with their lengths
    matches = [(c, len(c)) for c in _CITY_TO_AIRPORTS.keys() if c in tl]
    if matches:
        # Return the city with longest name (prefer more specific matches)
        return max(matches, key=lambda x: x[1])[0]
    
    return None

# -------------------------
# Constraints extraction
# -------------------------
def _extract_nonstop_only(tl: str) -> Optional[bool]:
    if re.search(r"\bnon[-\s]?stop\b", tl) or "direct flight" in tl or "direct flights" in tl:
        return True
    return None

def _extract_cabin(tl: str) -> Optional[str]:
    if re.search(r"\bfirst\s+class\b|\bfirst-class\b", tl):
        return "FIRST"
    if re.search(r"\bbusiness\s+class\b|\bbusiness-class\b|\bbusiness\b", tl):
        return "BUSINESS"
    if re.search(r"\bpremium\s+economy\b|\bpremium-economy\b", tl):
        return "PREMIUM_ECONOMY"
    if re.search(r"\beconomy\b|\bcoach\b", tl):
        return "ECONOMY"
    return None

def _extract_max_stops(tl: str) -> Optional[int]:
    m = re.search(r"\b(up\s+to|max(?:imum)?)\s+(\d+)\s+stops?\b", tl) or re.search(r"\b(\d+)\s+stops?\b", tl)
    if m:
        try:
            return int(m.group(2)) if len(m.groups()) > 1 else int(m.group(1))
        except Exception:
            return None
    return None

def _extract_budget_usd(tl: str) -> Optional[float]:
    """Extract budget constraint like 'under $200', 'budget $500', etc."""
    # Handle various budget patterns
    patterns = [
        r"\bunder\s+\$(\d+(?:\.\d+)?)\b",
        r"\bunder\s+(\d+(?:\.\d+)?)\s+usd?\b",
        r"\bbudget\s+\$(\d+(?:\.\d+)?)\b",
        r"\bbudget\s+(\d+(?:\.\d+)?)\s+usd?\b",
        r"\blessthan\s+\$(\d+(?:\.\d+)?)\b",
        r"\bmax\s+\$(\d+(?:\.\d+)?)\b",
        r"\bmax\s+(\d+(?:\.\d+)?)\s+usd?\b"
    ]
    
    for pattern in patterns:
        m = re.search(pattern, tl, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                continue
    return None

def _extract_adults(tl: str) -> Optional[int]:
    m = re.search(r"\bfor\s+(\d+)\s+adults?\b", tl) or re.search(r"\b(\d+)\s+adults?\b", tl)
    if not m:
        return None
    try:
        v = int(next(g for g in m.groups() if g))
        return v if v > 0 else None
    except Exception:
        return None