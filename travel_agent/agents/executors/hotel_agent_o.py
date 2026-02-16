# travel_agent/agents/executors/hotel_agent.py
from __future__ import annotations

import os

from travel_agent.agents.base import AgentBase
from travel_agent.agents.executors.retry import RetryConfig, call_with_retries, is_retryable_message
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.events import EVENT_TOOL_CALLED, EVENT_TOOL_RESULT, make_event
from travel_agent.contracts.plan import Step
from travel_agent.contracts.result import HotelResult, Money
from travel_agent.contracts.warnings import Warning, WarningCode


class HotelSearchAgent(AgentBase):
    name = "agent.hotel_search"

    def handles(self):
        return ["hotel.search", "search_hotels"]

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        if isinstance(err, ValueError):
            return False
        return is_retryable_message(str(err))

    @staticmethod
    def _provider_args(step_args: dict) -> dict:
        args = dict(step_args or {})
        args.pop("expand_search", None)
        return args

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
            if v is None:
                return None
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return int(round(float(v)))
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                return int(round(float(s)))
        except Exception:
            return None
        return None

    @staticmethod
    def _to_float(v) -> float | None:
        try:
            if v is None:
                return None
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                return float(s)
        except Exception:
            return None
        return None

        # travel_agent/agents/executors/hotel_agent.py
        def run(self, ctx: SharedContext, step=None, attempt_index=None, **kwargs):
            # mock hotel results
            from types import SimpleNamespace
            ctx.hotels = [
                SimpleNamespace(name="Mock Hotel A", total_price=200, latitude=39.7392, longitude=-104.9903),
                SimpleNamespace(name="Mock Hotel B", total_price=150, latitude=39.7500, longitude=-104.9900),
            ]