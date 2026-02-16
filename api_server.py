# api_server.py
#
# Phase-2 API Server for travel-agent (Amadeus-only).
#
# Endpoints:
#   GET  /health
#   POST /search                -> runs orchestration, returns result JSON (plus run_log_path)
#   POST /search/stream         -> SSE stream (started -> attempts -> done -> summary)
#   GET  /runs/{run_id}         -> returns saved run JSON (file-based)
#   GET  /runs/latest           -> returns most recent run (file-based)
#   GET  /runs                  -> list runs (file-based) with optional filters + pagination metadata
#   GET  /runs/{run_id}/summary -> compact summary for UI
#   GET  /runs/{run_id}/explain -> human-readable reasoning (file-based)
#   GET  /runs/{run_id}/events  -> SSE tail (in-memory only)
#
# Run:
#   pip install fastapi uvicorn anyio
#   uvicorn api_server:app --reload --port 8000

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from legacy.app.explain.explain import explain_run  # expects app/explain/explain.py with explain_run()
from legacy.app.orchestration.runner import run as orchestrate_run

from legacy.app.geo.resolve import geo_resolver_stats
from legacy.app.geo.resolve import geo_resolver_reset_stats
from legacy.app.geo.resolve import resolve_to_codes


RUNS_DIR = Path("runs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Travel Agent API", version="2.3")


# -------------------------
# In-memory SSE broker
# -------------------------


class RunEventBroker:
    """
    Minimal in-memory broker:
    - Keeps a small ring-buffer per run_id for late joiners
    - Supports multiple SSE subscribers per run_id
    - Not durable across restarts
    """

    def __init__(self, keep_last: int = 200, ttl_seconds: int = 3600):
        self.keep_last = keep_last
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._buffers: Dict[str, List[Dict[str, Any]]] = {}
        self._subs: Dict[str, Set[asyncio.Queue]] = {}
        self._last_seen: Dict[str, float] = {}

    async def publish(self, run_id: str, event: Dict[str, Any]) -> None:
        now = time.time()
        async with self._lock:
            self._last_seen[run_id] = now

            buf = self._buffers.setdefault(run_id, [])
            buf.append(event)
            if len(buf) > self.keep_last:
                del buf[: len(buf) - self.keep_last]

            subs = self._subs.get(run_id, set())
            for q in list(subs):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

            self._cleanup_locked(now)

    async def subscribe(self, run_id: str, replay: bool = True) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._last_seen[run_id] = time.time()
            self._subs.setdefault(run_id, set()).add(q)

            if replay:
                for e in self._buffers.get(run_id, []):
                    try:
                        q.put_nowait(e)
                    except asyncio.QueueFull:
                        break
        return q

    async def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subs.get(run_id)
            if subs and q in subs:
                subs.remove(q)
                if not subs:
                    self._subs.pop(run_id, None)

    def _cleanup_locked(self, now: float) -> None:
        dead = [rid for rid, ts in self._last_seen.items() if (now - ts) > self.ttl_seconds]
        for rid in dead:
            self._last_seen.pop(rid, None)
            self._buffers.pop(rid, None)
            self._subs.pop(rid, None)


broker = RunEventBroker()

_run_seq: Dict[str, int] = {}
_run_seq_lock = asyncio.Lock()


async def next_seq(run_id: str) -> int:
    async with _run_seq_lock:
        cur = _run_seq.get(run_id, 0) + 1
        _run_seq[run_id] = cur
        return cur


def sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def publish(run_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    seq = await next_seq(run_id)
    ev: Dict[str, Any] = {
        "run_id": run_id,
        "type": event_type,
        "seq": seq,
        "ts": time.time(),
        **payload,
    }
    await broker.publish(run_id, ev)
    return ev


# -------------------------
# Request/Response models
# -------------------------


class SearchRequest(BaseModel):
    """
    Unified request model.
    Notes:
      - For hotel_only, hotel fields are required.
      - For flight_only, flight fields are required.
      - For bundle, both hotel + flight fields are required.
    """

    # Workflow
    domain: str = Field("hotel_only", description="hotel_only | flight_only | bundle")

    # Shared-ish
    adults: int = 2
    currency: str = "USD"

    # Hotels request
    city: Optional[str] = Field(default=None, examples=["Orlando", "Plano"])
    check_in: Optional[str] = Field(default=None, description="YYYY-MM-DD", examples=["2026-03-15"])
    check_out: Optional[str] = Field(default=None, description="YYYY-MM-DD", examples=["2026-03-20"])

    # Flights request
    origin: Optional[str] = Field(default=None, description="IATA code, e.g., DFW")
    destination: Optional[str] = Field(default=None, description="IATA code, e.g., SFO")
    depart_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    return_date: Optional[str] = Field(default=None, description="YYYY-MM-DD (optional)")
    cabin: str = Field("ECONOMY", description="ECONOMY | PREMIUM_ECONOMY | BUSINESS | FIRST")
    nonstop: bool = Field(False, description="Nonstop only (flight)")
    max_stops: Optional[int] = Field(None, description="Max stops (0 => nonstop)")
    # Optional metro expansion (flight)
    origin_alts: Optional[List[str]] = Field(default=None, description="Alternate origin IATA codes, e.g. ['JFK','LGA','EWR']")
    destination_alts: Optional[List[str]] = Field(default=None, description="Alternate destination IATA codes")


    # Policy loop knobs (hotels)
    min_results: int = Field(10, description="Stop hotel attempts once >= this many results")
    max_attempts: int = Field(5, description="How many hotel attempts to try (policy loop)")

    # Response controls
    max_results: int = Field(25, description="Max hotel results to return (ranked list length)")
    max_offers: int = Field(10, description="Max flight offers to return (ranked list length)")
    top: int = Field(5, description="How many recommendations to show in summary")

    # Reserved
    domain_country: str = Field("US", description="Country/domain hint for geocoding (default US)")


class SearchResponse(BaseModel):
    result: Dict[str, Any]


# -------------------------
# File-based run storage helpers
# -------------------------


def _safe_run_id(run_id: str) -> str:
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")
    return run_id.strip()


def _find_run_file(run_id: str) -> Path:
    rid = _safe_run_id(run_id)

    # Exact match
    exact = RUNS_DIR / f"{rid}.json"
    if exact.exists():
        return exact

    # Suffix match (short id)
    matches = sorted(
        RUNS_DIR.glob(f"*-{rid}.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return matches[0]

    # UUID prefix match
    short = rid.split("-", 1)[0]
    if len(short) >= 6:
        matches = sorted(
            RUNS_DIR.glob(f"*-{short}.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if matches:
            return matches[0]

    raise HTTPException(status_code=404, detail="Run not found")


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read run log: {e}")


def _latest_run_file() -> Path:
    items = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not items:
        raise HTTPException(status_code=404, detail="No runs found")
    return items[0]


def _compact_hotels(results: Any, top: int) -> List[Dict[str, Any]]:
    top_hotels: List[Dict[str, Any]] = []
    if not isinstance(results, list):
        return top_hotels

    for h in results[:top]:
        if not isinstance(h, dict):
            continue
        top_hotels.append(
            {
                "hotel_id": h.get("hotel_id"),
                "name": h.get("name"),
                "price_per_night": h.get("price_per_night"),
                "total_price": h.get("total_price"),
                "currency": h.get("currency"),
                "requested_currency": h.get("requested_currency"),
                "currency_mismatch": h.get("currency_mismatch"),
                "refundable": h.get("refundable"),
                "board_type": h.get("board_type"),
                "payment_type": h.get("payment_type"),
            }
        )
    return top_hotels


def _compact_flights(results: Any, top: int) -> List[Dict[str, Any]]:
    """
    Compact flight results for UI.

    Supports:
      - offers mode: dicts with offer_id/total/currency/itineraries...
      - availabilities mode: dicts with top-level 'segments' (often type='flight-availability')
      - round-trip availabilities: {"onward":[...], "return":[...]}
        -> returns a flattened list with `leg` field: "onward" | "return"
    """
    # ----------------------------
    # Normalize input "results"
    # ----------------------------
    normalized: List[Dict[str, Any]] = []

    # Round-trip dict shape: {"onward":[...], "return":[...]}
    if isinstance(results, dict) and ("onward" in results or "return" in results):
        onward = results.get("onward")
        ret = results.get("return")

        if isinstance(onward, list):
            for x in onward:
                if isinstance(x, dict):
                    y = dict(x)
                    y.setdefault("leg", "onward")
                    normalized.append(y)

        if isinstance(ret, list):
            for x in ret:
                if isinstance(x, dict):
                    y = dict(x)
                    y.setdefault("leg", "return")
                    normalized.append(y)

    # One-way list shape
    elif isinstance(results, list):
        normalized = [x for x in results if isinstance(x, dict)]

    else:
        return []

    # ----------------------------
    # Helpers
    # ----------------------------
    def _iso8601_to_hm(s: Any) -> Optional[str]:
        if not isinstance(s, str) or "T" not in s:
            return None
        # "2026-03-15T05:10:00" -> "05:10"
        try:
            t = s.split("T", 1)[1]
            return t[:5] if len(t) >= 5 else None
        except Exception:
            return None

    def _is_availability_item(o: Dict[str, Any]) -> bool:
        # Prefer explicit type, but also allow heuristic: top-level "segments" list
        t = o.get("type")
        if isinstance(t, str) and "availability" in t.lower():
            return True
        return isinstance(o.get("segments"), list)

    def _avail_segments(o: Dict[str, Any]) -> List[Dict[str, Any]]:
        segs = o.get("segments")
        return [s for s in segs if isinstance(s, dict)] if isinstance(segs, list) else []

    def _avail_route(o: Dict[str, Any]) -> str:
        segs = _avail_segments(o)
        if not segs:
            return "—"
        hops: List[str] = []
        for i, s in enumerate(segs):
            dep = s.get("departure") if isinstance(s.get("departure"), dict) else {}
            arr = s.get("arrival") if isinstance(s.get("arrival"), dict) else {}
            dep_iata = dep.get("iataCode")
            arr_iata = arr.get("iataCode")
            if i == 0 and isinstance(dep_iata, str) and dep_iata.strip():
                hops.append(dep_iata.strip().upper())
            if isinstance(arr_iata, str) and arr_iata.strip():
                hops.append(arr_iata.strip().upper())
        return " -> ".join(hops) if hops else "—"

    def _avail_stops(o: Dict[str, Any]) -> int:
        # UI "stops" == number of connections == segments - 1
        segs = _avail_segments(o)
        return max(0, len(segs) - 1)

    def _avail_carrier_flt(o: Dict[str, Any]) -> str:
        segs = _avail_segments(o)
        parts: List[str] = []

        def _pick_carrier(seg: Dict[str, Any]) -> Optional[str]:
            # Prefer operating carrier if present; fallback to carrierCode.
            op = seg.get("operating")
            if isinstance(op, dict):
                cc = op.get("carrierCode")
                if isinstance(cc, str) and cc.strip():
                    return cc.strip().upper()

            cc = seg.get("carrierCode")
            if isinstance(cc, str) and cc.strip():
                return cc.strip().upper()

            return None

        for s in segs:
            cc = _pick_carrier(s)
            num = s.get("number")
            if cc:
                if isinstance(num, str) and num.strip():
                    parts.append(f"{cc} {num.strip()}")
                else:
                    parts.append(cc)

        if not parts:
            return "—"

        # Convert ["UA 773","UA 2292"] -> "UA 773/2292" (only if same carrier)
        try:
            carrier = parts[0].split()[0]
            nums: List[str] = []
            for p in parts:
                toks = p.split()
                if len(toks) == 2 and toks[0] == carrier:
                    nums.append(toks[1])
                else:
                    raise ValueError("mixed carriers")
            return f"{carrier} " + "/".join(nums) if nums else carrier
        except Exception:
            return " / ".join(parts[:3]) + (" / …" if len(parts) > 3 else "")

    def _avail_time_window(o: Dict[str, Any]) -> Optional[str]:
        segs = _avail_segments(o)
        if not segs:
            return None
        first = segs[0]
        last = segs[-1]
        dep_at = ((first.get("departure") or {}) if isinstance(first.get("departure"), dict) else {}).get("at")
        arr_at = ((last.get("arrival") or {}) if isinstance(last.get("arrival"), dict) else {}).get("at")
        dep_hm = _iso8601_to_hm(dep_at)
        arr_hm = _iso8601_to_hm(arr_at)
        if dep_hm and arr_hm:
            return f"{dep_hm}-{arr_hm}"
        return None

    def _avail_booking_classes(o: Dict[str, Any]) -> List[str]:
        segs = _avail_segments(o)
        classes: List[str] = []
        for s in segs:
            ac = s.get("availabilityClasses")
            if not isinstance(ac, list):
                continue
            for c in ac:
                if not isinstance(c, dict):
                    continue
                k = c.get("class")
                if isinstance(k, str) and k.strip():
                    classes.append(k.strip().upper())
        return sorted(set(classes))

    # ----------------------------
    # Build compact output
    # ----------------------------
    top_flights: List[Dict[str, Any]] = []

    for o in normalized[:top]:
        if _is_availability_item(o):
            item = {
                "kind": "availability",
                "id": o.get("id"),
                "originDestinationId": o.get("originDestinationId"),
                "duration": o.get("duration"),               # keep raw ISO-8601 duration here
                "carrier_flt": _avail_carrier_flt(o),        # "UA 773/2292"
                "stops": _avail_stops(o),
                "time": _avail_time_window(o),              # "06:00-13:49"
                "route": _avail_route(o),                    # "DFW -> DEN -> MCO"
                "booking_classes": _avail_booking_classes(o) # ["B","H","J",...]
            }
            # Preserve leg tag for round-trip, if present
            if isinstance(o.get("leg"), str):
                item["leg"] = o.get("leg")
            top_flights.append(item)
            continue

        # offers shape
        top_flights.append(
            {
                "kind": "offer",
                "offer_id": o.get("offer_id"),
                "total": o.get("total"),
                "currency": o.get("currency"),
                "validating_carrier": o.get("validating_carrier"),
                "cabin": o.get("cabin"),
                "one_way": o.get("one_way"),
                "itineraries": o.get("itineraries"),
            }
        )

    return top_flights

def _compact_result(run_obj: Dict[str, Any], top: int = 5) -> Dict[str, Any]:
    """
    Supports:
      - hotel_only artifacts: results=[...]
      - flight_only artifacts: results=[...]
      - bundle artifacts: results={"flights":[...], "hotels":[...]}
    """
    plan = run_obj.get("plan") or {}
    kind = (plan.get("kind") if isinstance(plan, dict) else None) or run_obj.get("kind") or ""

    provider_warnings = run_obj.get("provider_warnings") or []
    provider_warning_count = run_obj.get("provider_warning_count", len(provider_warnings))

    base: Dict[str, Any] = {
        "run_id": run_obj.get("run_id") or run_obj.get("id") or run_obj.get("stream_run_id"),
        "plan": plan,
        "final_policy": run_obj.get("final_policy"),
        "attempts": run_obj.get("attempts"),
        "provider_warning_count": provider_warning_count,
        "provider_warnings": provider_warnings[:5],
        "flight_search_mode": run_obj.get("flight_search_mode"),
        "note": run_obj.get("note"),
    }

    def _detect_flight_results_kind(raw_results: Any) -> Optional[str]:
        """
        Determine whether flight results look like:
          - availability results (Amadeus flight-availabilities)
          - offer results (priced offers)
        """
        if not isinstance(raw_results, list) or not raw_results:
            return None
        first = raw_results[0]
        if not isinstance(first, dict):
            return None

        t = first.get("type")
        if isinstance(t, str) and "availability" in t.lower():
            return "availability"

        # Availability items usually have top-level segments
        if isinstance(first.get("segments"), list):
            return "availability"

        # Offers usually have top-level itineraries
        if isinstance(first.get("itineraries"), list):
            return "offer"

        return None

    results = run_obj.get("results")

    # ---- flight_only ----
    if kind == "flight_only":
        # If later you store {"onward":[...], "return":[...]}, support it:
        if isinstance(results, dict) and ("onward" in results or "return" in results):
            onward = results.get("onward")
            ret = results.get("return")

            base["flight_results_kind"] = _detect_flight_results_kind(onward) or _detect_flight_results_kind(ret)
            base["flight_results_count"] = (
                (len(onward) if isinstance(onward, list) else 0)
                + (len(ret) if isinstance(ret, list) else 0)
            )
            base["top_flights_onward"] = _compact_flights(onward, top=top)
            base["top_flights_return"] = _compact_flights(ret, top=top)
            return base

        base["flight_results_kind"] = _detect_flight_results_kind(results)
        base["flight_results_count"] = len(results) if isinstance(results, list) else 0
        base["top_flights"] = _compact_flights(results, top=top)
        return base

    # ---- bundle ----
    if kind == "bundle":
        flights = results.get("flights") if isinstance(results, dict) else None
        hotels = results.get("hotels") if isinstance(results, dict) else None

        base["flight_results_kind"] = _detect_flight_results_kind(flights)
        base["flight_results_count"] = len(flights) if isinstance(flights, list) else 0

        base["top_flights"] = _compact_flights(flights, top=top)
        base["top_hotels"] = _compact_hotels(hotels, top=top)
        return base

    # ---- hotel_only (default) ----
    base["top_hotels"] = _compact_hotels(results, top=top)
    return base


# -------------------------
# Routes
# -------------------------

@app.get("/debug/resolver")
def debug_resolver():
    return geo_resolver_stats()

@app.post("/debug/resolver/reset")
def debug_resolver_reset(clear_cache: bool = Query(default=False, description="If true, clears resolver cache too")):
    return geo_resolver_reset_stats(clear_cache=clear_cache)

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _validate_domain_payload(req: SearchRequest) -> None:
    d = (req.domain or "hotel_only").strip().lower()
    if d == "hotel_only":
        if not req.city or not req.check_in or not req.check_out:
            raise HTTPException(status_code=400, detail="hotel_only requires: city, check_in, check_out")
        return
    if d == "flight_only":
        if not req.origin or not req.destination or not req.depart_date:
            raise HTTPException(status_code=400, detail="flight_only requires: origin, destination, depart_date")
        return
    if d == "bundle":
        if not req.origin or not req.destination or not req.depart_date:
            raise HTTPException(status_code=400, detail="bundle requires flight fields: origin, destination, depart_date")
        if not req.city or not req.check_in or not req.check_out:
            raise HTTPException(status_code=400, detail="bundle requires: (city or destination), check_in, check_out")
        return
    raise HTTPException(status_code=400, detail="Invalid domain. Use: hotel_only | flight_only | bundle")

def _build_runner_payload(req: SearchRequest, *, run_id: Optional[str] = None) -> Dict[str, Any]:
    max_stops = req.max_stops
    if req.nonstop:
        max_stops = 0

    payload: Dict[str, Any] = {
        "adults": req.adults,
        "currency": req.currency,
        "domain_country": req.domain_country,

        "max_results": req.max_results,
        "min_results": req.min_results,
        "max_attempts": req.max_attempts,

        "max_offers": req.max_offers,
        "cabin": req.cabin,
        "nonstop_only": req.nonstop,
        "max_stops": max_stops,

        "city": req.city,
        "check_in": req.check_in,
        "check_out": req.check_out,

        "origin": req.origin,
        "destination": req.destination,
        "depart_date": req.depart_date,
        "return_date": req.return_date,
    }

    # -------------------------
    # ✅ Auto-derive metro alts if not provided
    # -------------------------
    if req.domain in ("flight_only", "bundle"):
        if not req.origin_alts and req.origin:
            p, alts = resolve_to_codes(req.origin)
            # keep user's origin as-is, but attach alts if any
            if alts:
                payload["origin_alts"] = alts

        if not req.destination_alts and req.destination:
            p, alts = resolve_to_codes(req.destination)
            if alts:
                payload["destination_alts"] = alts

    # Keep explicit alts highest priority
    if req.origin_alts:
        payload["origin_alts"] = req.origin_alts
    if req.destination_alts:
        payload["destination_alts"] = req.destination_alts

    if run_id:
        payload["run_id"] = run_id
    return payload


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    _validate_domain_payload(req)

    payload = _build_runner_payload(req)

    try:
        out = orchestrate_run(domain=req.domain, payload=payload, runs_dir=str(RUNS_DIR))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"orchestration failed: {e}")

    # Convenience: if we have a run_log_path, also load the full persisted artifact
    run_log_path = out.get("run_log_path")
    if run_log_path:
        p = Path(run_log_path)
        if p.exists():
            out["run_artifact"] = _read_json(p)

    return SearchResponse(result=out)


@app.post("/search/stream")
async def search_stream(req: SearchRequest):
    """
    SSE stream:
      - started
      - attempt (replayed after orchestration completes; deterministic replay)
      - done
      - summary

    Note: This is not “live per attempt” from inside the runner yet.
    If you later want true live streaming, we’ll add an emit hook to runner.
    """
    _validate_domain_payload(req)

    stream_run_id = uuid.uuid4().hex  # stream identity
    payload = _build_runner_payload(req, run_id=stream_run_id)

    async def event_gen():
        started = await publish(stream_run_id, "started", {"status": "running", "domain": req.domain})
        yield sse(
            "started",
            {"run_id": stream_run_id, "status": "running", "domain": req.domain, "seq": started["seq"]},
        )

        try:
            out = await run_in_threadpool(orchestrate_run, req.domain, payload, runs_dir=str(RUNS_DIR))
            out = dict(out)
            out["stream_run_id"] = stream_run_id  # keep stream identity stable in response envelope

            # Replay attempts as SSE events (deterministic, useful for UI)
            attempts = out.get("attempts") or []
            for a in attempts:
                ev = await publish(stream_run_id, "attempt", {"attempt": a})
                yield sse("attempt", {"run_id": stream_run_id, "seq": ev["seq"], "attempt": a})

            done = await publish(stream_run_id, "done", {"status": "done", "run_log_path": out.get("run_log_path")})
            yield sse(
                "done",
                {
                    "run_id": stream_run_id,
                    "status": "done",
                    "seq": done["seq"],
                    "run_log_path": out.get("run_log_path"),
                },
            )

            # Summary (prefer persisted artifact for authoritative summary)
            summary_obj: Dict[str, Any] = {
                "run_id": stream_run_id,
                "plan": out.get("plan"),
                "final_policy": out.get("final_policy"),
                "attempts": out.get("attempts"),
                "run_log_path": out.get("run_log_path"),
                "flight_search_mode": out.get("flight_search_mode"),
                "note": out.get("note"),
            }

            run_log_path = out.get("run_log_path")
            if run_log_path and Path(run_log_path).exists():
                artifact = _read_json(Path(run_log_path))
                summary_obj = _compact_result(artifact, top=req.top)
                summary_obj["run_id"] = stream_run_id  # force stream identity
                summary_obj["run_log_path"] = run_log_path
            else:
                # fallback summary directly from runner output
                if req.domain == "flight_only":
                    summary_obj["top_flights"] = _compact_flights(out.get("results"), top=req.top)
                elif req.domain == "bundle":
                    br = out.get("results") or {}
                    summary_obj["top_flights"] = _compact_flights(br.get("flights"), top=req.top)
                    summary_obj["top_hotels"] = _compact_hotels(br.get("hotels"), top=req.top)
                else:
                    summary_obj["top_hotels"] = _compact_hotels(out.get("results"), top=req.top)

            summ = await publish(stream_run_id, "summary", summary_obj)
            yield sse("summary", {"run_id": stream_run_id, "seq": summ["seq"], **summary_obj})

        except Exception as e:
            err = await publish(stream_run_id, "error", {"detail": str(e)})
            yield sse("error", {"run_id": stream_run_id, "seq": err["seq"], "detail": str(e)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/runs/latest")
def runs_latest() -> Dict[str, Any]:
    p = _latest_run_file()
    return _read_json(p)


@app.get("/runs")
def runs_list(
    city: Optional[str] = Query(default=None, description="Substring match on city/destination_id"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    items = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    # optional filter by substring (best-effort)
    if city:
        needle = city.strip().lower()

        def match(p: Path) -> bool:
            name = p.name.lower()
            if needle in name:
                return True
            try:
                obj = _read_json(p)
                goal = obj.get("goal") if isinstance(obj.get("goal"), dict) else {}
                req_ = obj.get("request") if isinstance(obj.get("request"), dict) else {}
                hay = " ".join(
                    [
                        str(goal.get("city") or ""),
                        str(req_.get("destination_id") or ""),
                        str(obj.get("destination_id") or ""),
                    ]
                ).lower()
                return needle in hay
            except Exception:
                return False

        items = [p for p in items if match(p)]

    total = len(items)
    page = items[offset : offset + limit]

    out_items: List[Dict[str, Any]] = []
    for p in page:
        try:
            obj = _read_json(p)
            out_items.append(
                {
                    "run_id": obj.get("run_id") or obj.get("id") or p.stem,
                    "artifact_path": str(p),
                    "mtime": p.stat().st_mtime,
                    "plan": obj.get("plan"),
                    "final_policy": obj.get("final_policy"),
                    "flight_search_mode": obj.get("flight_search_mode"),
                }
            )
        except Exception:
            out_items.append(
                {"run_id": p.stem, "artifact_path": str(p), "mtime": p.stat().st_mtime, "error": "unreadable"}
            )

    has_more = (offset + len(out_items)) < total
    next_offset = (offset + limit) if has_more else None

    return {
        "items": out_items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": has_more,
        "next_offset": next_offset,
        "city": city,
    }


@app.get("/runs/{run_id}")
def runs_get(run_id: str) -> Dict[str, Any]:
    p = _find_run_file(run_id)
    return _read_json(p)


@app.get("/runs/{run_id}/summary")
def runs_summary(
    run_id: str,
    top: int = Query(default=5, ge=1, le=50),
) -> Dict[str, Any]:
    p = _find_run_file(run_id)
    obj = _read_json(p)
    out = _compact_result(obj, top=top)
    out["artifact_path"] = str(p)
    return out


@app.get("/runs/{run_id}/explain")
def runs_explain(run_id: str) -> Dict[str, Any]:
    p = _find_run_file(run_id)
    obj = _read_json(p)
    try:
        return explain_run(obj)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"explain_run failed: {e}")


@app.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    replay: bool = True,
):
    """
    SSE tail/replay (in-memory only).
    - If replay=true, replays broker buffer (not durable across restarts)
    """
    rid = _safe_run_id(run_id)
    q = await broker.subscribe(rid, replay=replay)

    async def gen():
        try:
            yield sse("connected", {"run_id": rid, "replay": replay})
            while True:
                ev = await q.get()
                yield sse(ev.get("type", "message"), ev)
        finally:
            await broker.unsubscribe(rid, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
