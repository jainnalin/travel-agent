#travel-agent/travel_agent/nlp/normalize/bundle.py
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Optional, Tuple

from travel_agent.contracts.intent import Objective, UserIntent

# Minimal deterministic mappings for V1.
# (Keep this tiny + explicit for Phase 5 determinism.)
_CITY_TO_AIRPORTS: dict[str, tuple[str, str]] = {
    "dallas": ("DAL", "DFW"),
    "orlando": ("MCO", "SFB"),
}


def normalize_bundle_intent(intent: UserIntent, *, default_year: Optional[int] = None) -> UserIntent:
    """
    Deterministic bundle normalization.

    Responsibilities (V1):
      - Ensure objective is set (default "cheapest")
      - Ensure dates are internally consistent:
          depart/return and check_in/check_out align for bundle
          if trip_days + start date exist, infer missing end
      - Ensure origin/destination are airport codes where possible
        and populate origin_alts/destination_alts for known cities
      - Preserve user-provided fields (do not overwrite explicit airport codes)

    This function is SAFE to call on any domain; it only mutates when domain=="bundle".
    """
    if intent.domain != "bundle":
        return intent

    if default_year is None:
        default_year = date.today().year

    meta = dict(intent.meta or {})
    meta.setdefault("normalize", {})
    nm = meta["normalize"]
    if not isinstance(nm, dict):
        nm = {}
        meta["normalize"] = nm

    # -------------------------
    # Objective (default)
    # -------------------------
    obj: Objective = intent.normalized_objective()
    nm["objective"] = obj

    # -------------------------
    # Dates: align flight + hotel windows
    # -------------------------
    depart = _parse_iso_date(intent.depart_date)
    ret = _parse_iso_date(intent.return_date)
    ci = _parse_iso_date(intent.check_in)
    co = _parse_iso_date(intent.check_out)

    trip_days = intent.trip_days if isinstance(intent.trip_days, int) and intent.trip_days > 0 else None

    # Prefer depart_date as the anchor if present, else check_in.
    start = depart or ci
    end = ret or co

    # If we have start + trip_days but missing end, infer end = start + trip_days.
    inferred_end = None
    if start and (end is None) and trip_days:
        inferred_end = start + timedelta(days=int(trip_days))
        end = inferred_end
        nm["inferred_end_from_trip_days"] = True
    else:
        nm["inferred_end_from_trip_days"] = False

    # If we have both start and end, materialize all four date fields consistently.
    if start and end:
        # Ensure end > start (basic sanity). If not, leave as-is but breadcrumb.
        if end <= start:
            nm["date_warning"] = "end_date_not_after_start"
        depart_s = start.isoformat()
        end_s = end.isoformat()
        intent = replace(
            intent,
            depart_date=depart_s,
            return_date=end_s,
            check_in=depart_s,
            check_out=end_s,
        )
        nm["dates_aligned"] = True
    else:
        nm["dates_aligned"] = False

    # -------------------------
    # Origin/Destination mapping
    # -------------------------
    # If origin/destination already look like airport codes, preserve them.
    # Otherwise, if we have meta city hints OR city names, map known cities.
    origin = (intent.origin or "").strip().upper() or None
    dest = (intent.destination or "").strip().upper() or None

    # City hints (rule_parser sets origin_city/destination_city)
    origin_city = _lower_str(meta.get("origin_city"))
    dest_city = _lower_str(meta.get("destination_city"))

    # Fall back: if city field exists (destination city), use that for dest mapping
    if not dest_city and intent.city:
        dest_city = _lower_str(intent.city)

    # Map origin if not a 3-letter airport code already
    if not _looks_like_airport_code(origin) and origin_city:
        pri, alt = _CITY_TO_AIRPORTS.get(origin_city, (None, None))  # type: ignore
        if pri:
            origin = pri
            nm["origin_mapped_from_city"] = origin_city
            if alt and alt not in (intent.origin_alts or []):
                intent.add_origin_alt(alt)

    # Map destination if not a 3-letter airport code already
    if not _looks_like_airport_code(dest) and dest_city:
        pri, alt = _CITY_TO_AIRPORTS.get(dest_city, (None, None))  # type: ignore
        if pri:
            dest = pri
            nm["destination_mapped_from_city"] = dest_city
            if alt and alt not in (intent.destination_alts or []):
                intent.add_destination_alt(alt)

    # Ensure destination city exists for hotels in bundle
    city = intent.city
    if not city and dest_city:
        city = dest_city.title()
        nm["city_set_from_destination_city"] = True
    else:
        nm["city_set_from_destination_city"] = False

    # Write back normalized routing + meta
    intent = replace(
        intent,
        origin=origin,
        destination=dest,
        city=city,
        objective=obj,
        meta=meta,
    )
    return intent


# -------------------------
# Helpers
# -------------------------

def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip())
    except Exception:
        return None


def _looks_like_airport_code(s: Optional[str]) -> bool:
    if not s:
        return False
    t = str(s).strip().upper()
    return len(t) == 3 and t.isalpha()


def _lower_str(v) -> Optional[str]:
    if v is None:
        return None
    try:
        s = str(v).strip()
        return s.lower() if s else None
    except Exception:
        return None
