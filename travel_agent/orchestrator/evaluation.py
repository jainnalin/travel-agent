# travel_agent/orchestrator/evaluation.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    REPLAN = "REPLAN"
    ABORT = "ABORT"


@dataclass(frozen=True)
class EvaluationResult:
    verdict: Verdict
    reason: str
    confidence: float


def _count_provider_errors_by_tool(ctx) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for w in getattr(ctx, "warnings", []) or []:
        code = getattr(w, "code", None)
        code_s = str(code) if code is not None else ""
        if code_s not in ("PROVIDER_ERROR", "WarningCode.PROVIDER_ERROR"):
            continue
        try:
            tool = (getattr(w, "details", None) or {}).get("tool") or "unknown"
        except Exception:
            tool = "unknown"
        out[tool] = out.get(tool, 0) + 1
    return out


def _dedupe_ratio(raw: int, uniq: int) -> float:
    if raw <= 0:
        return 1.0
    if uniq <= 0:
        return 0.0
    return float(uniq) / float(raw)


def _runtime_counts_by_step(ctx) -> Dict[str, Dict[str, int]]:
    timeouts: Dict[str, int] = {}
    completed_after: Dict[str, int] = {}
    slows: Dict[str, int] = {}

    for ev in getattr(ctx, "events", []) or []:
        try:
            kind = ev.get("kind") if isinstance(ev, dict) else getattr(ev, "kind", None)
            if kind != "step.runtime":
                continue

            d = ev.get("data") if isinstance(ev, dict) else getattr(ev, "data", None)
            d = d or {}
            sid = str(d.get("step_id") or "unknown")

            if bool(d.get("timed_out") is True):
                timeouts[sid] = timeouts.get(sid, 0) + 1
                if bool(d.get("ok") is True):
                    completed_after[sid] = completed_after.get(sid, 0) + 1

            if bool(d.get("slow") is True):
                slows[sid] = slows.get(sid, 0) + 1
        except Exception:
            continue

    return {
        "timeouts_by_step": timeouts,
        "slow_by_step": slows,
        "completed_after_timeout_by_step": completed_after,
    }


def _domain(ctx) -> str:
    try:
        d = getattr(getattr(ctx, "intent", None), "domain", None)
        return str(d or "hotel_only")
    except Exception:
        return "hotel_only"


def _counts(ctx) -> Dict[str, int]:
    return {
        "hotels": int(len(getattr(ctx, "hotels", []) or [])),
        "flights": int(len(getattr(ctx, "flights", []) or [])),
        "bundles": int(len(getattr(ctx, "bundles", []) or [])),
    }


def _required_domain_count(ctx) -> int:
    d = _domain(ctx)
    c = _counts(ctx)
    if d == "bundle":
        return c["bundles"]
    if d == "flight_only":
        return c["flights"]
    return c["hotels"]


def _planner_signals(ctx, attempt: int, min_results: int) -> Dict[str, Any]:
    c = _counts(ctx)

    hotels_raw = int(ctx.scratch.get("hotels_raw_count") or c["hotels"])
    hotels_unique = int(ctx.scratch.get("hotels_unique_count") or hotels_raw)

    total_results = int(c["hotels"] + c["flights"] + c["bundles"])
    required_domain_results = int(_required_domain_count(ctx))

    rt = _runtime_counts_by_step(ctx)

    return {
        "attempt": int(attempt),
        "domain": _domain(ctx),
        "min_results": int(min_results),

        "total_results": int(total_results),
        "required_domain_results": int(required_domain_results),

        "hotels_raw": int(hotels_raw),
        "hotels_unique": int(hotels_unique),
        "dedupe_ratio_hotels": _dedupe_ratio(hotels_raw, hotels_unique),

        "flights_raw": int(c["flights"]),
        "bundles_raw": int(c["bundles"]),
        "provider_errors_by_tool": _count_provider_errors_by_tool(ctx),

        "timeouts_by_step": rt["timeouts_by_step"],
        "slow_by_step": rt["slow_by_step"],
        "completed_after_timeout_by_step": rt["completed_after_timeout_by_step"],

        "fatal": bool(ctx.scratch.get("fatal") is True),
        "fatal_reason": ctx.scratch.get("fatal_reason"),
    }


