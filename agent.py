# agent.py
#
# Phase-2 CLI entrypoint for travel-agent (Amadeus-only).
# Prints recommendations + policy/attempt trace from orchestration runner.

from __future__ import annotations

import argparse
import math
import re
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from legacy.app.orchestration.runner import run
from legacy.entrypoints.nlp import nlp_parse
from legacy.nlp.enrich import extract_origin_destination_places, resolve_place_to_codes


# -------------------------
# Args
# -------------------------


def _parse_yyyy_mm_dd(s: str) -> Optional[date]:
    if not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def parse_args():
    p = argparse.ArgumentParser()

    # for NLP
    p.add_argument("--nlp", type=str, help="Natural language input")
    p.add_argument("--debug", action="store_true", help="Print NLP intent/plan when using --nlp")

    # Workflow
    p.add_argument("--domain", default="hotel_only", help="hotel_only | flight_only | bundle")

    # Shared-ish inputs
    p.add_argument("--adults", type=int, default=2)
    p.add_argument("--currency", default="USD", help="Display/request currency, e.g., USD")

    # Hotels inputs
    p.add_argument("--city", help="City name (hotel search), e.g., Orlando or Plano")
    p.add_argument("--check-in", help="YYYY-MM-DD (hotel)")
    p.add_argument("--check-out", help="YYYY-MM-DD (hotel)")

    # Flights inputs
    p.add_argument("--origin", help="IATA code, e.g., DFW (flight)")
    p.add_argument("--destination", help="IATA code, e.g., SFO (flight)")
    p.add_argument("--depart-date", help="YYYY-MM-DD (flight)")
    p.add_argument("--return-date", default=None, help="YYYY-MM-DD (optional, flight)")
    p.add_argument("--cabin", default="ECONOMY", help="ECONOMY | PREMIUM_ECONOMY | BUSINESS | FIRST")
    p.add_argument("--nonstop", action="store_true", help="Prefer nonstop (flight; falls back to connections if none)")
    p.add_argument("--max-stops", type=int, default=None, help="Max stops allowed for flights (0=nonstop)")

    # Flight output enhancements
    p.add_argument("--group-by-carrier", action="store_true", help="Group flight availabilities by carrier")
    p.add_argument(
        "--return-top",
        type=int,
        default=None,
        help="How many return flights to show (defaults to --top)",
    )

    # Policy loop knobs (hotels)
    p.add_argument("--min-results", type=int, default=10, help="Stop hotel attempts once >= this many results")
    p.add_argument("--max-attempts", type=int, default=5, help="How many hotel attempts to try (policy loop)")

    # Output controls
    p.add_argument("--top", type=int, default=5, help="How many recommendations to show")
    p.add_argument("--max-results", type=int, default=25, help="Max results to return (before printing top)")
    p.add_argument("--max-offers", type=int, default=50, help="Max flight offers to return (before printing top)")

    # Reserved; not used by Amadeus-only flow right now
    p.add_argument("--domain-country", default="US", help="Country/domain hint for geocoding (default US)")

    return p.parse_args()


# -------------------------
# Formatting helpers
# -------------------------


def _fmt_money(v: Any, decimals: int = 0) -> str:
    if isinstance(v, (int, float)) and not math.isnan(float(v)):
        if decimals <= 0:
            return f"{float(v):,.0f}"
        return f"{float(v):,.{decimals}f}"
    return "—"


def _fmt_bool(v: Any) -> str:
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    return "—"


def _fmt_text(v: Any, *, max_len: int) -> str:
    s = str(v) if v is not None else ""
    s = " ".join(s.split())
    if max_len <= 0:
        return ""
    if len(s) > max_len:
        return s[: max(0, max_len - 1)] + "…"
    return s


def _prettify_enum(v: Any) -> str:
    if not isinstance(v, str) or not v.strip():
        return "—"
    s = v.strip().replace("_", " ").lower()
    return s[:1].upper() + s[1:]


