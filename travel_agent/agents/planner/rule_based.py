# travel_agent/agents/planner/rule_based.py
from __future__ import annotations

import time
from typing import Any, Dict, List, Set

from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan, Step


class RuleBasedPlanner:
    """
    Deterministic planner.

    Key rule:
      - For domain=bundle we ALWAYS emit the full pipeline steps on every attempt.
      - Skipping expensive work happens at execution-time (step.skipped), not by removing steps.
    """

    name = "planner.rule_based"

    # ---- REQUIRED BY travel_agent/orchestrator/planner.py ----
    def make_plan(self, ctx: SharedContext) -> Plan:
        try:
            attempt_index = int(getattr(ctx, "attempt", 0) or 0)
        except Exception:
            attempt_index = 0
        return self._make_plan(ctx=ctx, attempt_index=attempt_index)

    # Optional compat if anything calls plan(...)
    def plan(self, *, ctx: SharedContext, attempt_index: int = 0) -> Plan:
        return self._make_plan(ctx=ctx, attempt_index=int(attempt_index))

    # -------------------------
    # Plan factory (robust to Plan contract differences)
    # -------------------------
    @staticmethod
    def _new_plan(*, domain: str, steps: List[Step]) -> Plan:
        # Try common constructor shapes across iterations of the contract.
        for kwargs in (
            {"domain": domain, "steps": steps},
            {"required_domain": domain, "steps": steps},
            {"steps": steps},
        ):
            try:
                plan = Plan(**kwargs)  # type: ignore[arg-type]
                # Best-effort annotate if attributes exist and are writable.
                try:
                    if hasattr(plan, "domain"):
                        setattr(plan, "domain", getattr(plan, "domain") or domain)
                    if hasattr(plan, "required_domain"):
                        setattr(plan, "required_domain", getattr(plan, "required_domain") or domain)
                except Exception:
                    pass
                try:
                    if hasattr(plan, "required_domain"):
                        setattr(plan, "required_domain", getattr(plan, "required_domain") or domain)
                except Exception:
                    pass
                return plan
            except TypeError:
                continue

        # If we get here, your Plan contract is something else entirely.
        raise TypeError("Unable to construct Plan with known argument shapes; check travel_agent/contracts/plan.py")

    # -------------------------
    # Core planner
    # -------------------------
    def _make_plan(self, *, ctx: SharedContext, attempt_index: int) -> Plan:
        domain = str(getattr(getattr(ctx, "intent", None), "domain", None) or "hotel_only")
        if domain == "bundle":
            plan =  self._plan_bundle(ctx=ctx, attempt_index=attempt_index)
        elif domain == "flight_only":
            plan =  self._plan_flight_only(ctx=ctx, attempt_index=attempt_index)
        else:
            plan = self._plan_hotel_only(ctx=ctx, attempt_index=attempt_index)

        # --- sanitize missing depends_on to prevent executor warnings ---
        plan = self._sanitize_unknown_deps(ctx, plan, attempt_index)
        return plan

    # -------------------------
    # Step sanitization
    # -------------------------

    def _sanitize_unknown_deps(self, ctx: SharedContext, plan: Plan, attempt_index: int) -> Plan:
        steps: List[Step] = list(getattr(plan, "steps", []) or [])
        known: Set[str] = set(getattr(s, "id", None) or getattr(s, "step_id", None) or "" for s in steps if s)
        updated: List[Step] = []

        for s in steps:
            sid = getattr(s, "id", getattr(s, "step_id", "")) or ""
            raw = list(getattr(s, "depends_on", None) or [])
            if not raw:
                updated.append(s)
                continue

            kept: List[str] = [d for d in raw if d in known]
            dropped: List[str] = [d for d in raw if d not in known]

            if dropped:
                # emit event to ctx
                try:
                    ctx.events.append(
                        {
                            "ts": time.time(),
                            "kind": "planner.plan_warning",
                            "data": {
                                "attempt_index": int(attempt_index),
                                "step_id": sid,
                                "warning": "depends_on referenced missing step(s); dropping them",
                                "dropped": dropped,
                                "kept": kept,
                                "known_steps": sorted(list(known)),
                            },
                        }
                    )
                except Exception:
                    pass

            # update depends_on safely
            try:
                if hasattr(s, "depends_on"):
                    setattr(s, "depends_on", kept)
            except Exception:
                pass

            updated.append(s)

        # update plan
        try:
            if hasattr(plan, "steps"):
                setattr(plan, "steps", updated)
        except Exception:
            pass
        return plan
    # -------------------------
    # Bundle
    # -------------------------
    def _plan_bundle(self, *, ctx: SharedContext, attempt_index: int) -> Plan:
        intent = ctx.intent
        constraints = getattr(intent, "constraints", None)
        objective = getattr(intent, "objective", None)

        relax = bool(attempt_index > 0)
        max_offers = 5 if attempt_index == 0 else 50

        flight_args: Dict[str, Any] = {
            "origin": intent.origin,
            "destination": intent.destination,
            "origin_alts": list(getattr(intent, "origin_alts", []) or []),
            "destination_alts": list(getattr(intent, "destination_alts", []) or []),
            "depart_date": intent.depart_date,
            "return_date": intent.return_date,
            "adults": getattr(getattr(intent, "passengers", None), "adults", 1) or 1,
            "currency": getattr(intent, "currency", "USD") or "USD",
            "nonstop_only": getattr(constraints, "nonstop", None),
            "max_stops": getattr(constraints, "max_stops", None),
            "cabin": getattr(constraints, "cabin", None),
            "mode": "offers",
            "max_offers": max_offers,
            "relax": relax,
        }

        hotel_args: Dict[str, Any] = {
            "city": intent.city,
            "check_in": intent.check_in,
            "check_out": intent.check_out,
            "adults": getattr(getattr(intent, "passengers", None), "adults", 1) or 1,
            "currency": getattr(intent, "currency", "USD") or "USD",
            "relax": relax,
        }

        bundle_cap = 200
        try:
            scratch = getattr(ctx, "scratch", {}) or {}
            bundle_cap = int(scratch.get("bundle_cap", 200))
        except Exception:
            bundle_cap = 200

        steps: List[Step] = [
            Step(id="flight.search", tool="flight.search", args=flight_args, timeout_s=30, depends_on=[]),
            Step(id="hotel.search", tool="hotel.search", args=hotel_args, timeout_s=30, depends_on=[]),
            Step(
                id="bundle.compose",
                tool="bundle.compose",
                args={"cap": bundle_cap},
                timeout_s=5,
                depends_on=["flight.search", "hotel.search"],
            ),
            Step(
                id="bundle.rank",
                tool="bundle.rank",
                args={"top": 5, "objective": objective or "cheapest"},
                timeout_s=5,
                depends_on=["bundle.compose"],
            ),
            Step(
                id="critic.cost_guard",
                tool="critic.cost_guard",
                args={},
                timeout_s=2,
                depends_on=["flight.search", "hotel.search"],
            ),
            Step(
                id="critic.validate",
                tool="critic.validate",
                args={},
                timeout_s=5,
                depends_on=["bundle.rank"],
            ),
        ]

        plan = self._new_plan(domain="bundle", steps=steps)

        # Optional debug breadcrumb
        try:
            ctx.events.append(
                {
                    "ts": time.time(),
                    "kind": "planner.plan",
                    "data": {
                        "attempt_index": int(attempt_index),
                        "domain": "bundle",
                        "objective": objective,
                        "relax": bool(relax),
                        "step_ids": [s.id for s in steps],
                    },
                }
            )
        except Exception:
            pass

        return plan

    # -------------------------
    # Flight-only
    # -------------------------
    def _plan_flight_only(self, *, ctx: SharedContext, attempt_index: int) -> Plan:
        intent = ctx.intent
        constraints = getattr(intent, "constraints", None)

        relax = bool(attempt_index > 0)
        max_offers = 5 if attempt_index == 0 else 50

        steps: List[Step] = [
            Step(
                id="flight.search",
                tool="flight.search",
                args={
                    "origin": intent.origin,
                    "destination": intent.destination,
                    "origin_alts": list(getattr(intent, "origin_alts", []) or []),
                    "destination_alts": list(getattr(intent, "destination_alts", []) or []),
                    "depart_date": intent.depart_date,
                    "return_date": intent.return_date,
                    "adults": getattr(getattr(intent, "passengers", None), "adults", 1) or 1,
                    "currency": getattr(intent, "currency", "USD") or "USD",
                    "nonstop_only": getattr(constraints, "nonstop", None),
                    "max_stops": getattr(constraints, "max_stops", None),
                    "cabin": getattr(constraints, "cabin", None),
                    "mode": "offers",
                    "max_offers": max_offers,
                    "relax": relax,
                },
                timeout_s=30,
                depends_on=[],
            ),
            Step(id="critic.cost_guard", tool="critic.cost_guard", args={}, timeout_s=2, depends_on=["flight.search"]),
            Step(id="critic.validate", tool="critic.validate", args={}, timeout_s=5, depends_on=["flight.search"]),
        ]
        return self._new_plan(domain="flight_only", steps=steps)

    # -------------------------
    # Hotel-only
    # -------------------------
    def _plan_hotel_only(self, *, ctx: SharedContext, attempt_index: int) -> Plan:
        intent = ctx.intent
        relax = bool(attempt_index > 0)

        steps: List[Step] = [
            Step(
                id="hotel.search",
                tool="hotel.search",
                args={
                    "city": intent.city,
                    "check_in": intent.check_in,
                    "check_out": intent.check_out,
                    "adults": getattr(getattr(intent, "passengers", None), "adults", 1) or 1,
                    "currency": getattr(intent, "currency", "USD") or "USD",
                    "relax": relax,
                },
                timeout_s=30,
                depends_on=[],
            ),
            Step(id="critic.cost_guard", tool="critic.cost_guard", args={}, timeout_s=2, depends_on=["hotel.search"]),
            Step(id="critic.validate", tool="critic.validate", args={}, timeout_s=5, depends_on=["hotel.search"]),
        ]
        return self._new_plan(domain="hotel_only", steps=steps)