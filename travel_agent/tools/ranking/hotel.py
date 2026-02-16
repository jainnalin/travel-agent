# travel_agent/tools/ranking/hotel.py
from __future__ import annotations
from types import SimpleNamespace

from travel_agent.contracts.result import HotelResult, RankedItem
from travel_agent.tools.ranking.scoring import money_amount  # noqa: F401 - used by score()


def _rating_from_raw(h: HotelResult) -> float | None:
    """
    Try to read rating (0-5) from HotelResult.raw.
    Kept defensive because provider payloads vary.
    """
    raw = getattr(h, "raw", None)
    if not isinstance(raw, dict):
        return None
    v = raw.get("rating")
    try:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            v = float(s)
        if isinstance(v, (int, float)):
            fv = float(v)
            # If something slips in as 0-10, normalize to 0-5
            if fv > 5.0 and fv <= 10.0:
                fv = fv / 2.0
            if 0.0 <= fv <= 5.0:
                return fv
    except Exception:
        return None
    return None


def rank_hotels(hotels: list[HotelResult], *, top: int = 10, objective: str = "cheapest") -> list[RankedItem]:
    """
    Deterministic hotel ranking (V1):

    objective:
      - "cheapest" (default): cheaper total price wins, small bonus for stars/refundable
      - "highest_rated": stars/rating wins, price is a tiebreaker

    Returns RankedItem indices into the *original* hotels list.
    """
    top = max(0, int(top))
    obj = "highest_rated" if str(objective) == "highest_rated" else "cheapest"

    scored: list[tuple[float, int, list[str]]] = []

    for i, h in enumerate(hotels):
        reasons: list[str] = []
        score = 0.0

        total_amt = money_amount(h.total_price)

        stars = h.stars
        rating = _rating_from_raw(h)

        if obj == "highest_rated":
            # Primary: stars/rating (where present)
            if stars is not None:
                score += float(stars) * 1000.0
                reasons.append("stars")
            if rating is not None:
                score += float(rating) * 800.0
                reasons.append("rating")

            if h.refundable is True:
                score += 100.0
                reasons.append("refundable")

            # Price only as tiebreaker
            if total_amt is not None:
                score += max(0.0, 5000.0 - total_amt) * 0.2
                reasons.append("price-tiebreak")
            else:
                reasons.append("missing-total-price")

        else:
            # Primary: cheap total
            if total_amt is not None:
                score += max(0.0, 10000.0 - total_amt)
                reasons.append("cheap-total")
            else:
                reasons.append("missing-total-price")

            # Small quality bumps
            if stars is not None:
                score += float(stars) * 20.0
                reasons.append("stars-bonus")
            if rating is not None:
                score += float(rating) * 10.0
                reasons.append("rating-bonus")
            if h.refundable is True:
                score += 15.0
                reasons.append("refundable-bonus")

        scored.append((float(score), int(i), reasons or ["baseline"]))

    scored.sort(key=lambda t: (-t[0], t[1]))

    out: list[RankedItem] = []
    for score, idx, reasons in scored[:top]:
        out.append(RankedItem(score=score, item_type="hotel", item_index=idx, reasons=reasons or ["baseline"]))
    return out


def score(hotel):
    # simple mock: cheaper hotels get higher score
    price = money_amount(getattr(hotel, "total_price", None))
    if price is None:
        price = 1000.0
    return max(0.0, 1000.0 - float(price))
