# travel_agent/orchestrator/exec/parallel.py
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace, is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan, Step
from travel_agent.orchestrator.registry import Registry
from travel_agent.orchestrator.exec.dag import build_dag, initial_ready, mark_done, detect_cycle_or_missing


class ParallelExecutor:
    """
    Parallel DAG executor with safety fixes:

    - Robust DAG build: sanitize unknown depends_on refs instead of crashing.
    - Bundle ordering: enforce flight.search before hotel.search, and both before post_search steps.
    - Instrumentation: emits executor.phase + step.skipped + step.runtime consistently.
    - Registry resolution: defensive agent resolution (supports multiple method names).

    NOTE:
      If the planner produces an inconsistent Plan (e.g. depends_on references a missing step),
      we remove the missing dep and emit a warning event (kind="planner.plan_warning").
    """

    def __init__(
        self,
        max_workers: int = 6,
        default_resource_limit: Optional[int] = None,
        resource_limits: Optional[Dict[str, int]] = None,
        max_provider_calls: Optional[int] = None,
        **_ignored: Any,
    ):
        self.max_workers = int(max_workers or 6)
        self.default_resource_limit = default_resource_limit
        self.resource_limits = resource_limits or {}
        self.max_provider_calls = max_provider_calls

        # ✅ NEW: preserve original plan step universe
        self._full_plan_step_ids: Set[str] = set()

    def run_plan(self, *, ctx: SharedContext, registry: Registry, plan: Plan, attempt_index: int = 0, **_: Any) -> None:
        self.execute_plan(ctx=ctx, registry=registry, plan=plan, attempt_index=attempt_index)

    def execute_plan(self, *, ctx: SharedContext, registry: Registry, plan: Plan, attempt_index: int = 0, **_: Any) -> None:
        # ✅ NEW: capture full original plan step IDs before any mutation
        self._full_plan_step_ids = {
            self._step_id(s)
            for s in (getattr(plan, "steps", []) or [])
            if self._step_id(s)
        }
        self._execute_plan_dag(ctx=ctx, registry=registry, plan=plan, attempt_index=int(attempt_index))

    # -----------------------------
    # Helpers
    # -----------------------------

    def _sanitize_unknown_deps(self, ctx: SharedContext, plan: Plan, attempt_index: int) -> Plan:
        """
        Remove depends_on references to missing steps so DAG build can't crash.
        Emit a planner.plan_warning event when we do it.
        """
        steps: List[Step] = list(getattr(plan, "steps", []) or [])

        # ✅ FIX: use full original plan universe if available
        if self._full_plan_step_ids:
            known: Set[str] = set(self._full_plan_step_ids)
        else:
            known: Set[str] = set(self._step_id(s) for s in steps if self._step_id(s))

        updated: List[Step] = []
        changed_any = False

        for s in steps:
            sid = self._step_id(s)
            raw = list(getattr(s, "depends_on", None) or [])
            if not raw:
                updated.append(s)
                continue

            kept: List[str] = []
            dropped: List[str] = []
            for dep in raw:
                d = str(dep)
                if d in known:
                    kept.append(d)
                else:
                    dropped.append(d)

            if dropped:
                changed_any = True
                ctx.events.append(
                    {
                        "ts": time.time(),
                        "kind": "planner.plan_warning",
                        "data": {
                            "attempt_index": int(attempt_index),
                            "step_id": sid,
                            "warning": "depends_on referenced missing step(s); dropping them to allow execution",
                            "dropped": dropped,
                            "kept": kept,
                            "known_steps": sorted(list(known)),
                        },
                    }
                )
                s2 = self._set_depends_on(s, kept)
                updated.append(s2)
            else:
                updated.append(s)

        if not changed_any:
            return plan

        try:
            if is_dataclass(plan):
                return replace(plan, steps=updated)
        except Exception:
            pass

        try:
            setattr(plan, "steps", updated)
        except Exception:
            pass
        return plan

    def _skip_steps(self, ctx: SharedContext) -> List[str]:
        try:
            knobs = (getattr(ctx, "scratch", None) or {}).get("planner_knobs") or {}
            ss = knobs.get("skip_steps") or []
            return [str(x) for x in ss if x]
        except Exception:
            return []

    @staticmethod
    def _step_id(step: Step) -> str:
        for attr in ("id", "step_id", "name"):
            try:
                v = getattr(step, attr, None)
                if v:
                    return str(v)
            except Exception:
                continue
        return ""

    @staticmethod
    def _step_tool(step: Step) -> str:
        try:
            v = getattr(step, "tool", None)
            return str(v) if v else ""
        except Exception:
            return ""

    @staticmethod
    def _step_timeout(step: Step) -> Optional[int]:
        try:
            v = getattr(step, "timeout_s", None)
            return int(v) if v is not None else None
        except Exception:
            return None

    @staticmethod
    def _domain(ctx: SharedContext) -> str:
        try:
            return str(getattr(getattr(ctx, "intent", None), "domain", None) or "hotel_only")
        except Exception:
            return "hotel_only"

    @staticmethod
    def _is_post_search_step(step_id: str) -> bool:
        return step_id in ("bundle.compose", "bundle.rank", "critic.validate", "critic.cost_guard")

    def _emit_step_skipped(
        self,
        ctx: SharedContext,
        *,
        step_id: str,
        attempt_index: int,
        reason: str,
        tool: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> None:
        ctx.events.append(
            {
                "ts": time.time(),
                "kind": "step.skipped",
                "data": {"attempt_index": int(attempt_index), "step_id": str(step_id), "reason": str(reason or "")},
            }
        )
        ctx.events.append(
            {
                "ts": time.time(),
                "kind": "step.runtime",
                "data": {
                    "attempt_index": int(attempt_index),
                    "step_id": str(step_id),
                    "tool": str(tool or step_id),
                    "timeout_s": timeout_s,
                    "elapsed_s": 0.0,
                    "timed_out": False,
                    "ok": True,
                    "error": None,
                    "skipped": True,
                },
            }
        )

    def _resolve_agent(self, registry: Registry, step_id: str):
        for name in ("get_agent_for_step", "get_agent", "agent_for", "resolve", "get"):
            fn = getattr(registry, name, None)
            if callable(fn):
                try:
                    a = fn(step_id)
                    if a is not None:
                        return a
                except Exception:
                    pass

        agents = getattr(registry, "agents", None)
        if isinstance(agents, dict):
            a = agents.get(step_id)
            if a is not None:
                return a

        raise RuntimeError(f"Registry cannot resolve agent for step_id={step_id!r}")

    # -----------------------------
    # Plan sanitization / enforcement
    # -----------------------------

    def _set_depends_on(self, step: Step, new_deps: List[str]) -> Step:
        """
        Update depends_on safely:
          - If Step is mutable: setattr and return original
          - If Step is dataclass: replace(step, depends_on=...)
          - Else: best-effort setattr
        """
        try:
            if hasattr(step, "depends_on"):
                setattr(step, "depends_on", list(new_deps))
                return step
        except Exception:
            pass

        try:
            if is_dataclass(step):
                return replace(step, depends_on=list(new_deps))
        except Exception:
            pass

        # fallback: return original (can’t safely mutate/copy)
        return step

    def _sanitize_unknown_deps(self, ctx: SharedContext, plan: Plan, attempt_index: int) -> Plan:
        """
        Remove depends_on references to missing steps so DAG build can't crash.
        Emit a planner.plan_warning event when we do it.
        """
        steps: List[Step] = list(getattr(plan, "steps", []) or [])
        known: Set[str] = set(self._step_id(s) for s in steps if self._step_id(s))

        updated: List[Step] = []
        changed_any = False

        for s in steps:
            sid = self._step_id(s)
            raw = list(getattr(s, "depends_on", None) or [])
            if not raw:
                updated.append(s)
                continue

            kept: List[str] = []
            dropped: List[str] = []
            for dep in raw:
                d = str(dep)
                if d in known:
                    kept.append(d)
                else:
                    dropped.append(d)

            if dropped:
                changed_any = True
                ctx.events.append(
                    {
                        "ts": time.time(),
                        "kind": "planner.plan_warning",
                        "data": {
                            "attempt_index": int(attempt_index),
                            "step_id": sid,
                            "warning": "depends_on referenced missing step(s); dropping them to allow execution",
                            "dropped": dropped,
                            "kept": kept,
                            "known_steps": sorted(list(known)),
                        },
                    }
                )
                s2 = self._set_depends_on(s, kept)
                updated.append(s2)
            else:
                updated.append(s)

        if not changed_any:
            return plan

        # Rebuild plan with updated steps if possible
        try:
            if is_dataclass(plan):
                return replace(plan, steps=updated)
        except Exception:
            pass

        try:
            setattr(plan, "steps", updated)
        except Exception:
            pass
        return plan

    def _enforce_bundle_phase_edges(self, ctx: SharedContext, plan: Plan, attempt_index: int) -> Plan:
        """
        Ensure bundle phase ordering even if planner forgot:
          - hotel.search depends on flight.search (if both exist)
          - post_search steps depend on both flight.search and hotel.search (if present)
        """
        steps: List[Step] = list(getattr(plan, "steps", []) or [])
        by_id: Dict[str, Step] = {self._step_id(s): s for s in steps if self._step_id(s)}

        if "flight.search" not in by_id:
            return plan  # nothing to enforce
        flight_id = "flight.search"

        # If hotel exists, enforce hotel depends on flight
        if "hotel.search" in by_id:
            hotel = by_id["hotel.search"]
            deps = list(getattr(hotel, "depends_on", None) or [])
            if flight_id not in deps:
                deps.append(flight_id)
                by_id["hotel.search"] = self._set_depends_on(hotel, deps)

        # Post-search steps depend on available search steps
        prereqs: List[str] = ["flight.search"]
        if "hotel.search" in by_id:
            prereqs.append("hotel.search")

        for pid in list(by_id.keys()):
            if self._is_post_search_step(pid):
                st = by_id[pid]
                deps = list(getattr(st, "depends_on", None) or [])
                changed = False
                for p in prereqs:
                    if p in by_id and p not in deps:
                        deps.append(p)
                        changed = True
                if changed:
                    by_id[pid] = self._set_depends_on(st, deps)

        # Reassemble steps preserving original order
        new_steps: List[Step] = []
        for s in steps:
            sid = self._step_id(s)
            new_steps.append(by_id.get(sid, s))

        try:
            if is_dataclass(plan):
                return replace(plan, steps=new_steps)
        except Exception:
            pass

        try:
            setattr(plan, "steps", new_steps)
        except Exception:
            pass

        return plan

    # -----------------------------
    # Step execution
    # -----------------------------

    def _run_one_step(self, ctx: SharedContext, registry: Registry, step: Step, attempt_index: int) -> None:
        step_id = self._step_id(step)
        step_tool = self._step_tool(step) or step_id
        timeout_s = self._step_timeout(step)

        t0 = time.time()
        ok = True
        err: Optional[str] = None

        try:
            agent = self._resolve_agent(registry, step_tool)
            agent.run(ctx, step)
        except Exception as e:
            ok = False
            err = str(e)

        elapsed = time.time() - t0
        ctx.events.append(
            {
                "ts": time.time(),
                "kind": "step.runtime",
                "data": {
                    "attempt_index": int(attempt_index),
                    "step_id": str(step_id),
                    "tool": str(step_tool),
                    "timeout_s": timeout_s,
                    "elapsed_s": float(elapsed),
                    "timed_out": False,
                    "ok": bool(ok),
                    "error": err,
                },
            }
        )

        if not ok:
            raise RuntimeError(f"step {step_id} failed: {err}")

    # -----------------------------
    # DAG scheduler
    # -----------------------------

    def _execute_plan_dag(self, *, ctx: SharedContext, registry: Registry, plan: Plan, attempt_index: int) -> None:
        # 0) Optional skips
        skip = set(self._skip_steps(ctx))

        # 1) Make plan safe + enforce bundle ordering
        domain = self._domain(ctx)
        if domain == "bundle":
            plan = self._enforce_bundle_phase_edges(ctx, plan, attempt_index)

        plan = self._sanitize_unknown_deps(ctx, plan, attempt_index)

        # 2) Build DAG
        dag = build_dag(plan)

        # 3) Ready queue
        ready = initial_ready(dag)
        ctx.events.append({"ts": time.time(), "kind": "executor.phase", "data": {"attempt_index": int(attempt_index), "phase": "dag.ready", "count": len(ready)}})

        completed: List[str] = []
        in_flight = {}

        def _label_for(step_id: str) -> str:
            if step_id == "flight.search":
                return "search.flight"
            if step_id == "hotel.search":
                return "search.hotel"
            if self._is_post_search_step(step_id):
                return "post_search"
            return "other"

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            # Submit all ready up to workers; then as they complete submit newly-ready
            while ready or in_flight:
                # fill workers
                while ready and len(in_flight) < self.max_workers:
                    sid = ready.pop(0)
                    step = dag.steps_by_id[sid]

                    if sid in skip:
                        self._emit_step_skipped(
                            ctx,
                            step_id=sid,
                            attempt_index=attempt_index,
                            reason="skipped by planner_knobs.skip_steps",
                            tool=self._step_tool(step),
                            timeout_s=self._step_timeout(step),
                        )
                        completed.append(sid)
                        ready.extend(mark_done(dag, sid))
                        continue

                    ctx.events.append(
                        {"ts": time.time(), "kind": "executor.phase", "data": {"attempt_index": int(attempt_index), "phase": _label_for(sid), "count": 1, "step_id": sid}}
                    )
                    fut = pool.submit(self._run_one_step, ctx, registry, step, int(attempt_index))
                    in_flight[fut] = sid

                # wait for at least 1 completion
                for fut in as_completed(list(in_flight.keys()), timeout=None):
                    sid = in_flight.pop(fut)
                    # propagate exceptions
                    fut.result()
                    completed.append(sid)
                    newly = mark_done(dag, sid)
                    ready.extend(newly)
                    # maintain plan order-ish by using dag.order
                    ready.sort(key=lambda x: dag.order.get(x, 10**9))
                    break

        detect_cycle_or_missing(dag, completed)