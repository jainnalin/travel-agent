#travel-agent/travel_agent/tools/ranking/flight.py
from __future__ import annotations
from types import SimpleNamespace

from travel_agent.contracts.result import FlightResult, RankedItem
from travel_agent.tools.ranking.scoring import money_amount


def rank_flights(flights: list[FlightResult], *, top: int = 10) -> list[RankedItem]:
    """
    Deterministic flight ranking (V1):
      - cheaper total price is better
      - nonstop bonus
      - fewer stops bonus

    Returns RankedItem indices into the *original* flights list.
    """
    top = max(0, int(top))
    scored: list[tuple[float, int, list[str]]] = []

    for i, f in enumerate(flights):
        reasons: list[str] = []
        score = 0.0

        amt = money_amount(f.price)
        if amt is not None:
            score += max(0.0, 10000.0 - amt)
            reasons.append("cheap-total")
        else:
            reasons.append("missing-price")

        if f.stops == 0:
            score += 500.0
            reasons.append("nonstop-bonus")
        elif f.stops is not None:
            score += max(0.0, 300.0 - (float(f.stops) * 150.0))
            reasons.append("fewer-stops")
        else:
            reasons.append("unknown-stops")

        scored.append((float(score), int(i), reasons))

    # Stable sort: score desc, index asc
    scored.sort(key=lambda t: (-t[0], t[1]))

    out: list[RankedItem] = []
    for score, idx, reasons in scored[:top]:
        out.append(RankedItem(score=score, item_type="flight", item_index=idx, reasons=reasons or ["baseline"]))
    return out


def score(flight):
    # simple mock scoring: lower price = higher score
    price = money_amount(getattr(flight, "price", None) or getattr(flight, "total_price", None))
    if price is None:
        price = 1000.0
    return max(0.0, 1000.0 - float(price))
