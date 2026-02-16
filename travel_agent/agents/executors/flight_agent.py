# travel_agent/agents/executors/flight_agent.py
from __future__ import annotations
from types import SimpleNamespace

import os
from typing import Any, Dict, List, Optional, Tuple

from travel_agent.agents.base import AgentBase
from travel_agent.agents.executors.retry import RetryConfig, call_with_retries, is_retryable_message
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.events import EVENT_TOOL_CALLED, EVENT_TOOL_RESULT, make_event
from travel_agent.contracts.plan import Step
from travel_agent.contracts.result import FlightResult, Money
from travel_agent.contracts.warnings import Warning, WarningCode
from travel_agent.tools.providers.amadeus import flights as amadeus_flights
from travel_agent.tools.geo.resolver import resolve_to_codes


class FlightSearchAgent(AgentBase):
    name = "agent.flight_search"

    def handles(self):
        return ["flight.search", "search_flights"]

    @staticmethod
    def _provider_args(ctx: SharedContext, step: Step) -> dict:
        """Merge intent + step args, resolve city->IATA, format dates for Amadeus."""
        intent = getattr(ctx, "intent", None)
        step_args = dict(step.args or {})
        args = {}

        def _date_str(v) -> Optional[str]:
            if v is None:
                return None
            if isinstance(v, str) and len(v) >= 10:
                return v[:10]
            if hasattr(v, "date"):
                return v.date().isoformat()
            if hasattr(v, "strftime"):
                return v.strftime("%Y-%m-%d")
            return str(v)[:10] if v else None

        origin_raw = step_args.get("origin") or (getattr(intent, "origin", None) if intent else None)
        dest_raw = step_args.get("destination") or (getattr(intent, "destination", None) if intent else None)

        if origin_raw:
            primary, alts = resolve_to_codes(str(origin_raw))
            args["origin"] = primary or str(origin_raw).strip().upper()[:3]
            if alts:
                args["origin_alts"] = alts
        if dest_raw:
            primary, alts = resolve_to_codes(str(dest_raw))
            args["destination"] = primary or str(dest_raw).strip().upper()[:3]
            if alts:
                args["destination_alts"] = alts

        args["depart_date"] = (
            step_args.get("depart_date")
            or (getattr(intent, "depart_date", None) if intent else None)
        )
        args["depart_date"] = _date_str(args.get("depart_date"))

        args["return_date"] = (
            step_args.get("return_date")
            or (getattr(intent, "return_date", None) if intent else None)
        )
        args["return_date"] = _date_str(args.get("return_date"))

        args["adults"] = int(
            step_args.get("adults")
            or (getattr(getattr(intent, "passengers", None), "adults", 1) if intent else 1)
            or 1
        )
        args["currency"] = step_args.get("currency") or (getattr(intent, "currency", "USD") if intent else "USD")
        args["cabin"] = step_args.get("cabin")
        args["nonstop_only"] = step_args.get("nonstop_only")
        args["max_stops"] = step_args.get("max_stops")
        args["mode"] = step_args.get("mode", "offers")
        args["max_offers"] = int(step_args.get("max_offers", 50))
        args["relax"] = bool(step_args.get("relax", False))

        return {k: v for k, v in args.items() if v is not None}

    @staticmethod
    def _is_fatal_provider_input_error(err: Exception) -> bool:
        msg = str(err) or ""
        up = msg.upper()
        if "STATUS\":400" in msg or "STATUS=400" in up:
            return True
        if "INVALID FORMAT" in up:
            return True
        if "MUST BE A 3-LETTER" in up:
            return True
        if '"code":477' in msg or "CODE\":477" in msg or " CODE 477" in up:
            return True
        return False

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        if isinstance(err, ValueError):
            return False
        if FlightSearchAgent._is_fatal_provider_input_error(err):
            return False
        return is_retryable_message(str(err))

    @staticmethod
    def _as_list(v) -> List[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if x]
        if isinstance(v, str):
            s = v.strip()
            return [s] if s else []
        return [str(v)]

    @staticmethod
    def _extract_count(raw: Any) -> int:
        try:
            if isinstance(raw, dict) and isinstance(raw.get("data"), list):
                return len(raw["data"])
        except Exception:
            pass
        return 0

    @staticmethod
    def _should_allow_mode_switch(*, step_args: Dict[str, Any], planner_knobs: Dict[str, Any]) -> bool:
        """
        Gate switching from offers <-> availabilities.

        Why: availabilities payload shape differs; until we implement a parser for it,
        mode_switch can create raw_count>0 but parsed_count=0 -> "payload parse mismatch".

        Default: DISABLED unless explicitly enabled via planner_knobs.allow_mode_switch == True.
        Additionally, if enabled, we only allow mode_switch on "simple" searches.
        """
        # explicit knob
        allow = False
        try:
            if "allow_mode_switch" in (planner_knobs or {}):
                allow = bool(planner_knobs.get("allow_mode_switch") is True)
        except Exception:
            allow = False
        if not allow:
            return False

        # Heuristic: only allow when no strong filters are present.
        # (You can relax this later once availabilities parsing is implemented.)
        nonstop_only = step_args.get("nonstop_only")
        cabin = step_args.get("cabin")
        max_stops = step_args.get("max_stops")

        # If user asked for nonstop/first/etc, don't mode-switch (likely incompatible / brittle).
        if nonstop_only is True:
            return False
        if cabin not in (None, "", "ECONOMY"):
            return False
        if max_stops not in (None, ""):
            return False

        return True

    def _parse_results(
        self,
        ctx: SharedContext,
        step: Step,
        raw: Dict[str, Any],
        *,
        resolved_origin: Optional[str] = None,
        resolved_dest: Optional[str] = None,
    ) -> List[FlightResult]:
        results: List[FlightResult] = []
        data = raw.get("data")
        if not isinstance(data, list):
            return results

        origin = resolved_origin or (step.args or {}).get("origin")
        destination = resolved_dest or (step.args or {}).get("destination")

        for item in data:
            if not isinstance(item, dict):
                continue

            price = item.get("price") if isinstance(item.get("price"), dict) else {}
            total = price.get("total")
            cur = price.get("currency") or (getattr(ctx.intent, "currency", None) if ctx.intent else "USD")

            itins = item.get("itineraries")
            if not isinstance(itins, list) or not itins:
                continue

            # Process each itinerary (onward and return flights)
            for itin in itins:
                if not isinstance(itin, dict):
                    continue
                    
                segs = itin.get("segments")
                if not isinstance(segs, list) or not segs:
                    continue

                seg0 = segs[0] if isinstance(segs[0], dict) else {}
                seg_last = segs[-1] if isinstance(segs[-1], dict) else {}

                dep = seg0.get("departure") if isinstance(seg0.get("departure"), dict) else {}
                arr = seg_last.get("arrival") if isinstance(seg_last.get("arrival"), dict) else {}

                carrier = seg0.get("carrierCode")
                flight_no = seg0.get("number")
                stops = max(0, len(segs) - 1)

                money_total = None
                try:
                    if total is not None:
                        money_total = Money(float(total), str(cur))
                except Exception:
                    money_total = None

                results.append(
                    FlightResult(
                        provider="amadeus",
                        origin=dep.get("iataCode"),  # Use actual segment origin
                        destination=arr.get("iataCode"),  # Use actual segment destination
                        depart_time=dep.get("at"),
                        arrive_time=arr.get("at"),
                    carrier=str(carrier) if carrier else None,
                    flight_number=str(flight_no) if flight_no else None,
                    stops=stops,
                    price=money_total,
                    raw=item,
                )
            )
        return results

    def run(self, ctx: SharedContext, step: Step) -> None:

        provider = "amadeus"
        tool_name = "amadeus.flights.search_flights"

        attempts = max(1, int(os.getenv("AMADEUS_HTTP_RETRIES", "1") or "1"))
        cfg = RetryConfig(attempts=attempts)

        # --- DEBUG ---
        #print("FlightSearchAgent.run()")
        #print("Intent:", getattr(ctx, "intent", None))

        def on_retry(failed_attempt: int, max_attempts: int, err: Exception, sleep_s: float) -> None:
            ctx.events.append(
                make_event(
                    "agent.retry",
                    agent=self.name,
                    step_id=step.id,
                    tool=tool_name,
                    attempt=failed_attempt,
                    max_attempts=max_attempts,
                    error=str(err),
                    sleep_s=float(sleep_s),
                )
            )

        try:
            provider_args = self._provider_args(ctx, step)
            # amadeus.flights.search_flights doesn't accept origin_alts/destination_alts
            kwargs = {k: v for k, v in provider_args.items() if k not in ("origin_alts", "destination_alts")}

            raw_data = call_with_retries(
                lambda: amadeus_flights.search_flights(**kwargs),
                args={},
                cfg=cfg,
                is_retryable=self._is_retryable,
                on_retry=on_retry,
            )

        except ValueError as e:
            ctx.scratch["fatal"] = True
            ctx.scratch["fatal_reason"] = str(e)
            ctx.confidence = 0.0
            ctx.add_warning(
                Warning(
                    code=WarningCode.PROVIDER_ERROR,
                    title="Invalid flight search input",
                    details={"error": str(e), "tool": tool_name, "args": dict(step.args or {}), "fatal": True,
                             "retries": attempts},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            return

        except Exception as e:
            fatal = self._is_fatal_provider_input_error(e)
            if fatal:
                ctx.scratch["fatal"] = True
                ctx.scratch["fatal_reason"] = "provider rejected request (bad input)"
                ctx.confidence = 0.0

            ctx.add_warning(
                Warning(
                    code=WarningCode.PROVIDER_ERROR,
                    title="Invalid flight search input" if fatal else "Flight provider error",
                    details={"error": str(e), "tool": tool_name, "args": dict(step.args or {}), "fatal": fatal,
                             "retries": attempts},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            if not fatal:
                ctx.confidence = min(ctx.confidence, 0.2)
            return

        if not isinstance(raw_data, dict) or not isinstance(raw_data.get("data"), list) or len(raw_data["data"]) == 0:
            ctx.scratch["bundle_impossible"] = True
            ctx.add_warning(
                Warning(
                    code=WarningCode.NO_RESULTS,
                    title="No flights found",
                    details={"tool": tool_name, "args": dict(step.args or {}),
                             "raw_count": len(raw_data.get("data", []))},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            ctx.flights.extend([])  # Ensure ctx.flights is always set to empty list when no results
            return

        # Parse results (use resolved origin/dest from kwargs for display)
        results = self._parse_results(ctx, step, raw_data, resolved_origin=kwargs.get("origin"), resolved_dest=kwargs.get("destination"))
        if not results:
            ctx.scratch["bundle_impossible"] = True
            ctx.add_warning(
                Warning(
                    code=WarningCode.PROVIDER_ERROR,
                    title="Flight payload parse mismatch",
                    details={"tool": tool_name, "args": dict(step.args or {}),
                             "raw_count": len(raw_data.get("data", [])), "parsed_count": 0},
                    step_id=step.id,
                    agent=self.name,
                )
            )
            return

        ctx.flights.extend(results)