def _print_api_call_stats(stats: Dict[str, Any], *, indent: str = "") -> None:
    """
    Supports both shapes:
      1) flat stats: {"real_calls":..., "cache_hits":..., ...}
      2) bundle stats: {"flights": <flat>, "hotels": <flat>}
    """
    if not isinstance(stats, dict) or not stats:
        return

    def _is_flat(d: Dict[str, Any]) -> bool:
        return any(k in d for k in ("real_calls", "cache_hits", "token_calls", "retries", "endpoints"))

    def _print_flat(label: str, d: Dict[str, Any], *, extra_indent: str = "") -> None:
        print(f"{extra_indent}{label}")
        print(f"{extra_indent}  real amadeus calls: {d.get('real_calls', 0)}")
        print(f"{extra_indent}  cache hits:         {d.get('cache_hits', 0)}")
        print(f"{extra_indent}  token calls:        {d.get('token_calls', 0)}")
        print(f"{extra_indent}  retries:            {d.get('retries', 0)}")
        eps = d.get("endpoints") or {}
        if isinstance(eps, dict) and eps:
            print(f"{extra_indent}  endpoints:")
            for k, v in eps.items():
                print(f"{extra_indent}    {k}: {v}")

    # Flat stats
    if _is_flat(stats):
        _print_flat("API call count for this run:", stats, extra_indent=indent)
        return

    # Bundle stats (nested)
    flights_stats = stats.get("flights") if isinstance(stats.get("flights"), dict) else {}
    hotels_stats = stats.get("hotels") if isinstance(stats.get("hotels"), dict) else {}

    def _sum_key(key: str) -> int:
        return int(flights_stats.get(key, 0) or 0) + int(hotels_stats.get(key, 0) or 0)

    totals = {
        "real_calls": _sum_key("real_calls"),
        "cache_hits": _sum_key("cache_hits"),
        "token_calls": _sum_key("token_calls"),
        "retries": _sum_key("retries"),
    }

    print(f"{indent}API call count for this run (bundle):")
    print(f"{indent}  total real amadeus calls: {totals['real_calls']}")
    print(f"{indent}  total cache hits:         {totals['cache_hits']}")
    print(f"{indent}  total token calls:        {totals['token_calls']}")
    print(f"{indent}  total retries:            {totals['retries']}")

    if flights_stats:
        _print_flat("flights:", flights_stats, extra_indent=indent + "  ")
    if hotels_stats:
        _print_flat("hotels:", hotels_stats, extra_indent=indent + "  ")


# -------------------------
# Hotel helpers + printing
# -------------------------


def _nights_between(check_in: str, check_out: str) -> Optional[int]:
    try:
        ci = date.fromisoformat(check_in)
        co = date.fromisoformat(check_out)
        n = (co - ci).days
        return n if n > 0 else None
    except Exception:
        return None


def _total_cost(h: Dict[str, Any], nights: Optional[int]) -> Optional[float]:
    total = h.get("total_price")
    if isinstance(total, (int, float)):
        return float(total)
    ppn = h.get("price_per_night")
    if nights and isinstance(ppn, (int, float)):
        return float(ppn) * float(nights)
    return None


def print_hotels(*, results: List[Dict[str, Any]], goal: Dict[str, Any], top: int) -> None:
    hotels = results[:top]
    print("\nTop hotel recommendations:\n")

    if not hotels:
        print("(no hotels returned)\n")
        return

    nights = _nights_between(goal.get("check_in", ""), goal.get("check_out", ""))

    idx_w = 3
    name_w = 55
    ppn_w = 8
    total_w = 10
    cur_w = 5
    refund_w = 7
    board_w = 14
    pay_w = 12

    header = " ".join(
        [
            f"{'#':<{idx_w}}",
            f"{'Hotel':<{name_w}}",
            f"{'/night':>{ppn_w}}",
            f"{'Total':>{total_w}}",
            f"{'Cur':<{cur_w}}",
            f"{'Refund':<{refund_w}}",
            f"{'Board':<{board_w}}",
            f"{'Pay':<{pay_w}}",
        ]
    )
    print(header)
    print("-" * len(header))

    any_currency_mismatch = False

    for i, h in enumerate(hotels, start=1):
        name = _fmt_text(h.get("name"), max_len=name_w)

        ppn_disp = _fmt_money(h.get("price_per_night"), decimals=0)
        total_disp = _fmt_money(_total_cost(h, nights), decimals=0)

        cur = h.get("currency")
        cur_disp = cur if isinstance(cur, str) and cur.strip() else "—"

        req_cur = h.get("requested_currency") or goal.get("currency")
        if req_cur and cur_disp != "—" and req_cur != cur_disp:
            cur_disp = f"{cur_disp}*"
            any_currency_mismatch = True

        refundable = _fmt_bool(h.get("refundable"))
        board = _fmt_text(_prettify_enum(h.get("board_type")), max_len=board_w)
        pay = _fmt_text(_prettify_enum(h.get("payment_type")), max_len=pay_w)

        row = " ".join(
            [
                f"{i:<{idx_w}}",
                f"{name:<{name_w}}",
                f"{ppn_disp:>{ppn_w}}",
                f"{total_disp:>{total_w}}",
                f"{cur_disp:<{cur_w}}",
                f"{refundable:<{refund_w}}",
                f"{board:<{board_w}}",
                f"{pay:<{pay_w}}",
            ]
        )
        print(row)

    if any_currency_mismatch:
        print("\n* Currency differs from requested --currency (provider returned a different currency).")


