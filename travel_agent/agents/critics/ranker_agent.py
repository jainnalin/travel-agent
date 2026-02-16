#travel_agent/agents/critics/ranker_agent.py
from __future__ import annotations

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.events import EVENT_TOOL_CALLED, EVENT_TOOL_RESULT, make_event
from travel_agent.contracts.plan import Step
from travel_agent.contracts.result import RankedItem
from travel_agent.tools.ranking.bundle import rank_bundles
from travel_agent.tools.ranking.flight import rank_flights
from travel_agent.tools.ranking.hotel import rank_hotels


class SimpleRankerAgent(AgentBase):
    name = "agent.ranker"
    def handles(self):
        return ["hotel.rank", "flight.rank", "bundle.rank"]

    def run(self, ctx: SharedContext, step: Step) -> None:
        top = int(step.args.get("top", 10))
        ranked: list[RankedItem] = []

        # Emit tool.called for rank steps (so you can grep reliably)
        ctx.events.append(
            make_event(
                EVENT_TOOL_CALLED,
                tool=str(step.tool),
                args={"top": int(top), **dict(step.args or {})},
            )
        )

        if step.tool == "hotel.rank":
            deduped_indices = _dedupe_hotels_best_index(ctx)

            ctx.scratch["hotels_raw_count"] = len(ctx.hotels)
            ctx.scratch["hotels_unique_count"] = len(deduped_indices)

            deduped_hotels = [ctx.hotels[i] for i in deduped_indices]
            objective = step.args.get("objective") or getattr(ctx.intent, "objective", None) or "cheapest"

            ranked_deduped = rank_hotels(deduped_hotels, top=top, objective=str(objective))

            ranked = [
                RankedItem(
                    score=ri.score,
                    item_type="hotel",
                    item_index=deduped_indices[ri.item_index],
                    reasons=list(ri.reasons or []),
                )
                for ri in ranked_deduped
                if 0 <= ri.item_index < len(deduped_indices)
            ]

            meta = {"objective": str(objective), "top": int(top), "raw": len(ctx.hotels), "unique": len(deduped_indices)}
            ctx.scratch.setdefault("hotel_rank_meta", {}).update(meta)
            ctx.events.append(make_event("rank.meta", domain="hotel", **meta))

        elif step.tool == "flight.rank":
            ranked = rank_flights(ctx.flights, top=top)

            meta = {"top": int(top), "flights_total": len(ctx.flights)}
            ctx.scratch.setdefault("flight_rank_meta", {}).update(meta)
            ctx.events.append(make_event("rank.meta", domain="flight", **meta))

        else:  # bundle.rank
            objective = step.args.get("objective") or getattr(ctx.intent, "objective", None) or "cheapest"
            ranked = rank_bundles(ctx.bundles, objective=str(objective), top=top)

            meta = {"objective": str(objective), "top": int(top), "bundles_total": len(ctx.bundles)}
            ctx.scratch.setdefault("bundle_rank_meta", {}).update(meta)
            ctx.events.append(make_event("rank.meta", domain="bundle", **meta))

        ctx.ranked = sorted(ranked, key=lambda x: x.score, reverse=True)

        # Emit tool.result for rank steps (count = ranked items)
        ctx.events.append(make_event(EVENT_TOOL_RESULT, tool=str(step.tool), count=len(ctx.ranked)))


def _dedupe_hotels_best_index(ctx: SharedContext) -> list[int]:
    best: dict[str, int] = {}

    for i, h in enumerate(ctx.hotels):
        key = _hotel_key(h)
        prev_i = best.get(key)
        if prev_i is None:
            best[key] = i
            continue

        cur_amt = _money_amount(getattr(h, "total_price", None))
        prev_amt = _money_amount(getattr(ctx.hotels[prev_i], "total_price", None))

        if prev_amt is None and cur_amt is not None:
            best[key] = i
        elif cur_amt is None and prev_amt is not None:
            continue
        elif cur_amt is not None and prev_amt is not None:
            if cur_amt < prev_amt:
                best[key] = i

    indices = list(best.values())
    indices.sort(
        key=lambda idx: (
            _money_amount(getattr(ctx.hotels[idx], "total_price", None)) is None,
            _money_amount(getattr(ctx.hotels[idx], "total_price", None)) or 10**12,
            idx,
        )
    )
    return indices


def _hotel_key(h) -> str:
    raw = getattr(h, "raw", {}) or {}
    hid = None
    if isinstance(raw, dict):
        hid = raw.get("hotelId") or raw.get("id") or raw.get("hotel_id") or raw.get("compose", {}).get("hotel_id")

    if hid:
        return f"id:{str(hid).strip().lower()}"

    name = (getattr(h, "hotel_name", "") or "").strip().lower()
    city = (getattr(h, "city", "") or "").strip().lower()
    area = (getattr(h, "area", "") or "").strip().lower()
    return f"name:{name}|city:{city}|area:{area}"


def _money_amount(m) -> float | None:
    try:
        if m is None:
            return None
        return float(getattr(m, "amount", None))
    except Exception:
        return None