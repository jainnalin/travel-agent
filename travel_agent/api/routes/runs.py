# travel_agent/api/routes/runs.py
from __future__ import annotations

import inspect
import json
import os
import time
from typing import Any, Dict, Generator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.intent import UserIntent
from travel_agent.nlp.rule_parser import parse_text_to_intent
from travel_agent.orchestrator.loop import run_loop
from travel_agent.orchestrator.policies import default_loop_policy
from travel_agent.orchestrator.wiring import make_registry
from travel_agent.runtime.run_id import new_run_id
from travel_agent.runtime.serialization import read_ctx
from travel_agent.runtime.store.file_store import FileRunStore

router = APIRouter()

_RUNS_DIR_DEFAULT = "runs"


def _run_path(runs_dir: str, run_id: str) -> str:
    return os.path.join(runs_dir, f"{run_id}.json")


def _ndjson_line(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _construct_shared_context(**kwargs) -> SharedContext:
    sig = inspect.signature(SharedContext)
    allowed = set(sig.parameters.keys())
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return SharedContext(**filtered)  # type: ignore[arg-type]


def _scratch(ctx) -> Dict[str, Any]:
    s = getattr(ctx, "scratch", None)
    return s if isinstance(s, dict) else {}


def _final_verdict(ctx) -> str:
    s = _scratch(ctx)
    v = s.get("final_verdict")
    if v:
        return str(v).upper()

    last = None
    for ev in (getattr(ctx, "events", []) or []):
        try:
            if isinstance(ev, dict) and ev.get("kind") == "verdict":
                last = ev
        except Exception:
            continue

    if last is not None:
        data = last.get("data") if isinstance(last, dict) else None
        if isinstance(data, dict) and data.get("verdict"):
            return str(data["verdict"]).upper()

    # conservative
    return "ABORT"


def _status_from_ctx(ctx) -> str:
    return "completed" if _final_verdict(ctx) == "ACCEPT" else "failed"


def _attempts_from_ctx(ctx) -> int:
    try:
        return int(getattr(ctx, "attempt", 0) or 0) + 1
    except Exception:
        return 1


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.post("/runs")
async def create_run(req: Request):
    try:
        body = await req.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")

    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text'")

    runs_dir = str(body.get("runs_dir") or _RUNS_DIR_DEFAULT)

    intent: UserIntent = parse_text_to_intent(text)
    policy = default_loop_policy()

    ctx = _construct_shared_context(
        run_id=new_run_id(prefix="travel"),
        intent=intent,
        attempt=0,
        max_attempts=policy.max_attempts,
        max_provider_calls=policy.max_provider_calls,
        time_budget_ms=policy.time_budget_ms,
    )

    # Ensure required runtime attributes exist
    if getattr(ctx, "scratch", None) is None:
        ctx.scratch = {}
    if getattr(ctx, "events", None) is None:
        ctx.events = []
    if getattr(ctx, "warnings", None) is None:
        ctx.warnings = []
    if getattr(ctx, "confidence", None) is None:
        ctx.confidence = 0.0

    # planner/agents knobs
    ctx.scratch["min_results"] = policy.min_results
    ctx.scratch["target_confidence"] = policy.target_confidence
    ctx.scratch["max_attempts"] = policy.max_attempts
    ctx.scratch["max_provider_calls"] = policy.max_provider_calls
    if policy.time_budget_ms is not None:
        ctx.scratch["time_budget_ms"] = policy.time_budget_ms

    registry = make_registry()
    store = FileRunStore(runs_dir=runs_dir)

    try:
        run_loop(ctx=ctx, registry=registry, policy=policy)
        store.save(ctx)
        return JSONResponse({
            "run_id": ctx.run_id,
            "summary": {
                "attempts": _attempts_from_ctx(ctx),
                "confidence": float(getattr(ctx, "confidence", 0.0) or 0.0),
                "counts": {
                    "hotels": len(getattr(ctx, "hotels", []) or []),
                    "flights": len(getattr(ctx, "flights", []) or []),
                    "bundles": len(getattr(ctx, "bundles", []) or []),
                }
            }
        })
    except Exception as e:
        # ✅ ALWAYS return run_id so shell pipelines never KeyError again
        try:
            ctx.scratch["fatal"] = True
            ctx.scratch["fatal_reason"] = str(e)
            ctx.scratch["final_verdict"] = "ABORT"
            ctx.scratch["final_reason"] = f"run failed: {e}"
        except Exception:
            pass

        try:
            store.save(ctx)
        except Exception:
            pass

        return JSONResponse(
            {"run_id": getattr(ctx, "run_id", None), "error": str(e), "status": "failed"},
            status_code=500,
        )


@router.get("/runs/{run_id}")
def get_run(run_id: str, runs_dir: str = _RUNS_DIR_DEFAULT):
    path = _run_path(runs_dir, run_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="run_id not found")

    ctx = read_ctx(path)

    return {
        "run_id": ctx.run_id,
        "status": _status_from_ctx(ctx),
        "attempts": _attempts_from_ctx(ctx),
        "confidence": float(getattr(ctx, "confidence", 0.0) or 0.0),
        "counts": {
            "hotels": len(getattr(ctx, "hotels", []) or []),
            "flights": len(getattr(ctx, "flights", []) or []),
            "bundles": len(getattr(ctx, "bundles", []) or []),
        },
        "warnings_count": len(getattr(ctx, "warnings", []) or []),
        "decision": getattr(ctx, "decision", None),
        "final_verdict": _final_verdict(ctx),
        "final_reason": _scratch(ctx).get("final_reason"),
    }


@router.get("/runs/{run_id}/events")
def stream_events(
    run_id: str,
    format: str = "ndjson",
    since: int = 0,
    follow: bool = False,
    follow_seconds: int = 0,
    runs_dir: str = _RUNS_DIR_DEFAULT,
    stop_on_verdict: bool = False,
):
    if format != "ndjson":
        raise HTTPException(status_code=400, detail="Only format=ndjson is supported")

    path = _run_path(runs_dir, run_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="run_id not found")

    start = max(0, int(since or 0))

    def _is_verdict_event(ev: Any) -> bool:
        try:
            return isinstance(ev, dict) and ev.get("kind") == "verdict"
        except Exception:
            return False

    def gen() -> Generator[bytes, None, None]:
        idx = start
        start_ts = time.time()

        yield _ndjson_line({"ts": time.time(), "kind": "stream.open", "data": {"run_id": run_id, "since": idx, "follow": bool(follow)}})

        emitted_any = False

        while True:
            ctx = read_ctx(path)
            evs = getattr(ctx, "events", []) or []

            while idx < len(evs):
                ev = evs[idx]
                yield _ndjson_line(ev)
                emitted_any = True
                idx += 1
                if stop_on_verdict and _is_verdict_event(ev):
                    return

            if not follow:
                if not emitted_any:
                    yield _ndjson_line({"ts": time.time(), "kind": "stream.heartbeat", "data": {"note": "no events"}})
                break

            if follow_seconds and follow_seconds > 0 and (time.time() - start_ts) >= float(follow_seconds):
                yield _ndjson_line({"ts": time.time(), "kind": "stream.heartbeat", "data": {"note": "follow_seconds elapsed"}})
                break

            if not emitted_any:
                yield _ndjson_line({"ts": time.time(), "kind": "stream.heartbeat", "data": {"note": "waiting"}})

            time.sleep(0.25)

    return StreamingResponse(gen(), media_type="application/x-ndjson")