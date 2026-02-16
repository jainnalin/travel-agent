# travel_agent/cli/commands/run.py
from __future__ import annotations

import argparse
from dataclasses import replace

from travel_agent.contracts.intent import PassengerInfo, UserIntent, Constraints
from travel_agent.contracts.context import SharedContext
from travel_agent.nlp.rule_parser import parse_text_to_intent
from travel_agent.nlp.normalize.bundle import normalize_bundle_intent
from travel_agent.orchestrator.policies import default_loop_policy
from travel_agent.orchestrator.loop import run_loop
from travel_agent.orchestrator.wiring import make_registry
from travel_agent.runtime.store.file_store import FileRunStore
from travel_agent.runtime.run_id import new_run_id


def _build_intent_from_args(args: argparse.Namespace) -> UserIntent:
    passengers = PassengerInfo(
        adults=int(getattr(args, "adults", 1) or 1),
        children=int(getattr(args, "children", 0) or 0),
        infants=int(getattr(args, "infants", 0) or 0),
    )

    constraints = Constraints(
        nonstop=getattr(args, "nonstop", None),
        max_stops=getattr(args, "max_stops", None),
        budget_usd=getattr(args, "budget_usd", None),
        cabin=getattr(args, "cabin", None),
        hotel_stars_min=getattr(args, "hotel_stars_min", None),
        hotel_area=getattr(args, "hotel_area", None),
        notes=[],
    )

    raw_text = (getattr(args, "text", None) or getattr(args, "raw_text", "") or "")

    return UserIntent(
        domain=args.domain,
        origin=getattr(args, "origin", None),
        destination=getattr(args, "destination", None),
        city=getattr(args, "city", None),
        depart_date=getattr(args, "depart_date", None),
        return_date=getattr(args, "return_date", None),
        check_in=getattr(args, "check_in", None),
        check_out=getattr(args, "check_out", None),
        passengers=passengers,
        currency=(getattr(args, "currency", None) or "USD"),
        constraints=constraints,
        raw_text=raw_text,
        confidence=1.0,
        meta={},
    )


def _clamp_int(v, default: int, *, min_v: int = 1) -> int:
    try:
        if v is None:
            return int(default)
        return max(int(min_v), int(v))
    except Exception:
        return int(default)


def _default_allow_mode_switch(intent: UserIntent) -> bool:
    """
    Policy default:
      - If strict flight constraints exist (nonstop/cabin/max_stops), do NOT mode-switch by default
        because availabilities payloads are more likely to differ and can trigger parse mismatch.
      - Otherwise, allow mode switch as a fallback.
    """
    c = getattr(intent, "constraints", None)
    if not c:
        return True

    strict = any(
        [
            c.nonstop is True,
            bool(c.cabin),
            c.max_stops is not None,
        ]
    )
    return not strict


def _build_intent(args: argparse.Namespace) -> UserIntent:
    """
    Single NLP entrypoint for CLI.

    Rules:
      - If --text is provided, parse via NLP into a UserIntent.
      - If parsed intent is bundle, normalize bundle exactly once here.
      - Otherwise, fall back to building intent from structured CLI args.
    """
    txt = (getattr(args, "text", None) or "").strip()
    if txt:
        intent = parse_text_to_intent(txt)
        # Normalize bundle here (and only here) to avoid duplicates
        if intent.domain == "bundle":
            intent = normalize_bundle_intent(intent)
        return intent
    return _build_intent_from_args(args)


