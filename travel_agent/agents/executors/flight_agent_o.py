# travel_agent/agents/executors/flight_agent.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from travel_agent.agents.base import AgentBase
from travel_agent.agents.executors.retry import RetryConfig, call_with_retries, is_retryable_message
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.events import EVENT_TOOL_CALLED, EVENT_TOOL_RESULT, make_event
from travel_agent.contracts.plan import Step
from travel_agent.contracts.result import FlightResult, Money
from travel_agent.contracts.warnings import Warning, WarningCode


class FlightSearchAgent(AgentBase):
    name = "agent.flight_search"

    def handles(self):
        return ["flight.search", "search_flights"]

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

    def _parse_results(self, ctx: SharedContext, step: Step, raw: Dict[str, Any]) -> List[FlightResult]:
        results: List[FlightResult] = []
        data = raw.get("data")
        if not isinstance(data, list):
            return results

        for item in data:
            if not isinstance(item, dict):
                continue

            price = item.get("price") if isinstance(item.get("price"), dict) else {}
            total = price.get("total")
            cur = price.get("currency") or ctx.intent.currency

            itins = item.get("itineraries")
            if not isinstance(itins, list) or not itins or not isinstance(itins[0], dict):
                continue
            itin0 = itins[0]
            segs = itin0.get("segments")
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
                    origin=step.args.get("origin"),
                    destination=step.args.get("destination"),
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

        # travel_agent/agents/executors/flight_agent.py
        def run(self, ctx: SharedContext, step=None, attempt_index=None, **kwargs):
            # mock flight results
            from types import SimpleNamespace
            ctx.flights = [
                SimpleNamespace(origin="Dallas", destination="Denver", total_price=120),
            ]