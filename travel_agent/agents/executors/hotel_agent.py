# travel_agent/agents/executors/hotel_agent.py
from __future__ import annotations

from datetime import timedelta, datetime
import os

from travel_agent.agents.base import AgentBase
from travel_agent.agents.executors.retry import (
    RetryConfig,
    call_with_retries,
    is_retryable_message,
)
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.events import (
    EVENT_TOOL_CALLED,
    EVENT_TOOL_RESULT,
    make_event,
)
from travel_agent.contracts.plan import Step
from travel_agent.contracts.result import HotelResult, Money
from travel_agent.contracts.warnings import Warning, WarningCode


class HotelSearchAgent(AgentBase):
    name = "agent.hotel_search"

    def handles(self):
        return ["hotel.search", "search_hotels"]

    # -------------------------------------------------
    # Retry handling
    # -------------------------------------------------

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        if isinstance(err, ValueError):
            return False
        return is_retryable_message(str(err))

    # -------------------------------------------------
    # Provider arg preparation
    # -------------------------------------------------

    @staticmethod
    def _provider_args(step_args: dict) -> dict:
        args = dict(step_args or {})
        args.pop("expand_search", None)
        return args

    # -------------------------------------------------
    # Utility conversion helpers
    # -------------------------------------------------

    @staticmethod
    def _money_from(v, currency: str) -> Money | None:
        if v is None:
            return None
        if isinstance(v, Money):
            return v
        if isinstance(v, dict):
            amt = v.get("amount")
            cur = v.get("currency") or currency
            try:
                if amt is None:
                    return None
                return Money(float(amt), str(cur))
            except Exception:
                return None
        try:
            return Money(float(v), currency)
        except Exception:
            return None

    @staticmethod
    def _to_int(v) -> int | None:
        try:
            if v is None or isinstance(v, bool):
                return None
            return int(round(float(v)))
        except Exception:
            return None

    @staticmethod
    def _to_float(v) -> float | None:
        try:
            if v is None or isinstance(v, bool):
                return None
            return float(v)
        except Exception:
            return None

    # -------------------------------------------------
    # MAIN RUN
    # -------------------------------------------------

    def run(self, ctx: SharedContext, step: Step) -> None:

        # ---- Short-circuit if bundle impossible ----
        try:
            domain = str(getattr(getattr(ctx, "intent", None), "domain", "") or "")
        except Exception:
            domain = ""

        if domain == "flight_only":
            ctx.events.append(
                make_event(
                    "step.skipped",
                    agent=self.name,
                    step_id=step.id,
                    reason="domain=flight_only; skipping hotel search",
                )
            )
            return

        if domain == "bundle" and bool((ctx.scratch or {}).get("bundle_impossible")):
            ctx.events.append(
                make_event(
                    "step.skipped",
                    agent=self.name,
                    step_id=step.id,
                    reason="bundle_impossible=true; skipping hotel search",
                )
            )
            return

        # ---- Load provider tool ----
        from travel_agent.tools.providers.amadeus import hotels as amadeus_hotels

        if hasattr(amadeus_hotels, "search_hotels"):
            fn = amadeus_hotels.search_hotels
            fn_name = "search_hotels"
        elif hasattr(amadeus_hotels, "search"):
            fn = amadeus_hotels.search
            fn_name = "search"
        else:
            raise RuntimeError("Amadeus hotels tool missing search function")

        tool_name = f"amadeus.hotels.{fn_name}"

        attempts = max(1, int(os.getenv("AMADEUS_HTTP_RETRIES", "1")))
        cfg = RetryConfig(attempts=attempts)

        intent = ctx.intent

        # -------------------------------------------------
        # Determine city
        # -------------------------------------------------

        if intent.domain == "bundle":
            city = intent.destination
        else:
            city = intent.city or intent.destination

        if not city:
            raise RuntimeError("Cannot run hotel search: no city in intent")

        # -------------------------------------------------
        # Determine dates from intent
        # -------------------------------------------------

        check_in = getattr(intent, "check_in", None) or getattr(intent, "depart_date", None)
        check_out = getattr(intent, "check_out", None)

        if check_in and not check_out and getattr(intent, "trip_days", None):
            check_out = check_in + timedelta(days=int(intent.trip_days))

        # -------------------------------------------------
        # Prepare provider args
        # -------------------------------------------------

        provider_args = self._provider_args(step.args)

        # -------- Planner → Provider normalization --------

        # Legacy field names
        if "checkin_date" in provider_args and "check_in" not in provider_args:
            provider_args["check_in"] = provider_args.pop("checkin_date")

        if "checkout_date" in provider_args and "check_out" not in provider_args:
            provider_args["check_out"] = provider_args.pop("checkout_date")

        # NEW: planner emits "date"
        if "date" in provider_args and "check_in" not in provider_args:
            provider_args["check_in"] = provider_args.pop("date")

        # Convert "days"
        if (
            "days" in provider_args
            and provider_args.get("check_in")
            and not provider_args.get("check_out")
        ):
            try:
                ci = provider_args["check_in"]
                if isinstance(ci, str):
                    ci = datetime.fromisoformat(ci)
                provider_args["check_out"] = ci + timedelta(days=int(provider_args.pop("days")))
            except Exception:
                provider_args.pop("days", None)

        # NEW: planner emits "duration"
        if (
            "duration" in provider_args
            and provider_args.get("check_in")
            and not provider_args.get("check_out")
        ):
            try:
                ci = provider_args["check_in"]
                if isinstance(ci, str):
                    ci = datetime.fromisoformat(ci)
                provider_args["check_out"] = ci + timedelta(days=int(provider_args.pop("duration")))
            except Exception:
                provider_args.pop("duration", None)

        # -------- Final enforcement from intent --------
        # Intent dates always override planner/LLM args (LLM may emit wrong year e.g. 2023)
        provider_args.setdefault("city", city)
        if check_in is not None:
            provider_args["check_in"] = check_in
        if check_out is not None:
            provider_args["check_out"] = check_out

        # -------- CRITICAL: ISO date enforcement --------

        if isinstance(provider_args.get("check_in"), datetime):
            provider_args["check_in"] = provider_args["check_in"].date().isoformat()

        if isinstance(provider_args.get("check_out"), datetime):
            provider_args["check_out"] = provider_args["check_out"].date().isoformat()

        # Final safety check
        if not provider_args.get("check_in") or not provider_args.get("check_out"):
            raise RuntimeError("Hotel search missing check-in/check-out dates")

        # -------------------------------------------------
        # Provider call
        # -------------------------------------------------

        try:
            attempt_counter = {"n": 0}

            def _call_once(**kwargs):
                attempt_counter["n"] += 1
                ctx.inc_provider_call("amadeus", 1)
                ctx.events.append(
                    make_event(
                        EVENT_TOOL_CALLED,
                        tool=tool_name,
                        args={**kwargs, "_attempt": attempt_counter["n"]},
                    )
                )
                return fn(**kwargs)

            raw_items = call_with_retries(
                _call_once,
                args=provider_args,
                cfg=cfg,
                is_retryable=self._is_retryable,
                on_retry=on_retry,
            )

        except TypeError as e:
            ctx.add_warning(
                Warning(
                    code=WarningCode.PROVIDER_ERROR,
                    title="Planner/tool arg mismatch (hotel.search)",
                    details={"error": str(e), "tool": tool_name, "args": provider_args},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            ctx.confidence = min(ctx.confidence, 0.2)
            return

        except Exception as e:
            ctx.add_warning(
                Warning(
                    code=WarningCode.PROVIDER_ERROR,
                    title="Hotel provider error",
                    details={"error": str(e), "tool": tool_name, "args": provider_args},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            ctx.confidence = min(ctx.confidence, 0.2)
            return

        # -------------------------------------------------
        # Process results
        # -------------------------------------------------

        try:
            count = len(raw_items or [])
        except Exception:
            count = -1

        ctx.events.append(make_event(EVENT_TOOL_RESULT, tool=tool_name, count=count))

        if not raw_items:
            ctx.add_warning(
                Warning(
                    code=WarningCode.NO_RESULTS,
                    title="No hotels found",
                    details={"tool": tool_name, "args": provider_args},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            return

        results: list[HotelResult] = []

        currency = getattr(ctx.intent, "currency", "USD") or "USD"
        for r in raw_items:
            if not isinstance(r, dict):
                continue

            # Amadeus normalized uses price_total/price_per_night; support both names
            total_raw = r.get("total_price") or r.get("price_total")
            nightly_raw = r.get("nightly_price") or r.get("price_per_night")
            cur = r.get("currency") or currency
            total_price = self._money_from(total_raw, cur)
            if total_price is None and isinstance(total_raw, (int, float)):
                total_price = Money(float(total_raw), str(cur))
            nightly_price = self._money_from(nightly_raw, cur)
            if nightly_price is None and isinstance(nightly_raw, (int, float)):
                nightly_price = Money(float(nightly_raw), str(cur))

            # Amadeus uses board_type, payment_type
            board = r.get("board") or r.get("board_type")
            pay_type = r.get("pay_type") or r.get("payment_type")

            # Area: Amadeus hotel may have address.district or address.lines
            area = r.get("area")
            if area is None and isinstance(r.get("raw"), dict):
                hotel = (r.get("raw") or {}).get("hotel") or {}
                addr = hotel.get("address") if isinstance(hotel.get("address"), dict) else {}
        
        ctx.hotels.extend(results)