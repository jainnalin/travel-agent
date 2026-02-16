# travel_agent/orchestrator/exec/dag.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

from travel_agent.contracts.plan import Plan, Step


@dataclass
class Dag:
    """
    Simple DAG structure derived from Plan.steps.

    NOTE:
      - Some code paths may use Step.id, others Step.step_id (or Step.name).
      - Dependencies may reference either form.
      - This DAG builder normalizes everything to a canonical step id.
    """
    steps_by_id: Dict[str, Step]
    deps: Dict[str, Set[str]]          # step_id -> dependencies (canonical ids)
    rdeps: Dict[str, Set[str]]         # step_id -> reverse deps
    indegree: Dict[str, int]           # step_id -> # unmet dependencies
    order: Dict[str, int]              # step_id -> plan order index


def _canonical_step_id(step: Step) -> str:
    """
    Pick a stable canonical id for a step.
    Prefer: id -> step_id -> name
    """
    for attr in ("id", "step_id", "name"):
        try:
            v = getattr(step, attr, None)
            if v:
                s = str(v).strip()
                if s:
                    return s
        except Exception:
            continue
    return ""


def _candidate_ids(step: Step) -> List[str]:
    """
    Collect all ids this step may be known by (aliases).
    """
    out: List[str] = []
    for attr in ("id", "step_id", "name"):
        try:
            v = getattr(step, attr, None)
            if v:
                s = str(v).strip()
                if s:
                    out.append(s)
        except Exception:
            continue
    # de-dupe while preserving order
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq


def build_dag(plan: Plan) -> Dag:
    steps = list(getattr(plan, "steps", []) or [])

    # Canonical ids + alias map
    canonical_ids: List[str] = []
    alias_to_canonical: Dict[str, str] = {}

    for s in steps:
        cid = _canonical_step_id(s)
        if not cid:
            raise ValueError("Plan contains a Step with no usable id/step_id/name")
        canonical_ids.append(cid)

        # Map all candidate ids -> canonical
        for alias in _candidate_ids(s):
            alias_to_canonical[alias] = cid

    # Ensure canonical ids are unique
    if len(set(canonical_ids)) != len(canonical_ids):
        # Find duplicates for a helpful error
        seen: Set[str] = set()
        dups: List[str] = []
        for cid in canonical_ids:
            if cid in seen and cid not in dups:
                dups.append(cid)
            seen.add(cid)
        raise ValueError(f"Plan has duplicate step identifiers after normalization: {dups}")

    steps_by_id: Dict[str, Step] = {alias_to_canonical[_canonical_step_id(s)]: s for s in steps}
    order: Dict[str, int] = {alias_to_canonical[_canonical_step_id(s)]: i for i, s in enumerate(steps)}

    deps: Dict[str, Set[str]] = {}
    rdeps: Dict[str, Set[str]] = {}
    indegree: Dict[str, int] = {}

    for s in steps:
        sid = alias_to_canonical[_canonical_step_id(s)]
        raw_deps = set(getattr(s, "depends_on", None) or [])

        normalized: Set[str] = set()
        for dep in raw_deps:
            dep_s = str(dep).strip()
            if not dep_s:
                continue
            canon = alias_to_canonical.get(dep_s)
            if not canon:
                # Helpful debug: show what we *do* have
                known = sorted(steps_by_id.keys())
                raise ValueError(
                    f"Step {sid!r} depends on unknown step {dep_s!r}. "
                    f"Known steps={known}"
                )
            normalized.add(canon)

        deps[sid] = normalized
        indegree[sid] = len(normalized)
        rdeps.setdefault(sid, set())

    for sid, dset in deps.items():
        for dep in dset:
            rdeps.setdefault(dep, set()).add(sid)

    return Dag(
        steps_by_id=steps_by_id,
        deps=deps,
        rdeps=rdeps,
        indegree=indegree,
        order=order,
    )


def initial_ready(dag: Dag) -> List[str]:
    """
    Steps with indegree 0 (no dependencies), in plan order.
    """
    ready = [sid for sid, deg in dag.indegree.items() if deg == 0]
    ready.sort(key=lambda sid: dag.order.get(sid, 10**9))
    return ready


def mark_done(dag: Dag, done_step_id: str) -> List[str]:
    """
    Decrement indegrees of reverse dependencies; return newly-ready step ids (in plan order).
    """
    newly_ready: List[str] = []
    for child in dag.rdeps.get(done_step_id, set()):
        dag.indegree[child] -= 1
        if dag.indegree[child] == 0:
            newly_ready.append(child)

    newly_ready.sort(key=lambda sid: dag.order.get(sid, 10**9))
    return newly_ready


def detect_cycle_or_missing(dag: Dag, completed: Iterable[str]) -> None:
    """
    If we finish scheduling and some nodes never reached indegree 0,
    it’s a cycle (or an unreachable dependency graph).
    """
    completed_set = set(completed)
    if len(completed_set) != len(dag.steps_by_id):
        remaining = sorted([sid for sid in dag.steps_by_id.keys() if sid not in completed_set])
        raise RuntimeError(f"DAG did not complete all steps; cycle suspected. Remaining={remaining}")