def print_hotel_policy_trace(out: Dict[str, Any]) -> None:
    fp = out.get("final_policy") or {}
    attempts = out.get("attempts") or []

    print("\nFinal policy used:")
    if not fp:
        print("  (none)")
    else:
        label = fp.get("label", "unknown")
        dest = fp.get("destination_id", "—")
        radius = fp.get("radius_km", "—")
        limit_ = fp.get("hotel_id_limit", "—")
        attempt_no = fp.get("attempt", "—")
        note = fp.get("note")
        extra = f" | note={note}" if note else ""
        print(f"  {label} (attempt {attempt_no}) | dest={dest} | radius_km={radius} | hotel_id_limit={limit_}{extra}")

    if attempts:
        print("\nAgent decision trace (attempts):")
        for a in attempts:
            idx = a.get("attempt", "?")
            label = a.get("label", "unknown")
            dest = a.get("destination_id", "—")
            radius = a.get("radius_km", "—")
            limit_ = a.get("hotel_id_limit", "—")
            ranked_count = a.get("ranked_count", a.get("normalized_count", "?"))
            print(f"  {idx}. {label}: ranked={ranked_count} | dest={dest} | radius_km={radius} | hotel_id_limit={limit_}")
    else:
        print("\nAgent decision trace: single-pass (no attempts logged)")


# -------------------------
# Flights: availabilities helpers + printing
# -------------------------


def _parse_iso_duration_to_hm(dur: Any) -> str:
    if not isinstance(dur, str) or not dur.strip():
        return "—"
    s = dur.strip().upper()
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", s)
    if not m:
        return s
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    if h and mins:
        return f"{h}h{mins:02d}m"
    if h:
        return f"{h}h"
    return f"{mins}m" if mins else "0m"


