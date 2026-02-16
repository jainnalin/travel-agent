import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import urllib.request
from legacy.nlp.rule_parser import parse_rule_based

BASE = "http://127.0.0.1:8000/search"

def _post(payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, headers={"content-type": "application/json"})
    resp = urllib.request.urlopen(req).read()
    return json.loads(resp)

def _print_table(cols, rows):
    if not rows:
        print("(no rows)")
        return
    widths = [max(len(str(x)) for x in [c] + [r[i] for r in rows]) for i, c in enumerate(cols)]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*cols))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt.format(*[str(x) for x in r]))

def _flights_table(label, items):
    if not items:
        print(f"\n{label}\n(no results)")
        return
    cols = ["#", "Carrier/Flt", "Stops", "Dur", "Time", "Route"]
    rows = []
    for i, it in enumerate(items, 1):
        segs = it.get("segments") or []
        if not segs:
            continue
        dep0 = (segs[0].get("departure") or {})
        arrL = (segs[-1].get("arrival") or {})
        first_dep = (dep0.get("iataCode") or "")
        last_arr = (arrL.get("iataCode") or "")
        dep_t = (dep0.get("at") or "")[11:16]
        arr_t = (arrL.get("at") or "")[11:16]
        dur = it.get("duration") or ""
        stops = max(0, len(segs) - 1)

        op = segs[0].get("operating") if isinstance(segs[0].get("operating"), dict) else {}
        cc = (op.get("carrierCode") or segs[0].get("carrierCode") or "")
        num = (op.get("number") or segs[0].get("number") or "")
        rows.append([i, f"{cc} {num}".strip(), stops, dur, f"{dep_t}-{arr_t}", f"{first_dep}->{last_arr}"])

    print(f"\n{label}")
    _print_table(cols, rows)

def _hotels_table(items):
    if not items:
        print("\nHOTELS\n(no results)")
        return
    cols = ["#", "Hotel", "/night", "Total", "Cur", "Refund", "Board", "Pay"]
    rows = []
    for i, h in enumerate(items, 1):
        rows.append([
            i,
            (h.get("hotel") or h.get("name") or "")[:44],
            h.get("price_per_night") or h.get("nightly") or "",
            h.get("total_price") or h.get("total") or "",
            h.get("currency") or "",
            h.get("refundable") if h.get("refundable") is not None else h.get("refund") or "",
            h.get("board") or "",
            h.get("payment") or h.get("pay") or "",
        ])
    print("\nHOTELS")
    _print_table(cols, rows)

def _compute_counts_and_status(intent_domain: str, result_obj: dict) -> tuple[int, int, int, str]:
    """
    Returns: (flight_count, hotel_count, warning_count_total, status)
    status in: OK | OK_WITH_WARNINGS | FAILED
    """
    results = result_obj.get("results")

    # warnings
    warning_total = 0
    if "warning_summary" in result_obj:
        ws = result_obj.get("warning_summary") or {}
        wf = int(((ws.get("flights") or {}).get("warning_count") or 0))
        wh = int(((ws.get("hotels") or {}).get("warning_count") or 0))
        warning_total = wf + wh
    else:
        warning_total = int(result_obj.get("warning_count") or 0)

    # counts
    flight_count = 0
    hotel_count = 0

    if intent_domain == "flight_only":
        flight_count = len(results or [])
    elif intent_domain == "hotel_only":
        hotel_count = len(results or [])
    elif intent_domain == "bundle":
        if isinstance(results, dict):
            fl = results.get("flights") or {}
            ht = results.get("hotels") or []
            if isinstance(fl, dict):
                flight_count = len(fl.get("onward") or []) + len(fl.get("return") or [])
            else:
                flight_count = len(fl or [])
            hotel_count = len(ht or [])
        else:
            # unexpected shape; treat as failure-ish
            flight_count = 0
            hotel_count = 0

    total_items = flight_count + hotel_count

    if total_items == 0:
        status = "FAILED"
    elif warning_total > 0:
        status = "OK_WITH_WARNINGS"
    else:
        status = "OK"

    return flight_count, hotel_count, warning_total, status

def run_text(text: str):
    intent = parse_rule_based(text)
    payload = {
        "domain": intent.domain,
        "origin": intent.origin,
        "destination": intent.destination,
        "depart_date": intent.depart_date,
        "return_date": intent.return_date,
        "origin_alts": getattr(intent, "origin_alts", []) or [],
        "destination_alts": getattr(intent, "destination_alts", []) or [],
        "city": intent.city or "",
        "check_in": intent.check_in,
        "check_out": intent.check_out,
        "adults": intent.passengers.adults,
        "currency": intent.currency or "USD",
        "max_offers": 10,
        "max_results": 10,
        "min_results": 10,
        "max_attempts": 5,
    }

    print("\n" + "=" * 90)
    print("TEXT:", text)
    print("INTENT:", intent)

    out = _post(payload)
    r = out.get("result") or {}

    print("RUN:", r.get("run_log_path"))

    # warnings (consistent + debuggable)
    if "warning_summary" in r:
        ws = r.get("warning_summary") or {}
        fw = ws.get("flights") or {}
        hw = ws.get("hotels") or {}

        print(
            "WARNINGS flights:", fw.get("warning_count"),
            "hotels:", hw.get("warning_count")
        )
        print("  flight_warn_sample:", (fw.get("warnings_sample") or [])[:1])
        print("  hotel_warn_sample:", (hw.get("warnings_sample") or [])[:1])
    else:
        print("WARNINGS:", r.get("warning_count"))
        print("  warn_sample:", (r.get("warnings_sample") or [])[:1])

    # bundle component runs (helps debug)
    if intent.domain == "bundle":
        comps = r.get("components") or {}
        print("  flight_run:", (comps.get("flights") or {}).get("run_log_path"))
        print("  hotel_run:", (comps.get("hotels") or {}).get("run_log_path"))

    flight_count, hotel_count, warn_total, status = _compute_counts_and_status(intent.domain, r)
    print("COUNTS flights:", flight_count, "hotels:", hotel_count, "warnings_total:", warn_total)
    print("STATUS:", status)

    results = r.get("results")

    if intent.domain == "flight_only":
        _flights_table("FLIGHTS", results or [])
    elif intent.domain == "hotel_only":
        _hotels_table(results or [])
    elif intent.domain == "bundle":
        results = results or {}
        fl = (results.get("flights") or {})
        ht = (results.get("hotels") or [])
        _flights_table("FLIGHTS (ONWARD)", fl.get("onward") if isinstance(fl, dict) else fl)
        if isinstance(fl, dict) and "return" in fl:
            _flights_table("FLIGHTS (RETURN)", fl.get("return"))
        _hotels_table(ht)
    else:
        print("Unsupported domain from NLP:", intent.domain)

if __name__ == "__main__":
    run_text("provide me hotels in Orlando for 3 days")
    run_text("book me trip from Austin to Miami for 3 days from Feb 20th")