def cmd_run(args: argparse.Namespace) -> int:
    # 1) Intent (single entrypoint)
    intent = _build_intent(args)

    # 2) Policy: start from central defaults, then override with CLI
    base = default_loop_policy()

    max_attempts = _clamp_int(getattr(args, "max_attempts", None), base.max_attempts, min_v=1)
    min_results = _clamp_int(getattr(args, "min_results", None), base.min_results, min_v=0)

    try:
        target_confidence = float(getattr(args, "target_confidence", base.target_confidence))
    except Exception:
        target_confidence = float(base.target_confidence)

    max_provider_calls = _clamp_int(getattr(args, "max_provider_calls", None), base.max_provider_calls, min_v=1)
    time_budget_ms = getattr(args, "time_budget_ms", base.time_budget_ms)

    policy = replace(
        base,
        max_attempts=max_attempts,
        min_results=min_results,
        target_confidence=target_confidence,
        max_provider_calls=max_provider_calls,
        time_budget_ms=time_budget_ms,
    )

    # 3) Context
    ctx = SharedContext(
        run_id=new_run_id(),
        intent=intent,
        attempt=0,
        max_attempts=policy.max_attempts,
        max_provider_calls=policy.max_provider_calls,
        time_budget_ms=policy.time_budget_ms,
    )

    # 4) Scratch knobs (agents/planner read these)
    ctx.scratch["min_results"] = policy.min_results
    ctx.scratch["target_confidence"] = policy.target_confidence
    ctx.scratch["max_attempts"] = policy.max_attempts
    ctx.scratch["max_provider_calls"] = policy.max_provider_calls

    # ---- Planner knobs live under ctx.scratch["planner_knobs"] ----
    ctx.scratch.setdefault("planner_knobs", {})
    planner_knobs = ctx.scratch["planner_knobs"]

    # default policy (smart default based on strict constraints)
    allow_mode_switch = _default_allow_mode_switch(intent)

    # CLI override if user specified it
    if getattr(args, "allow_mode_switch", None) is True:
        allow_mode_switch = True
    if getattr(args, "no_allow_mode_switch", None) is True:
        allow_mode_switch = False

    planner_knobs["allow_mode_switch"] = bool(allow_mode_switch)

    # Phase 5 Cost Guard budgets (optional; safe defaults if unset)
    # NOTE: CostGuardAgent reads these keys.
    if getattr(args, "provider_call_budget_soft", None) is not None:
        ctx.scratch["provider_call_budget_soft"] = int(getattr(args, "provider_call_budget_soft"))
    if getattr(args, "provider_call_budget_hard", None) is not None:
        ctx.scratch["provider_call_budget_hard"] = int(getattr(args, "provider_call_budget_hard"))
    if getattr(args, "provider_call_budget_provider", None) is not None:
        ctx.scratch["provider_call_budget_provider"] = str(getattr(args, "provider_call_budget_provider"))

    # bundle knobs
    ctx.scratch["bundle_cap"] = _clamp_int(getattr(args, "bundle_cap", 200), 200, min_v=1)

    ap = getattr(args, "amadeus_parallelism", None)
    if ap is not None:
        try:
            ctx.scratch["amadeus_parallelism"] = max(1, int(ap))
        except Exception:
            pass

    if policy.time_budget_ms is not None:
        ctx.scratch["time_budget_ms"] = policy.time_budget_ms

    # 5) Registry
    registry = make_registry()

    # 6) Store
    store = FileRunStore(runs_dir=getattr(args, "runs_dir", "runs"))

    # 7) Run
    run_loop(ctx=ctx, registry=registry, policy=policy)

    # 8) Persist & print
    path = store.save(ctx)
    print(f"\nRun saved: {path}")
    print(f"Attempts: {ctx.attempt + 1}  Confidence: {ctx.confidence:.2f}")
    print(f"Hotels: {len(ctx.hotels)}  Flights: {len(ctx.flights)}  Bundles: {len(ctx.bundles)}")

    if getattr(ctx, "ranked", None):
        top = ctx.ranked[0]
        print(f"Top ranked: type={top.item_type} index={top.item_index} score={top.score:.2f}")

    return 0


def add_run_subparser(subparsers) -> None:
    p = subparsers.add_parser("run", help="Run the multi-agent loop")
    p.set_defaults(func=cmd_run)

    # NLP entrypoint
    p.add_argument("--text", dest="text", default=None, help="Natural language query (overrides structured args)")

    p.add_argument("--domain", required=True, choices=["hotel_only", "flight_only", "bundle"])
    p.add_argument("--currency", default="USD")

    # hotel args
    p.add_argument("--city")
    p.add_argument("--check-in", dest="check_in")
    p.add_argument("--check-out", dest="check_out")

    # flight args
    p.add_argument("--origin")
    p.add_argument("--destination")
    p.add_argument("--depart-date", dest="depart_date")
    p.add_argument("--return-date", dest="return_date")

    # passengers
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--children", type=int, default=0)
    p.add_argument("--infants", type=int, default=0)

    # policy knobs
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=3)
    p.add_argument("--min-results", dest="min_results", type=int, default=5)
    p.add_argument("--target-confidence", dest="target_confidence", type=float, default=0.8)
    p.add_argument("--max-provider-calls", dest="max_provider_calls", type=int, default=200)
    p.add_argument("--time-budget-ms", dest="time_budget_ms", type=int, default=None)

    # runtime
    p.add_argument("--runs-dir", dest="runs_dir", default="runs")

    # bundle
    p.add_argument("--bundle-cap", dest="bundle_cap", type=int, default=200)

    # resource throttling override (policies.py default for bundle is 2; this can override)
    p.add_argument("--amadeus-parallelism", dest="amadeus_parallelism", type=int, default=None)

    # flight fallback knobs
    p.add_argument("--allow-mode-switch", dest="allow_mode_switch", action="store_true", default=None)
    p.add_argument("--no-allow-mode-switch", dest="no_allow_mode_switch", action="store_true", default=None)

    # Phase 5 Cost Guard knobs
    p.add_argument("--provider-call-budget-soft", dest="provider_call_budget_soft", type=int, default=None)
    p.add_argument("--provider-call-budget-hard", dest="provider_call_budget_hard", type=int, default=None)
    p.add_argument("--provider-call-budget-provider", dest="provider_call_budget_provider", type=str, default=None)
