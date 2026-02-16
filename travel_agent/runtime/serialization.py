#travel-agent/travel_agent/runtime/serialization.py

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.intent import UserIntent
from travel_agent.contracts.warnings import Warning, WarningCode


def _to_jsonable(o: Any) -> Any:
    if o is None:
        return None
    if isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, Enum):
        return o.value
    if is_dataclass(o):
        return {k: _to_jsonable(v) for k, v in asdict(o).items()}
    if isinstance(o, dict):
        return {str(k): _to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_jsonable(x) for x in o]
    return str(o)


def _construct_shared_context(**kwargs) -> SharedContext:
    """
    Create SharedContext defensively: only pass kwargs that SharedContext accepts.
    This avoids TypeErrors when the dataclass fields differ across phases.
    """
    sig = inspect.signature(SharedContext)
    allowed = set(sig.parameters.keys())
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return SharedContext(**filtered)  # type: ignore[arg-type]


def write_ctx(path: str, ctx: SharedContext) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {
        "run_id": getattr(ctx, "run_id", None),
        "attempt": int(getattr(ctx, "attempt", 0) or 0),
        "max_attempts": int(getattr(ctx, "max_attempts", 0) or 0),
        "max_provider_calls": int(getattr(ctx, "max_provider_calls", 0) or 0),
        "time_budget_ms": getattr(ctx, "time_budget_ms", None),
        "confidence": float(getattr(ctx, "confidence", 0.0) or 0.0),
        "intent": _to_jsonable(getattr(ctx, "intent", None)),
        "scratch": _to_jsonable(getattr(ctx, "scratch", {}) or {}),
        "hotels": _to_jsonable(getattr(ctx, "hotels", []) or []),
        "flights": _to_jsonable(getattr(ctx, "flights", []) or []),
        "bundles": _to_jsonable(getattr(ctx, "bundles", []) or []),
        "ranked": _to_jsonable(getattr(ctx, "ranked", None)),
        "warnings": _to_jsonable(getattr(ctx, "warnings", []) or []),
        # ✅ Persist events so /events can stream from disk
        "events": _to_jsonable(getattr(ctx, "events", []) or []),
        "decision": _to_jsonable(getattr(ctx, "decision", None)),
    }

    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_ctx(path: str) -> SharedContext:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    intent_d = raw.get("intent") or {}
    if isinstance(intent_d, dict):
        intent = UserIntent(**intent_d)
    else:
        intent = UserIntent(domain="hotel_only")

    # Construct ctx defensively (fields differ across phases)
    ctx = _construct_shared_context(
        run_id=str(raw.get("run_id") or ""),
        intent=intent,
        attempt=int(raw.get("attempt") or 0),
        max_attempts=int(raw.get("max_attempts") or 0),
        max_provider_calls=int(raw.get("max_provider_calls") or 0),
        time_budget_ms=raw.get("time_budget_ms"),
    )

    # Populate mutable runtime fields (setattr-safe even if not in __init__)
    try:
        ctx.confidence = float(raw.get("confidence") or 0.0)
    except Exception:
        pass

    try:
        ctx.scratch = raw.get("scratch") or {}
    except Exception:
        pass

    # warnings
    ws = raw.get("warnings") or []
    out_w: list[Warning] = []
    if isinstance(ws, list):
        for w in ws:
            if not isinstance(w, dict):
                continue
            code = w.get("code")
            try:
                code_e = WarningCode(code) if code else WarningCode.PROVIDER_ERROR
            except Exception:
                code_e = WarningCode.PROVIDER_ERROR
            out_w.append(
                Warning(
                    code=code_e,
                    title=str(w.get("title") or ""),
                    details=w.get("details") or {},
                    step_id=w.get("step_id"),
                    agent=w.get("agent"),
                )
            )
    try:
        ctx.warnings = out_w
    except Exception:
        pass

    # events (persisted)
    evs = raw.get("events") or []
    try:
        ctx.events = evs if isinstance(evs, list) else []
    except Exception:
        pass

    # results (best-effort; your API mostly needs counts + replay/debug)
    for k in ("hotels", "flights", "bundles", "ranked", "decision"):
        if k in raw:
            try:
                setattr(ctx, k, raw.get(k))
            except Exception:
                pass

    return ctx
