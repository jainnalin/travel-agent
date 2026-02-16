# travel_agent/orchestrator/loop.py
from __future__ import annotations

import inspect
import time
from typing import Any, Dict, Tuple

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan
from travel_agent.orchestrator.policies import LoopPolicy, ExecutorPolicy
from travel_agent.orchestrator.registry import Registry

from travel_agent.agents.support.geo_agent import GeoAgent
from travel_agent.agents.support.enrich_agent import EnrichAgent
from travel_agent.agents.planner.llm_based import LLMPlannerAgent

def _filter_kwargs_for_callable(fn, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sig = inspect.signature(fn)
        allowed = set(sig.parameters.keys())
        return {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    except Exception:
        return {}


def _normalize_verdict(v: Any) -> str:
    """
    Accepts:
      - enum-ish objects with .value (preferred)
      - strings like "Verdict.REPLAN" / "VERDICT.REPLAN" / "REPLAN"
    Returns: "REPLAN" | "ACCEPT" | "ABORT" (or "" if unknown)
    """
    if v is None:
        return ""

    # enum style
    try:
        vv = getattr(v, "value", None)
        if isinstance(vv, str) and vv.strip():
            return vv.strip().upper()
    except Exception:
        pass

    s = str(v).strip()
    s = s.replace("Verdict.", "").replace("VERDICT.", "")
    s = s.strip().upper()
    return s


def _emit_verdict(
    ctx: SharedContext,
    *,
    verdict: Any,
    reason: str,
    confidence: float,
    attempt_index: int,
) -> None:
    """
    Persist verdict in BOTH:
      - ctx.scratch (for status mapping)
      - ctx.events (for /events ndjson stream)
    """
    v = _normalize_verdict(verdict) or "ABORT"

    payload = {
        "attempt": int(attempt_index) + 1,     # human-friendly
        "attempt_index": int(attempt_index),   # 0-based
        "verdict": v,                          # ✅ normalized
        "reason": str(reason or ""),
        "confidence": float(confidence),
    }

    # canonical scratch fields for API
    ctx.scratch["final_verdict"] = v
    ctx.scratch["final_reason"] = payload["reason"]
    ctx.scratch["final_confidence"] = float(confidence)

    # event stream (dict event)
    ctx.events.append({"ts": time.time(), "kind": "verdict", "data": payload})


def _make_executor(policy: LoopPolicy, ctx: SharedContext) -> Tuple[Any, ExecutorPolicy]:
    from travel_agent.orchestrator.exec.parallel import ParallelExecutor

    domain = getattr(ctx.intent, "domain", "hotel_only")
    ep: ExecutorPolicy = policy.executor_for(str(domain))

    merged_limits = ep.merged_resource_limits(scratch=(ctx.scratch or {}))

    ctor_kwargs = {
        "max_workers": ep.max_workers,
        "default_resource_limit": ep.default_resource_limit,
        "resource_limits": merged_limits,
        "max_provider_calls": ep.max_provider_calls or getattr(policy, "max_provider_calls", None),
        # older executors may accept different kwargs; we filter below
        "registry": None,
        "bus": None,
        "resource_policy": None,
    }

    filtered = _filter_kwargs_for_callable(ParallelExecutor, ctor_kwargs)
    return ParallelExecutor(**filtered), ep  # type: ignore[arg-type]


def _run_executor(
    executor: Any,
    *,
    ctx: SharedContext,
    registry: Registry,
    plan: Plan,
    ep: ExecutorPolicy,
    attempt_index: int,
) -> None:
    """
    Try common executor methods; always include attempt_index when accepted.
    """
    candidates = [
        "run_plan",
        "execute_plan",
        "execute_steps",
        "run_steps",
        "execute",
        "run",
        "__call__",
    ]

    def try_call(fn) -> bool:
        kwargs = {
            "ctx": ctx,
            "registry": registry,
            "plan": plan,
            "steps": getattr(plan, "steps", None),
            "attempt_index": int(attempt_index),
            "max_provider_calls": ep.max_provider_calls,
            "resource_limits": ep.merged_resource_limits(scratch=(ctx.scratch or {})),
        }
        filtered = _filter_kwargs_for_callable(fn, kwargs)
        if not filtered:
            return False
        fn(**filtered)
        return True

    for name in candidates:
        fn = getattr(executor, name, None)
        if callable(fn) and try_call(fn):
            return

    for name, fn in inspect.getmembers(executor, predicate=callable):
        if name.startswith("_"):
            continue
        if name in ("__class__",):
            continue
        if try_call(fn):
            return

    raise RuntimeError(
        f"Unknown executor interface: {type(executor)}. "
        f"Could not find a callable that accepts (ctx, registry, plan/steps, attempt_index)."
    )


def _evaluate(ctx: SharedContext, plan: Plan, policy: LoopPolicy, attempt_index: int):
    import travel_agent.orchestrator.evaluation as evmod

    fn = getattr(evmod, "evaluate", None)
    if not callable(fn):
        fn = getattr(evmod, "evaluate_attempt", None)
    if not callable(fn):
        raise ImportError(
            "No evaluation function found in travel_agent.orchestrator.evaluation. "
            "Expected: evaluate (preferred) or evaluate_attempt."
        )

    max_attempts = int(getattr(ctx, "max_attempts", None) or getattr(policy, "max_attempts", 1) or 1)

    kwargs = {
        "ctx": ctx,
        "plan": plan,
        "policy": policy,
        "attempt_index": int(attempt_index),
        "min_results": int(getattr(policy, "min_results", 0) or 0),
        "target_confidence": float(getattr(policy, "target_confidence", 0.0) or 0.0),
        "attempt": int(attempt_index),
        "max_attempts": int(max_attempts),
    }
    filtered = _filter_kwargs_for_callable(fn, kwargs)
    return fn(**filtered)


def _verdict_value(obj: Any) -> Any:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return obj.get("verdict") or ""
    return getattr(obj, "verdict", "") or ""


def _reason_value(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return str(obj.get("reason") or "")
    return str(getattr(obj, "reason", "") or "")


def _confidence_value(obj: Any, default: float) -> float:
    if obj is None:
        return float(default)
    try:
        if isinstance(obj, dict):
            v = obj.get("confidence")
            return float(v) if v is not None else float(default)
        v = getattr(obj, "confidence", None)
        return float(v) if v is not None else float(default)
    except Exception:
        return float(default)


def run_loop(*, ctx: SharedContext, registry: Registry, policy: LoopPolicy) -> None:
    if getattr(ctx, "events", None) is None:
        ctx.events = []
    if getattr(ctx, "warnings", None) is None:
        ctx.warnings = []
    if getattr(ctx, "scratch", None) is None:
        ctx.scratch = {}
    if getattr(ctx, "confidence", None) is None:
        ctx.confidence = 0.0

    max_attempts = int(getattr(policy, "max_attempts", 1) or 1)
    ctx.max_attempts = max_attempts

    # default final state (overwritten after first evaluation)
    ctx.scratch.setdefault("final_verdict", "REPLAN")
    ctx.scratch.setdefault("final_reason", "")
    ctx.scratch.setdefault("final_confidence", float(getattr(ctx, "confidence", 0.0)))

    for attempt_index in range(max_attempts):
        ctx.attempt = attempt_index

        planner_agent = LLMPlannerAgent()
        plan = planner_agent.run(ctx)

        executor, ep = _make_executor(policy, ctx)
        _run_executor(
            executor,
            ctx=ctx,
            registry=registry,
            plan=plan,
            ep=ep,
            attempt_index=attempt_index,
        )

        # ---------------------------------------------------------
        # Phase 6 Intelligence Pipeline
        # ---------------------------------------------------------

        # 1️⃣ Geo enrichment (mutates attributes only)
        geo_agent = GeoAgent()
        geo_agent.run(ctx)

        # 2️⃣ Context enrichment (mutates attributes only)
        enrich_agent = EnrichAgent()
        enrich_agent.run(ctx)

        # ---------------------------------------------------------
        # Evaluation
        # ---------------------------------------------------------

        evaluation = _evaluate(ctx, plan, policy, attempt_index=attempt_index)

        v_raw = _verdict_value(evaluation)
        v = _normalize_verdict(v_raw)  # ✅ normalize here
        r = _reason_value(evaluation)
        c = _confidence_value(evaluation, float(getattr(ctx, "confidence", 0.0)))

        # always persist + emit verdict
        _emit_verdict(ctx, verdict=v, reason=r, confidence=c, attempt_index=attempt_index)

        # stop conditions
        if v == "ACCEPT":
            ctx.confidence = float(c)
            return
        if v == "ABORT":
            ctx.confidence = float(c)
            return

        # REPLAN: keep looping; preserve latest confidence
        ctx.confidence = float(c)

    # if loop exits without terminal verdict, mark ABORT defensively
    _emit_verdict(
        ctx,
        verdict="ABORT",
        reason=f"max attempts exhausted ({max_attempts}) without terminal verdict",
        confidence=float(getattr(ctx, "confidence", 0.0)),
        attempt_index=max_attempts - 1 if max_attempts > 0 else 0,
    )
    return