def _get_prev_timeout_override(ctx, step_id: str) -> Optional[int]:
    knobs = ctx.scratch.get("planner_knobs") or {}
    to = (knobs.get("timeout_overrides") or {}).get(step_id)
    try:
        return int(to) if to is not None else None
    except Exception:
        return None


def _bump_timeout(prev: Optional[int], *, base: int, bump: int, cap: int) -> int:
    x = prev if prev is not None else base
    return int(min(cap, max(base, x + bump)))


def _compute_knobs_for_next_attempt(ctx, signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fix #1 (gap): short-circuit expensive hotel retries when bundle is impossible for now.
      - If domain=bundle AND flights==0 AND we already have hotels, skip hotel.search next attempt.
      - Focus retries on flight search relaxation (mode switch, origin/dest alts, relax=true).
    Executor must honor knobs['skip_steps'] (handled in parallel executor drop-in).
    """
    knobs: Dict[str, Any] = {}

    domain = str(signals.get("domain") or "hotel_only")
    required_domain_results = int(signals.get("required_domain_results") or 0)
    min_results = int(signals.get("min_results") or 5)

    c = _counts(ctx)
    flights = int(c["flights"])
    hotels = int(c["hotels"])

    # If we're missing required-domain results, relax search
    if required_domain_results < min_results:
        knobs["relax"] = True

    # --- Bundle adaptive shortcut ---
    # If bundle can't be formed because flights are missing, don't keep re-running hotel.search.
    if domain == "bundle" and flights <= 0 and hotels > 0:
        knobs["skip_steps"] = ["hotel.search"]
        ctx.scratch["bundle_impossible"] = True
        ctx.scratch["bundle_impossible_reason"] = "missing_flights"

    if domain in ("hotel_only", "bundle"):
        dedupe = float(signals.get("dedupe_ratio_hotels") or 1.0)
        hotels_raw = int(signals.get("hotels_raw") or 0)
        if hotels_raw >= 10 and dedupe < 0.75:
            knobs["hotel_expand_search"] = True
        if min_results > 50:
            knobs["hotel_expand_search"] = True

    if domain in ("flight_only", "bundle"):
        # If we're short on required-domain results, toggle flight mode as you already do
        if required_domain_results < min_results:
            prior = (ctx.scratch.get("planner_knobs") or {}).get("flight_mode") or "offers"
            knobs["flight_mode"] = "availabilities" if prior == "offers" else "offers"

        try:
            base = int(
                (ctx.scratch.get("planner_knobs") or {}).get("max_offers")
                or (ctx.scratch.get("min_results") or 10)
            )
        except Exception:
            base = 10
        knobs["max_offers"] = min(200, max(base, 50))

    # Timeout adaptation based on canonical runtime signals
    timeout_overrides: Dict[str, int] = {}
    timeouts = signals.get("timeouts_by_step") or {}
    completed_after = signals.get("completed_after_timeout_by_step") or {}

    def _want_bump(step_id: str) -> bool:
        return int(timeouts.get(step_id, 0) or 0) > 0 or int(completed_after.get(step_id, 0) or 0) > 0

    for step_id in ("hotel.search", "flight.search"):
        if not _want_bump(step_id):
            continue
        prev = _get_prev_timeout_override(ctx, step_id)
        bump = 20 if int(timeouts.get(step_id, 0) or 0) > 0 else 10
        timeout_overrides[step_id] = _bump_timeout(prev, base=30, bump=bump, cap=90)

    if timeout_overrides:
        knobs["timeout_overrides"] = timeout_overrides

    return knobs


def evaluate(
    ctx,
    *,
    min_results: int,
    target_confidence: float,
    attempt: int,
    max_attempts: int,
) -> EvaluationResult:
    min_required = int(min_results or 0)

    if ctx.scratch.get("fatal") is True:
        ctx.confidence = 0.0
        sig = _planner_signals(ctx, attempt=attempt, min_results=min_required)
        ctx.scratch["planner_signals"] = sig
        ctx.scratch["planner_knobs"] = {}
        return EvaluationResult(
            verdict=Verdict.ABORT,
            reason=str(ctx.scratch.get("fatal_reason") or "fatal error"),
            confidence=0.0,
        )

    sig = _planner_signals(ctx, attempt=attempt, min_results=min_required)
    ctx.scratch["planner_signals"] = sig

    total = int(sig["total_results"])
    required_domain_results = int(sig["required_domain_results"])
    domain = str(sig.get("domain") or "hotel_only")

    # Accept if enough required-domain results
    if required_domain_results >= min_required:
        ctx.confidence = max(float(getattr(ctx, "confidence", 0.0)), min(1.0, max(float(target_confidence), 0.8)))
        ctx.scratch["planner_knobs"] = {}
        return EvaluationResult(
            verdict=Verdict.ACCEPT,
            reason=f"{required_domain_results} required-domain results (>= {min_required}) domain={domain}",
            confidence=float(ctx.confidence),
        )

    last_attempt = int(attempt) >= int(max_attempts - 1)

    # bundle last attempt:
    # - if still no bundles but we have hotels, accept hotels-only best effort
    if last_attempt and domain == "bundle" and required_domain_results <= 0:
        c = _counts(ctx)
        if c["hotels"] > 0:
            ctx.scratch["decision"] = "PARTIAL_BUNDLE_HOTELS_ONLY"
            ctx.confidence = max(float(getattr(ctx, "confidence", 0.0)), 0.4)
            ctx.scratch["planner_knobs"] = {}
            return EvaluationResult(
                verdict=Verdict.ACCEPT,
                reason=(
                    "bundle unavailable; returning hotels-only best effort "
                    f"(required_domain_results=0; min_required={min_required}; total={total})"
                ),
                confidence=float(ctx.confidence),
            )

        ctx.confidence = 0.0
        ctx.scratch["planner_knobs"] = {}
        return EvaluationResult(
            verdict=Verdict.ABORT,
            reason=(
                f"no required-domain results after {max_attempts} attempts "
                f"(required_domain_results={required_domain_results}; min_required={min_required}; total={total})"
            ),
            confidence=float(ctx.confidence),
        )

    # non-bundle last attempt => accept best effort
    if last_attempt:
        ctx.confidence = max(float(getattr(ctx, "confidence", 0.0)), 0.4)
        ctx.scratch["planner_knobs"] = {}
        return EvaluationResult(
            verdict=Verdict.ACCEPT,
            reason=(
                "max attempts reached; returning best effort "
                f"(required_domain_results={required_domain_results}; min_required={min_required}; total={total})"
            ),
            confidence=float(ctx.confidence),
        )

    # Track “no improvement” for hotels (optional early stop)
    hotels_unique = int(sig.get("hotels_unique") or 0)
    hotels_raw = int(sig.get("hotels_raw") or 0)

    prev_unique = ctx.scratch.get("_prev_hotels_unique")
    prev_raw = ctx.scratch.get("_prev_hotels_raw")
    try:
        prev_unique_i = int(prev_unique) if prev_unique is not None else None
        prev_raw_i = int(prev_raw) if prev_raw is not None else None
    except Exception:
        prev_unique_i = None
        prev_raw_i = None

    if prev_unique_i is not None and prev_raw_i is not None:
        if hotels_raw > prev_raw_i and hotels_unique <= prev_unique_i:
            ctx.confidence = max(float(getattr(ctx, "confidence", 0.0)), 0.4)
            ctx.scratch["planner_knobs"] = {}
            return EvaluationResult(
                verdict=Verdict.ACCEPT,
                reason="no improvement in unique hotel results; returning best effort",
                confidence=float(ctx.confidence),
            )

    ctx.scratch["_prev_hotels_unique"] = int(hotels_unique)
    ctx.scratch["_prev_hotels_raw"] = int(hotels_raw)

    ctx.scratch["planner_knobs"] = _compute_knobs_for_next_attempt(ctx, sig)

    ctx.confidence = max(float(getattr(ctx, "confidence", 0.0)), 0.4)
    return EvaluationResult(
        verdict=Verdict.REPLAN,
        reason=f"only {required_domain_results} required-domain results (<{min_required}); retrying",
        confidence=float(ctx.confidence),
    )