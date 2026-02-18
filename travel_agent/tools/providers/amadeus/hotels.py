#travel-agent/travel_agent/tools/providers/amadeus/hotels.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import requests

from travel_agent.tools.providers.amadeus.client import AmadeusClient, make_client

PROVIDER = "amadeus"

# Hard safety limits to prevent Amadeus 400 due to huge hotelIds query strings.
MAX_HOTEL_IDS_PER_OFFERS_CALL = 20   # safe default (keeps URL small)
MAX_TOTAL_OFFERS_CALLS = 5           # avoid exploding provider calls in relax mode


def _chunks(xs: List[str], n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


@dataclass(frozen=True)
class LocationResolution:
    destination_id: str              # "CITY:DFW" or "GEO:32.77,-96.79"
    strategy: str                    # "CITY_CODE" | "AMADEUS_GEOCODE" | "NOMINATIM_GEOCODE"
    raw: Dict[str, Any]              # raw payloads used to decide


class AmadeusHotelsAdapter:
    """
    Amadeus Hotels adapter.

    Primary responsibilities:
      - Resolve "city string" -> destination_id (CITY: or GEO:)
      - List hotel IDs for destination (by-city or by-geocode)
      - Fetch hotel offers for the hotel IDs
      - (Optional) get offers-by-hotel for a single hotel id

    By design:
      - This adapter returns *raw* provider payloads for normalize/rank layers.
      - A temporary helper list_properties_normalized(...) is included to keep
        your current agent flow working while you move normalization to app/normalize/hotel.py.
    """

    def __init__(self, client: AmadeusClient):
        self.client = client

    # ------------
    # Location
    # ------------

    def search_location(self, city: str, domain: str = "US") -> LocationResolution:
        q = (city or "").strip()
        if not q:
            raise ValueError("search_location() missing city")

        dom = (domain or "US").strip().upper()

        # 1) CITY code attempt
        cities_raw = self.client.get(
            "/v1/reference-data/locations/cities",
            params={"keyword": q, "max": 5},
        )

        city_code = self._pick_city_code(cities_raw)
        if city_code:
            return LocationResolution(
                destination_id=f"CITY:{city_code}",
                strategy="CITY_CODE",
                raw={"cities": cities_raw},
            )

        # 2) Amadeus GEO attempt
        params: Dict[str, Any] = {
            "keyword": q,
            "subType": "CITY",
            "page[limit]": 5,
        }
        if dom == "US":
            params["countryCode"] = "US"

        loc_raw = self.client.get("/v1/reference-data/locations", params=params)

        geo = self._pick_geocode(loc_raw)
        if geo:
            lat, lon = geo
            return LocationResolution(
                destination_id=f"GEO:{lat},{lon}",
                strategy="AMADEUS_GEOCODE",
                raw={"cities": cities_raw, "locations": loc_raw},
            )

        # 3) Nominatim fallback (real geocoder)
        nom = self._nominatim_geocode(q=q, domain=dom)
        if nom:
            lat, lon, raw = nom
            return LocationResolution(
                destination_id=f"GEO:{lat},{lon}",
                strategy="NOMINATIM_GEOCODE",
                raw={"cities": cities_raw, "locations": loc_raw, "nominatim": raw},
            )

        raise RuntimeError(
            f"Unable to resolve location for city={city!r} "
            f"(no Amadeus cityCode or geoCode, and Nominatim failed)."
        )

    # ------------
    # Raw listing methods (Phase 2 “correct”)
    # ------------

    def list_properties_raw(
        self,
        destination_id: str,
        check_in: str,
        check_out: str,
        adults: int = 2,
        currency: Optional[str] = None,
        hotel_id_limit: int = 50,
        radius_km: int = 50,
    ) -> Dict[str, Any]:
        kind, payload = self._parse_destination(destination_id)

        if kind == "CITY":
            hotels_raw = self.client.get(
                "/v1/reference-data/locations/hotels/by-city",
                params={
                    "cityCode": payload["city_code"],
                    "radius": int(radius_km),
                    "radiusUnit": "KM",
                },
            )
        else:
            hotels_raw = self.client.get(
                "/v1/reference-data/locations/hotels/by-geocode",
                params={
                    "latitude": float(payload["lat"]),
                    "longitude": float(payload["lon"]),
                    "radius": int(radius_km),
                    "radiusUnit": "KM",
                },
            )

        hotel_ids = self._extract_hotel_ids(hotels_raw, limit=hotel_id_limit)
        if not hotel_ids:
            return {
                "provider": PROVIDER,
                "destination_id": destination_id,
                "check_in": check_in,
                "check_out": check_out,
                "adults": int(adults),
                "currency": currency,
                "hotel_ids": [],
                "hotels_raw": hotels_raw,
                "offers_raw": {"data": []},
                "warnings": [],
                "warning_count": 0,
            }

        offers_data: List[Any] = []
        warnings: List[Dict[str, Any]] = []

        batch_size = MAX_HOTEL_IDS_PER_OFFERS_CALL
        batches = list(_chunks(hotel_ids, batch_size))[:MAX_TOTAL_OFFERS_CALLS]

        for batch in batches:
            offers_params: Dict[str, Any] = {
                "hotelIds": ",".join(batch),
                "adults": int(adults),
                "checkInDate": check_in,
                "checkOutDate": check_out,
                # "currency": currency,  # keep disabled unless confirmed supported
            }

            part = self.client.get("/v3/shopping/hotel-offers", params=offers_params)

            if isinstance(part, dict):
                d = part.get("data")
                if isinstance(d, list):
                    offers_data.extend(d)

                w = part.get("warnings")
                if isinstance(w, list):
                    warnings.extend([x for x in w if isinstance(x, dict)])

        offers_raw: Dict[str, Any] = {"data": offers_data}
        if warnings:
            offers_raw["warnings"] = warnings

        return {
            "provider": PROVIDER,
            "destination_id": destination_id,
            "check_in": check_in,
            "check_out": check_out,
            "adults": int(adults),
            "currency": currency,
            "hotel_ids": hotel_ids,
            "hotels_raw": hotels_raw,
            "offers_raw": offers_raw,
            "warnings": warnings,
            "warning_count": len(warnings),
        }

    def get_hotel_summary_raw(self, hotel_id: str) -> Dict[str, Any]:
        return self.client.get(
            "/v3/shopping/hotel-offers-by-hotel",
            params={"hotelId": str(hotel_id)},
        )

    # ------------
    # Convenience Methods
    # ------------

    def list_properties_normalized(
        self,
        destination_id: str,
        check_in: str,
        check_out: str,
        adults: int = 2,
        currency: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        bundle = self.list_properties_raw(
            destination_id=destination_id,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            currency=currency,
        )
        offers_raw = bundle.get("offers_raw")
        return self._normalize_offers_compat(
            raw=offers_raw,
            check_in=check_in,
            check_out=check_out,
            requested_currency=currency,
        )

    # -------------------------
    # Helpers
    # -------------------------

    @staticmethod
    def _pick_city_code(raw: Any) -> Optional[str]:
        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, list):
            return None
        for it in data:
            if not isinstance(it, dict):
                continue
            code = it.get("iataCode")
            if isinstance(code, str) and code.strip():
                return code.strip().upper()
            addr = it.get("address")
            if isinstance(addr, dict):
                cc = addr.get("cityCode")
                if isinstance(cc, str) and cc.strip():
                    return cc.strip().upper()
        return None

    @staticmethod
    def _pick_geocode(raw: Any) -> Optional[Tuple[float, float]]:
        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, list):
            return None
        for it in data:
            if not isinstance(it, dict):
                continue
            geo = it.get("geoCode")
            if not isinstance(geo, dict):
                continue
            try:
                lat = float(geo.get("latitude"))
                lon = float(geo.get("longitude"))
                return lat, lon
            except Exception:
                continue
        return None

    @staticmethod
    def _nominatim_geocode(q: str, domain: str) -> Optional[Tuple[float, float, Dict[str, Any]]]:
        try:
            nom_q = q
            if domain == "US" and "usa" not in q.lower() and "united states" not in q.lower():
                nom_q = f"{q}, USA"

            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": nom_q, "format": "json", "limit": 1},
                headers={"User-Agent": "travel-agent/1.0 (local dev)"},
                timeout=20,
            )
            r.raise_for_status()
            arr = r.json()
            if isinstance(arr, list) and arr:
                it = arr[0]
                lat = float(it.get("lat"))
                lon = float(it.get("lon"))
                return lat, lon, it
        except Exception:
            return None
        return None

    @staticmethod
    def _parse_destination(destination_id: str) -> Tuple[str, Dict[str, Any]]:
        dest = (destination_id or "").strip()

        if dest.upper().startswith("CITY:"):
            city_code = dest.split(":", 1)[1].strip().upper()
            if not city_code:
                raise ValueError("CITY destination missing city code")
            return "CITY", {"city_code": city_code}

        if dest.upper().startswith("GEO:"):
            coords = dest.split(":", 1)[1].strip()
            if "," not in coords:
                raise ValueError("GEO destination must be GEO:<lat>,<lon>")
            lat_s, lon_s = coords.split(",", 1)
            lat = float(lat_s.strip())
            lon = float(lon_s.strip())
            return "GEO", {"lat": lat, "lon": lon}

        city_code = dest.strip().upper()
        if not city_code:
            raise ValueError("destination_id is empty")
        return "CITY", {"city_code": city_code}

    @staticmethod
    def _extract_hotel_ids(hotels_raw: Any, limit: int = 50) -> List[str]:
        hotel_ids: List[str] = []
        if isinstance(hotels_raw, dict) and isinstance(hotels_raw.get("data"), list):
            for it in hotels_raw["data"]:
                if isinstance(it, dict) and it.get("hotelId"):
                    hotel_ids.append(str(it["hotelId"]))
        return hotel_ids[:limit]

    @staticmethod
    def _to_float(v: Any) -> Optional[float]:
        try:
            return float(v)
        except Exception:
            return None

    @staticmethod
    def _nights_between(check_in: str, check_out: str) -> Optional[int]:
        try:
            ci = date.fromisoformat(check_in)
            co = date.fromisoformat(check_out)
            nights = (co - ci).days
            return nights if nights > 0 else None
        except Exception:
            return None

    # ---- Rating / stars extraction helpers (Phase 5) ----

    @staticmethod
    def _extract_rating_and_stars(hotel_obj: Dict[str, Any], item_obj: Dict[str, Any]) -> Tuple[Optional[float], Optional[int]]:
        """
        Best-effort extraction of rating/stars from various provider shapes.
        We keep this defensive because Amadeus payloads vary by endpoint.
        """
        candidates: List[Any] = []

        # Common candidates in hotel object
        for k in ("rating", "hotelRating", "overallRating", "reviewScore"):
            if k in hotel_obj:
                candidates.append(hotel_obj.get(k))

        # Sometimes nested
        if isinstance(hotel_obj.get("media"), dict):
            candidates.append(hotel_obj["media"].get("rating"))

        # Fallback: sometimes on top-level item
        for k in ("rating", "hotelRating", "overallRating", "reviewScore"):
            if k in item_obj:
                candidates.append(item_obj.get(k))

        rating: Optional[float] = None
        stars: Optional[int] = None

        # Try interpret as rating first (0-5 or 0-10)
        for v in candidates:
            try:
                if v is None:
                    continue
                if isinstance(v, str):
                    v = v.strip()
                    if not v:
                        continue
                    v = float(v)
                if isinstance(v, (int, float)):
                    fv = float(v)
                    # If looks like 0-10 scale, convert to 0-5
                    if fv > 5.0 and fv <= 10.0:
                        fv = fv / 2.0
                    if fv >= 0.0 and fv <= 5.0:
                        rating = fv
                        break
            except Exception:
                continue

        # Stars (integer 1-5) if present
        star_candidates: List[Any] = []
        for k in ("stars", "hotelStars", "starRating", "category"):
            if k in hotel_obj:
                star_candidates.append(hotel_obj.get(k))
        for k in ("stars", "hotelStars", "starRating", "category"):
            if k in item_obj:
                star_candidates.append(item_obj.get(k))

        for v in star_candidates:
            try:
                if v is None:
                    continue
                if isinstance(v, str):
                    vv = v.strip()
                    if not vv:
                        continue
                    # allow "4" or "4.0"
                    fv = float(vv)
                    iv = int(round(fv))
                elif isinstance(v, (int, float)):
                    iv = int(round(float(v)))
                else:
                    continue
                if 1 <= iv <= 5:
                    stars = iv
                    break
            except Exception:
                continue

        return rating, stars

    # ---- Compatibility normalization ----

    def _normalize_offers_compat(
        self,
        raw: Any,
        check_in: str,
        check_out: str,
        requested_currency: Optional[str],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(raw, dict):
            return out

        data = raw.get("data")
        if not isinstance(data, list):
            return out

        nights = self._nights_between(check_in, check_out) or 1

        def offer_total_and_currency(offer: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
            price = offer.get("price") if isinstance(offer.get("price"), dict) else {}
            total = self._to_float(price.get("total"))
            cur = price.get("currency")
            cur_s = cur.strip().upper() if isinstance(cur, str) and cur.strip() else None
            return total, cur_s

        def infer_refundable(offer: Dict[str, Any]) -> Optional[bool]:
            try:
                pol = offer.get("policies") if isinstance(offer.get("policies"), dict) else {}
                ref = pol.get("refundable")
                if isinstance(ref, dict):
                    cr = ref.get("cancellationRefund")
                    if isinstance(cr, str) and cr.strip():
                        v = cr.strip().upper()
                        if v == "NON_REFUNDABLE":
                            return False
                        if v.startswith("REFUNDABLE"):
                            return True

                cancels = pol.get("cancellations")
                if isinstance(cancels, list):
                    for c in cancels:
                        if not isinstance(c, dict):
                            continue
                        desc = c.get("description")
                        if isinstance(desc, dict):
                            t = desc.get("text")
                            if isinstance(t, str) and "NON-REFUNDABLE" in t.upper():
                                return False
                return None
            except Exception:
                return None

        def first_cancel_deadline(offer: Dict[str, Any]) -> Optional[str]:
            try:
                pol = offer.get("policies") if isinstance(offer.get("policies"), dict) else {}
                cancels = pol.get("cancellations")
                if isinstance(cancels, list) and cancels:
                    c0 = cancels[0] if isinstance(cancels[0], dict) else {}
                    dl = c0.get("deadline")
                    return dl if isinstance(dl, str) and dl.strip() else None
                return None
            except Exception:
                return None

        for item in data:
            if not isinstance(item, dict):
                continue

            hotel = item.get("hotel") if isinstance(item.get("hotel"), dict) else {}
            offers = item.get("offers") if isinstance(item.get("offers"), list) else []

            hid = hotel.get("hotelId") or hotel.get("id") or item.get("hotelId")
            name = hotel.get("name")
            if not hid or not name:
                continue

            # Phase 5: rating/stars extraction
            rating, stars = self._extract_rating_and_stars(hotel_obj=hotel, item_obj=item)

            best_total: Optional[float] = None
            best_cur: Optional[str] = None
            best_offer: Optional[Dict[str, Any]] = None

            for o in offers:
                if not isinstance(o, dict):
                    continue
                total, cur_s = offer_total_and_currency(o)
                if total is None:
                    continue
                if best_total is None or total < best_total:
                    best_total = total
                    best_cur = cur_s
                    best_offer = o

            if best_total is None and offers:
                o0 = offers[0] if isinstance(offers[0], dict) else {}
                total, cur_s = offer_total_and_currency(o0)
                best_total = total
                best_cur = cur_s
                best_offer = o0 if isinstance(o0, dict) else None

            ppn = (
                round(float(best_total) / float(nights), 2)
                if isinstance(best_total, (int, float)) and nights
                else None
            )

            payment_type: Optional[str] = None
            board_type: Optional[str] = None
            refundable: Optional[bool] = None
            offer_id: Optional[str] = None
            rate_code: Optional[str] = None
            cancel_deadline: Optional[str] = None

            if isinstance(best_offer, dict):
                try:
                    offer_id = best_offer.get("id")
                    rate_code = best_offer.get("rateCode")
                    board_type = best_offer.get("boardType")
                    pol = best_offer.get("policies") if isinstance(best_offer.get("policies"), dict) else {}
                    payment_type = pol.get("paymentType")
                    refundable = infer_refundable(best_offer)
                    cancel_deadline = first_cancel_deadline(best_offer)
                except Exception:
                    pass

            lat = hotel.get("latitude")
            lon = hotel.get("longitude")

            out.append(
                {
                    "id": str(hid),
                    "name": name,
                    "rating": rating,          # was always None; now best-effort
                    "stars": stars,            # NEW: useful for HotelResult.stars
                    "distance_miles": None,
                    "price_per_night": ppn,
                    "price_total": best_total,
                    "currency": best_cur,
                    "requested_currency": requested_currency,
                    "currency_mismatch": bool(
                        requested_currency and best_cur and requested_currency != best_cur
                    ),
                    "payment_type": payment_type,
                    "refundable": refundable,
                    "board_type": board_type,
                    "offer_id": offer_id,
                    "rate_code": rate_code,
                    "cancel_deadline": cancel_deadline,
                    "latitude": self._to_float(lat),
                    "longitude": self._to_float(lon),
                    "provider": PROVIDER,
                    "raw": item,
                    "raw_offer": best_offer,
                }
            )

        return out


def search_hotels(
    *,
    city: str,
    check_in: str,
    check_out: str,
    adults: int = 2,
    currency: Optional[str] = None,
    relax: bool = False,
    domain: str = "US",
) -> List[Dict[str, Any]]:
    client = make_client()
    adapter = AmadeusHotelsAdapter(client=client)

    loc = adapter.search_location(city=city, domain=domain)

    if not relax:
        return adapter.list_properties_normalized(
            destination_id=loc.destination_id,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            currency=currency,
        )

    seen_ids: set[str] = set()
    merged: List[Dict[str, Any]] = []

    passes = [
        (30, 60),    # (radius_km, hotel_id_limit)
        (50, 100),
        (80, 140),
    ]

    for radius_km, hotel_id_limit in passes:
        raw_bundle = adapter.list_properties_raw(
            destination_id=loc.destination_id,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            currency=currency,
            hotel_id_limit=hotel_id_limit,
            radius_km=radius_km,
        )
        offers_raw = raw_bundle.get("offers_raw")

        batch = adapter._normalize_offers_compat(
            raw=offers_raw,
            check_in=check_in,
            check_out=check_out,
            requested_currency=currency,
        )

        for h in batch:
            if not isinstance(h, dict):
                continue
            hid = h.get("id")
            if not isinstance(hid, str) or not hid.strip():
                continue
            if hid in seen_ids:
                continue
            seen_ids.add(hid)
            merged.append(h)

    return merged


def search(**kwargs) -> List[Dict[str, Any]]:
    return search_hotels(**kwargs)