def _availability_extract_segments(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    segs = item.get("segments")
    if isinstance(segs, list):
        return [s for s in segs if isinstance(s, dict)]
    return []


def _coerce_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None
    return None


def _is_nonstop_availability(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False

    for k in ("numberOfStops", "number_of_stops", "stopCount", "stops", "stopsCount"):
        if k in item:
            v = _coerce_int(item.get(k))
            if v is not None:
                return v == 0

    for path in (
        ("flightDetails", "numberOfStops"),
        ("details", "numberOfStops"),
        ("itinerary", "numberOfStops"),
    ):
        cur: Any = item
        ok = True
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                ok = False
                break
            cur = cur[p]
        if ok:
            v = _coerce_int(cur)
            if v is not None:
                return v == 0

    segs = item.get("segments")
    if isinstance(segs, list):
        segs = [s for s in segs if isinstance(s, dict)]
        if segs:
            return len(segs) == 1

    itins = item.get("itineraries")
    if isinstance(itins, list) and itins and isinstance(itins[0], dict):
        segs2 = itins[0].get("segments")
        if isinstance(segs2, list):
            segs2 = [s for s in segs2 if isinstance(s, dict)]
            if segs2:
                return len(segs2) == 1

    return False


def _availability_segment_carrier_code(seg: Dict[str, Any]) -> Optional[str]:
    op = seg.get("operating")
    if isinstance(op, dict):
        cc = op.get("carrierCode")
        if isinstance(cc, str) and cc.strip():
            return cc.strip().upper()

    cc = seg.get("carrierCode")
    if isinstance(cc, str) and cc.strip():
        return cc.strip().upper()

    return None


def _availability_carrier_flights(item: Dict[str, Any], max_len: int = 14) -> str:
    segs = _availability_extract_segments(item)
    if not segs:
        return "—"

    parts: List[str] = []
    for s in segs:
        cc = _availability_segment_carrier_code(s)
        num = s.get("number")

        if not cc:
            continue

        num_s: Optional[str] = None
        if isinstance(num, str) and num.strip():
            num_s = num.strip()
        elif isinstance(num, int):
            num_s = str(num)

        if num_s:
            parts.append(f"{cc} {num_s}")
        else:
            parts.append(cc)

    if not parts:
        return "—"

    try:
        carrier = parts[0].split()[0]
        nums: List[str] = []
        for p in parts:
            toks = p.split()
            if len(toks) == 2 and toks[0] == carrier:
                nums.append(toks[1])
            else:
                raise ValueError("mixed carriers")
        s = f"{carrier} " + "/".join(nums) if nums else carrier
    except Exception:
        s = " / ".join(parts[:3]) + (" / …" if len(parts) > 3 else "")

    return _fmt_text(s, max_len=max_len)


def _availability_depart_arr_times(item: Dict[str, Any]) -> Tuple[str, str]:
    segs = _availability_extract_segments(item)
    if not segs:
        return ("—", "—")

    dep = segs[0].get("departure") if isinstance(segs[0].get("departure"), dict) else {}
    arr = segs[-1].get("arrival") if isinstance(segs[-1].get("arrival"), dict) else {}

    dep_at = dep.get("at")
    arr_at = arr.get("at")

    def _hm(x: Any) -> str:
        if not isinstance(x, str) or "T" not in x:
            return "—"
        t = x.split("T", 1)[1]
        return t[:5] if len(t) >= 5 else "—"

    return (_hm(dep_at), _hm(arr_at))


def _availability_route(item: Dict[str, Any]) -> str:
    segs = _availability_extract_segments(item)
    if not segs:
        return "—"

    hops: List[str] = []
    for i, s in enumerate(segs):
        dep = s.get("departure") if isinstance(s.get("departure"), dict) else {}
        arr = s.get("arrival") if isinstance(s.get("arrival"), dict) else {}

        dep_iata = dep.get("iataCode")
        arr_iata = arr.get("iataCode")

        if i == 0:
            hops.append(dep_iata.strip().upper() if isinstance(dep_iata, str) and dep_iata.strip() else "—")
        hops.append(arr_iata.strip().upper() if isinstance(arr_iata, str) and arr_iata.strip() else "—")

    return " -> ".join(hops) if hops else "—"


def _availability_total_stops(item: Dict[str, Any]) -> int:
    segs = _availability_extract_segments(item)
    return max(0, len(segs) - 1)


def _iso_to_epoch_minutes(at: Any) -> Optional[int]:
    if not isinstance(at, str) or "T" not in at:
        return None
    try:
        t = at.split("T", 1)[1]
        hh = int(t[0:2])
        mm = int(t[3:5])
        return hh * 60 + mm
    except Exception:
        return None


def _availability_depart_minutes(item: Dict[str, Any]) -> int:
    segs = _availability_extract_segments(item)
    if not segs:
        return 10**9
    dep = segs[0].get("departure")
    dep_at = dep.get("at") if isinstance(dep, dict) else None
    m = _iso_to_epoch_minutes(dep_at)
    return m if m is not None else 10**9


def _iso_duration_to_minutes(dur: Any) -> int:
    if not isinstance(dur, str) or not dur.strip():
        return 10**9
    s = dur.strip().upper()
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", s)
    if not m:
        return 10**9
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    return h * 60 + mins


def _availability_sort_key(item: Dict[str, Any]) -> Tuple[int, int, int]:
    stops = _availability_total_stops(item)
    dur_mins = _iso_duration_to_minutes(item.get("duration"))
    dep_mins = _availability_depart_minutes(item)
    return (stops, dur_mins, dep_mins)


def _sort_availabilities(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=_availability_sort_key)


def _availability_primary_carrier(item: Dict[str, Any]) -> str:
    segs = _availability_extract_segments(item)
    if not segs:
        return "—"
    cc = _availability_segment_carrier_code(segs[0])
    return cc if cc else "—"


def _group_by_carrier(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        buckets[_availability_primary_carrier(it)].append(it)
    return dict(sorted(buckets.items(), key=lambda kv: (kv[0] == "—", kv[0])))


def _split_availabilities_by_od(results: Any, goal: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(results, dict):
        onward = results.get("onward")
        ret = results.get("return")
        if isinstance(onward, list) or isinstance(ret, list):
            return {
                "onward": [x for x in (onward or []) if isinstance(x, dict)],
                "return": [x for x in (ret or []) if isinstance(x, dict)],
            }

    items: List[Dict[str, Any]] = [x for x in results if isinstance(x, dict)] if isinstance(results, list) else []
    if not items:
        return {"onward": [], "return": []}

    def _first_dep_iata(it: Dict[str, Any]) -> str:
        segs = _availability_extract_segments(it)
        if not segs:
            return ""
        dep = segs[0].get("departure") if isinstance(segs[0].get("departure"), dict) else {}
        return str(dep.get("iataCode") or "").strip().upper()

    def _last_arr_iata(it: Dict[str, Any]) -> str:
        segs = _availability_extract_segments(it)
        if not segs:
            return ""
        arr = segs[-1].get("arrival") if isinstance(segs[-1].get("arrival"), dict) else {}
        return str(arr.get("iataCode") or "").strip().upper()

    origin_req = str(goal.get("origin") or "").strip().upper()
    dest_req = str(goal.get("destination") or "").strip().upper()
    is_round_trip = bool(goal.get("return_date"))

    onward_out: List[Dict[str, Any]] = []
    return_out: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []

    if is_round_trip and origin_req and dest_req:
        for it in items:
            dep = _first_dep_iata(it)
            arr = _last_arr_iata(it)
            if dep == origin_req and arr == dest_req:
                onward_out.append(it)
            elif dep == dest_req and arr == origin_req:
                return_out.append(it)
            else:
                unknown.append(it)

        if onward_out or return_out:
            for it in unknown:
                arr = _last_arr_iata(it)
                if arr == dest_req:
                    onward_out.append(it)
                elif arr == origin_req:
                    return_out.append(it)
                else:
                    onward_out.append(it)
            return {"onward": onward_out, "return": return_out}

    for it in items:
        od = str(it.get("originDestinationId") or "").strip()
        if od == "2":
            return_out.append(it)
        else:
            onward_out.append(it)

    return {"onward": onward_out, "return": return_out}


def _filter_nonstop_preferred(items: List[Dict[str, Any]], *, prefer_nonstop: bool) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not items:
        return [], None
    if not prefer_nonstop:
        return items, None

    nonstop = [x for x in items if _is_nonstop_availability(x)]
    if nonstop:
        return nonstop, None

    return items, "No nonstop options found; showing connecting itineraries."


def _print_availability_table(*, title: str, items: List[Dict[str, Any]], top: int) -> None:
    print(f"\n{title}\n")

    if not items:
        print("(no flights returned)\n")
        return

    idx_w = 3
    cf_w = 14
    stops_w = 5
    dur_w = 8
    time_w = 11
    route_w = 52

    header = " ".join(
        [
            f"{'#':<{idx_w}}",
            f"{'Carrier/Flt':<{cf_w}}",
            f"{'Stops':>{stops_w}}",
            f"{'Dur':<{dur_w}}",
            f"{'Time':<{time_w}}",
            f"{'Route':<{route_w}}",
        ]
    )
    print(header)
    print("-" * len(header))

    for i, it in enumerate(items[:top], start=1):
        cf = _availability_carrier_flights(it, max_len=cf_w)
        stops = _availability_total_stops(it)
        dur = _parse_iso_duration_to_hm(it.get("duration"))
        dep_t, arr_t = _availability_depart_arr_times(it)
        time_range = f"{dep_t}-{arr_t}"
        route = _fmt_text(_availability_route(it), max_len=route_w)

        row = " ".join(
            [
                f"{i:<{idx_w}}",
                f"{cf:<{cf_w}}",
                f"{stops:>{stops_w}}",
                f"{dur:<{dur_w}}",
                f"{time_range:<{time_w}}",
                f"{route:<{route_w}}",
            ]
        )
        print(row)


def print_flight_availabilities(
    *,
    results: Any,
    goal: Dict[str, Any],
    top: int,
    is_round_trip: bool,
    prefer_nonstop: bool = False,
    group_by_carrier: bool = False,
    return_top: Optional[int] = None,
    note: Optional[str] = None,
) -> None:
    if not results:
        results = []

    grouped = _split_availabilities_by_od(results, goal)
    onward = grouped.get("onward") or []
    ret = grouped.get("return") or []

    def _strip_nonstop_fallback(note_: Optional[str], *, has_any_nonstop: bool) -> Optional[str]:
        if not note_:
            return note_
        if not has_any_nonstop:
            return note_

        bad = "No nonstop options found; showing connecting itineraries."
        lines = [ln.strip() for ln in str(note_).splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln != bad]
        return "\n".join(lines) if lines else None

    def _has_any_nonstop(items_: List[Dict[str, Any]]) -> bool:
        try:
            return any(_availability_total_stops(it) == 0 for it in items_)
        except Exception:
            return False

    onward = _sort_availabilities(onward)
    ret = _sort_availabilities(ret)

    onward, onward_note = _filter_nonstop_preferred(onward, prefer_nonstop=prefer_nonstop)
    ret, return_note = _filter_nonstop_preferred(ret, prefer_nonstop=prefer_nonstop)

    has_onward_nonstop = _has_any_nonstop(onward)
    has_return_nonstop = _has_any_nonstop(ret)
    has_any_nonstop = has_onward_nonstop or has_return_nonstop

    if has_onward_nonstop:
        onward_note = None
    if has_return_nonstop:
        return_note = None

    note = _strip_nonstop_fallback(note, has_any_nonstop=has_any_nonstop)

    top_onward = int(top)
    top_return = int(return_top) if return_top is not None else int(top)

    def _print_leg(title: str, items_: List[Dict[str, Any]], top_n: int) -> None:
        if not group_by_carrier:
            _print_availability_table(title=title, items=items_, top=top_n)
            return

        print(f"\n{title}\n")
        if not items_:
            print("(no flights returned)\n")
            return

        buckets = _group_by_carrier(items_)
        for carrier, carrier_items in buckets.items():
            _print_availability_table(title=carrier, items=carrier_items, top=top_n)

    if is_round_trip:
        _print_leg("Onward Journey", onward, top_onward)
        _print_leg("Return Journey", ret, top_return)
        if not ret:
            print("\n(no return flights returned)\n")
    else:
        _print_leg("Top flight recommendations", onward, top_onward)

    note_lines: List[str] = []
    if onward_note and is_round_trip:
        note_lines.append(f"Onward: {onward_note}")
    elif onward_note:
        note_lines.append(onward_note)

    if return_note:
        note_lines.append(f"Return: {return_note}" if is_round_trip else return_note)

    if note:
        note_lines.append(note)

    if note_lines:
        print("\nNote: " + "\n".join(note_lines))


# -------------------------
# Request builders
# -------------------------


def build_request_from_cli(args) -> tuple[str, Dict[str, Any]]:
    domain = (args.domain or "hotel_only").strip().lower()

    payload: Dict[str, Any] = {
        "currency": args.currency,
        "adults": args.adults,
        "max_results": args.max_results,
        "max_offers": args.max_offers,
        "domain_country": args.domain_country,
        # hotel knobs
        "min_results": args.min_results,
        "max_attempts": args.max_attempts,
    }

    if domain == "hotel_only":
        if not args.city or not args.check_in or not args.check_out:
            raise SystemExit("--city, --check-in, --check-out are required for --domain hotel_only")

        ci = _parse_yyyy_mm_dd(args.check_in)
        co = _parse_yyyy_mm_dd(args.check_out)
        if not ci or not co:
            raise SystemExit("Invalid date format. Use YYYY-MM-DD for --check-in/--check-out.")
        if co <= ci:
            raise SystemExit("--check-out must be after --check-in.")

        payload.update({"city": args.city, "check_in": args.check_in, "check_out": args.check_out})

    elif domain == "flight_only":
        if not args.origin or not args.destination or not args.depart_date:
            raise SystemExit("--origin, --destination, --depart-date are required for --domain flight_only")

        dd = _parse_yyyy_mm_dd(args.depart_date)
        if not dd:
            raise SystemExit("Invalid date format. Use YYYY-MM-DD for --depart-date.")
        if args.return_date:
            rd = _parse_yyyy_mm_dd(args.return_date)
            if not rd:
                raise SystemExit("Invalid date format. Use YYYY-MM-DD for --return-date.")
            if rd < dd:
                raise SystemExit("--return-date must be on/after --depart-date.")

        max_stops = args.max_stops
        if args.nonstop:
            max_stops = 0

        payload.update(
            {
                "origin": args.origin,
                "destination": args.destination,
                "depart_date": args.depart_date,
                "return_date": args.return_date,
                "cabin": args.cabin,
                "nonstop_only": args.nonstop,
                "max_stops": max_stops,
            }
        )

    elif domain == "bundle":
        if not args.origin or not args.destination or not args.depart_date:
            raise SystemExit("--origin, --destination, --depart-date are required for --domain bundle")
        if not args.check_in or not args.check_out:
            raise SystemExit("--check-in and --check-out are required for --domain bundle")
        if not args.city:
            raise SystemExit("--city is required for --domain bundle (hotel location)")

        ci = _parse_yyyy_mm_dd(args.check_in)
        co = _parse_yyyy_mm_dd(args.check_out)
        if not ci or not co:
            raise SystemExit("Invalid date format. Use YYYY-MM-DD for --check-in/--check-out.")
        if co <= ci:
            raise SystemExit("--check-out must be after --check-in.")

        dd = _parse_yyyy_mm_dd(args.depart_date)
        if not dd:
            raise SystemExit("Invalid date format. Use YYYY-MM-DD for --depart-date.")
        if args.return_date:
            rd = _parse_yyyy_mm_dd(args.return_date)
            if not rd:
                raise SystemExit("Invalid date format. Use YYYY-MM-DD for --return-date.")
            if rd < dd:
                raise SystemExit("--return-date must be on/after --depart-date.")

        max_stops = args.max_stops
        if args.nonstop:
            max_stops = 0

        payload.update(
            {
                "origin": args.origin,
                "destination": args.destination,
                "depart_date": args.depart_date,
                "return_date": args.return_date,
                "cabin": args.cabin,
                "nonstop_only": args.nonstop,
                "max_stops": max_stops,
                "city": args.city,
                "check_in": args.check_in,
                "check_out": args.check_out,
            }
        )
    else:
        raise SystemExit("Unknown --domain. Use hotel_only | flight_only | bundle")

    return domain, payload


def render_output(domain: str, args, out: Dict[str, Any], *, payload_override: Optional[Dict[str, Any]] = None) -> None:
    """
    Renders output consistently for both:
      - CLI path (values come from args.*)
      - NLP path (values come from payload_override because args may be empty)
    """
    payload = payload_override or {}

    if out.get("error"):
        print(f"\nError: {out['error']}")
        if out.get("status_code"):
            print(f"HTTP status: {out.get('status_code')}")

        run_log_path = out.get("run_log_path")
        if run_log_path:
            print(f"Saved run log: {run_log_path}")

        stats = out.get("api_call_stats") or {}
        if stats:
            print()
            _print_api_call_stats(stats)

        raise SystemExit(2)

    print(f"\nProvider: {out.get('provider', 'amadeus')}")

    if domain == "hotel_only":
        results = out.get("results") or []
        goal = {
            "city": payload.get("city") or args.city,
            "check_in": payload.get("check_in") or args.check_in,
            "check_out": payload.get("check_out") or args.check_out,
            "adults": payload.get("adults") or args.adults,
            "currency": payload.get("currency") or args.currency,
        }
        print_hotels(results=results, goal=goal, top=args.top)
        print_hotel_policy_trace(out)

    elif domain == "flight_only":
        results = out.get("results") or []
        goal_f = {
            "origin": payload.get("origin") or args.origin,
            "destination": payload.get("destination") or args.destination,
            "depart_date": payload.get("depart_date") or args.depart_date,
            "return_date": payload.get("return_date") or args.return_date,
            "currency": payload.get("currency") or args.currency,
            "adults": payload.get("adults") or args.adults,
        }

        print_flight_availabilities(
            results=results,
            goal=goal_f,
            top=args.top,
            return_top=args.return_top,
            group_by_carrier=bool(args.group_by_carrier),
            is_round_trip=bool(goal_f.get("return_date")),
            prefer_nonstop=bool(payload.get("nonstop_only") or args.nonstop),
            note=out.get("note"),
        )

    elif domain == "bundle":
        bundle_results = out.get("results") or {}
        flights = bundle_results.get("flights") or []
        hotels = bundle_results.get("hotels") or []

        goal_f = {
            "origin": payload.get("origin") or args.origin,
            "destination": payload.get("destination") or args.destination,
            "depart_date": payload.get("depart_date") or args.depart_date,
            "return_date": payload.get("return_date") or args.return_date,
            "currency": payload.get("currency") or args.currency,
            "adults": payload.get("adults") or args.adults,
        }

        print_flight_availabilities(
            results=flights,
            goal=goal_f,
            top=args.top,
            return_top=args.return_top,
            group_by_carrier=bool(args.group_by_carrier),
            is_round_trip=bool(goal_f.get("return_date")),
            prefer_nonstop=bool(payload.get("nonstop_only") or args.nonstop),
            note=out.get("note"),
        )

        goal_h = {
            "city": payload.get("city") or args.city,
            "check_in": payload.get("check_in") or args.check_in,
            "check_out": payload.get("check_out") or args.check_out,
            "adults": payload.get("adults") or args.adults,
            "currency": payload.get("currency") or args.currency,
        }
        print_hotels(results=hotels, goal=goal_h, top=args.top)

        comps = out.get("components")
        if isinstance(comps, dict):
            print("\nBundle components:")
            f = comps.get("flights") or {}
            h = comps.get("hotels") or {}
            if f:
                print(f"  flights: run_id={f.get('run_id')} log={f.get('run_log_path')}")
            if h:
                print(f"  hotels:  run_id={h.get('run_id')} log={h.get('run_log_path')}")

    run_log_path = out.get("run_log_path")
    if run_log_path:
        print(f"\nSaved run log: {run_log_path}")

    stats = out.get("api_call_stats") or {}
    if stats:
        print()
        _print_api_call_stats(stats)


def build_request_from_nlp(
    text: str,
    args,
) -> Tuple[str, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Returns:
      (domain, payload, intent, plan)
    """
    intent, plan = nlp_parse(text)

    domain = (intent.get("domain") or "hotel_only").strip().lower()
    if domain not in ("hotel_only", "flight_only", "bundle"):
        domain = "hotel_only"

    pax = intent.get("passengers") or {}
    constraints = intent.get("constraints") or {}

    # ------------------------------------------------------------
    # Resolve flight places from RAW TEXT first (keeps metro alts)
    # ------------------------------------------------------------
    raw_orig, raw_dest = extract_origin_destination_places(text)

    origin_primary: Optional[str] = None
    origin_alts: List[str] = []
    dest_primary: Optional[str] = None
    dest_alts: List[str] = []

    if raw_orig:
        origin_primary, origin_alts, _ = resolve_place_to_codes(raw_orig)
    if raw_dest:
        dest_primary, dest_alts, _ = resolve_place_to_codes(raw_dest)

    # Fallback to whatever rule_parser produced if still missing
    origin_final = origin_primary or intent.get("origin")
    dest_final = dest_primary or intent.get("destination")

    # ------------------------------------------------------------
    # Merge CLI overrides with NLP constraints
    # ------------------------------------------------------------
    nlp_nonstop = bool(constraints.get("nonstop") or False)
    nonstop_only = bool(nlp_nonstop or bool(getattr(args, "nonstop", False)))

    max_stops = constraints.get("max_stops")
    if max_stops is None:
        max_stops = getattr(args, "max_stops", None)

    if nonstop_only:
        max_stops = 0

    payload: Dict[str, Any] = {
        "currency": intent.get("currency") or args.currency,
        "adults": int(pax.get("adults") or args.adults or 1),
        "max_results": args.max_results,
        "max_offers": args.max_offers,
        "domain_country": args.domain_country,
        "min_results": args.min_results,
        "max_attempts": args.max_attempts,
        # debug / transparency
        "nlp_raw_text": text,
    }

    # -------------------------
    # flight_only
    # -------------------------
    if domain == "flight_only":
        payload.update(
            {
                "origin": origin_final,
                "destination": dest_final,
                "origin_alts": origin_alts,
                "destination_alts": dest_alts,
                "depart_date": intent.get("depart_date"),
                "return_date": intent.get("return_date"),
                "cabin": constraints.get("cabin") or args.cabin,
                "nonstop_only": nonstop_only,
                "max_stops": max_stops,
            }
        )
        return domain, payload, intent, plan

    # -------------------------
    # hotel_only
    # -------------------------
    if domain == "hotel_only":
        payload.update(
            {
                "city": intent.get("city"),
                "check_in": intent.get("check_in"),
                "check_out": intent.get("check_out"),
            }
        )
        return domain, payload, intent, plan

    # -------------------------
    # bundle
    # -------------------------
    hotel_city = intent.get("city")

    check_in = intent.get("check_in")
    check_out = intent.get("check_out")

    # if hotel dates missing, use flight dates
    if not check_in:
        check_in = intent.get("depart_date")
    if not check_out:
        check_out = intent.get("return_date")

    hotel_destination_id: Optional[str] = None
    if hotel_city:
        hotel_destination_id = hotel_city
    else:
        if isinstance(dest_final, str) and len(dest_final.strip()) == 3:
            hotel_destination_id = f"CITY:{dest_final.strip().upper()}"
        else:
            hotel_destination_id = dest_final

    payload.update(
        {
            # flights
            "origin": origin_final,
            "destination": dest_final,
            "origin_alts": origin_alts,
            "destination_alts": dest_alts,
            "depart_date": intent.get("depart_date"),
            "return_date": intent.get("return_date"),
            "cabin": constraints.get("cabin") or args.cabin,
            "nonstop_only": nonstop_only,
            "max_stops": max_stops,
            # hotels
            "hotel_city": hotel_city,
            "hotel_destination_id": hotel_destination_id,
            "check_in": check_in,
            "check_out": check_out,
        }
    )

    return domain, payload, intent, plan


# -------------------------
# Main
# -------------------------


def main():
    args = parse_args()

    if args.nlp:
        domain, payload, intent, plan = build_request_from_nlp(args.nlp, args)

        if getattr(args, "debug", False):
            print("\n--- NLP Intent ---")
            print(intent)
            print("\n--- NLP Plan ---")
            print(plan)

        out = run(domain=domain, payload=payload, runs_dir="runs")
        # ✅ IMPORTANT: use payload for rendering (args may be empty in NLP mode)
        render_output(domain, args, out, payload_override=payload)
        return

    domain, payload = build_request_from_cli(args)
    out = run(domain=domain, payload=payload, runs_dir="runs")
    render_output(domain, args, out, payload_override=payload)


if __name__ == "__main__":
    main()
