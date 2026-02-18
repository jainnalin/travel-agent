# tools/geo/resolver.py
from __future__ import annotations

import csv
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Airport type priority for sorting
_AIRPORT_PRIORITY = {
    'large_airport': 1,
    'medium_airport': 2, 
    'small_airport': 3,
    'heliport': 4,
    'seaplane_base': 5,
    'closed': 6
}

_IATA_RE = re.compile(r"^[A-Z]{3}$")

_lock = threading.Lock()

# normalized input -> (primary, alternates)
_cache: Dict[str, Tuple[Optional[str], List[str]]] = {}

# City to IATA mappings loaded from airports.csv
_city_to_iata: Dict[str, Tuple[str, List[str]]] = {}
_iata_to_city: Dict[str, str] = {}
_data_loaded = False

_metrics: Dict[str, Any] = {
    "calls": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_entries": 0,
    "last_reset_ts": time.time(),
}

# ✅ NEW: metro-code expansions even when user types the 3-letter code
# Keep this small + obvious; expand as needed.
_METRO_EXPANSIONS: Dict[str, List[str]] = {
    "NYC": ["JFK", "LGA", "EWR"],
    "LON": ["LHR", "LGW", "LCY", "LTN", "STN"],
    # Add more metros if you want:
    # "TYO": ["HND", "NRT"],
    # "PAR": ["CDG", "ORY"],
}


# -------------------------
# Public: stats helpers
# -------------------------

def geo_resolver_stats() -> Dict[str, Any]:
    """Snapshot stats for debugging/observability."""
    with _lock:
        return {
            **_metrics,
            "cache_size": len(_cache),
        }


def geo_resolver_reset_stats(*, clear_cache: bool = False) -> Dict[str, Any]:
    """
    Reset counters. Optionally clear cache.
    """
    with _lock:
        _metrics["calls"] = 0
        _metrics["cache_hits"] = 0
        _metrics["cache_misses"] = 0
        _metrics["last_reset_ts"] = time.time()

        if clear_cache:
            _cache.clear()

        _metrics["cache_entries"] = len(_cache)

    return geo_resolver_stats()


# -------------------------
# Internal helpers
# -------------------------

def _norm_key(place: str) -> str:
    # normalize whitespace + uppercase
    return " ".join((place or "").strip().upper().split())


def _dedupe_alts(primary: Optional[str], alts: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []

    if primary and _IATA_RE.match(primary):
        seen.add(primary)

    for a in alts or []:
        if not a:
            continue
        a = str(a).strip().upper()
        if not _IATA_RE.match(a):
            continue
        if a in seen:
            continue
        seen.add(a)
        out.append(a)

    return out


def _load_airport_data():
    """Load airport data from CSV and build city mappings."""
    global _city_to_iata, _iata_to_city, _data_loaded
    
    if _data_loaded:
        return
        
    csv_path = os.path.join(os.path.dirname(__file__), 'airports.csv')
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Collect all airports for each city first
            city_airports: Dict[str, List[Dict]] = {}
            
            for row in reader:
                iata_code = row.get('iata_code', '').strip().upper()
                municipality = row.get('municipality', '').strip().upper()
                airport_type = row.get('type', '').strip()
                scheduled_service = row.get('scheduled_service', '').strip().lower() == 'yes'
                
                # Skip entries without IATA codes or municipalities
                if not iata_code or not municipality or len(iata_code) != 3:
                    continue
                    
                # Build IATA to city mapping
                _iata_to_city[iata_code] = municipality
                
                # Collect airport info for prioritization
                if municipality not in city_airports:
                    city_airports[municipality] = []
                    
                city_airports[municipality].append({
                    'iata': iata_code,
                    'type': airport_type,
                    'scheduled_service': scheduled_service,
                    'priority': _AIRPORT_PRIORITY.get(airport_type, 999)
                })
            
            # Now build city to IATA mappings with prioritization
            for city, airports in city_airports.items():
                # Sort by priority (lower number = higher priority), then by scheduled service
                airports.sort(key=lambda x: (x['priority'], not x['scheduled_service']))
                
                # Get primary airport (highest priority)
                if airports:
                    primary = airports[0]['iata']
                    alts = [a['iata'] for a in airports[1:]]  # Rest as alternates
                    _city_to_iata[city] = (primary, alts)
                        
        _data_loaded = True
        
    except Exception as e:
        print(f"Warning: Could not load airport data: {e}")
        _data_loaded = True  # Prevent repeated load attempts


def _resolve_non_iata_text(key: str) -> Tuple[Optional[str], List[str]]:
    """
    Resolve city names to IATA codes using loaded airport data.
    Falls back to hardcoded mappings for special cases.
    """
    # Ensure data is loaded
    _load_airport_data()
    
    # Check city mappings first
    hit = _city_to_iata.get(key)
    if hit:
        return hit[0], list(hit[1])
    
    # Hardcoded mappings for special cases and metro areas
    TINY: Dict[str, Tuple[str, List[str]]] = {
        # NYC metro - special handling
        "NEW YORK": ("NYC", ["JFK", "LGA", "EWR"]),
        "NYC": ("NYC", ["JFK", "LGA", "EWR"]),
        
        # London metro - special handling  
        "LONDON": ("LON", ["LHR", "LGW", "LCY", "LTN", "STN"]),
        "LON": ("LON", ["LHR", "LGW", "LCY", "LTN", "STN"]),
    }
    
    hit = TINY.get(key)
    if hit:
        return hit[0], list(hit[1])
    
    return None, []


# -------------------------
# Public: resolver entrypoint
# -------------------------

def resolve_to_codes(place: str) -> Tuple[Optional[str], List[str]]:
    """
    Returns (primary, alternates).
    - If already 3-letter IATA:
        - if it's a known metro code (NYC/LON...), return alts
        - otherwise passthrough with alts=[]
    - Else: consult tiny dict
    - Cached by normalized key
    """
    key = _norm_key(place)
    if not key:
        return None, []

    with _lock:
        _metrics["calls"] += 1
        cached = _cache.get(key)
        if cached is not None:
            _metrics["cache_hits"] += 1
            primary, alts = cached
            return primary, list(alts)
        _metrics["cache_misses"] += 1

    # --- compute outside lock ---
    primary: Optional[str] = None
    alts: List[str] = []

    if _IATA_RE.match(key):
        primary = key
        # ✅ NEW: expand metros even for 3-letter input
        alts = list(_METRO_EXPANSIONS.get(key, []))
    else:
        primary, alts = _resolve_non_iata_text(key)

    alts = _dedupe_alts(primary, alts)

    with _lock:
        if key not in _cache:
            _cache[key] = (primary, alts)
        _metrics["cache_entries"] = len(_cache)

    return primary, list(alts)
