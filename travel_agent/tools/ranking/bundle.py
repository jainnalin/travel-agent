#travel_agent/tools/ranking/bundle.py
from __future__ import annotations
from typing import Literal
from travel_agent.contracts.result import BundleResult, RankedItem
from travel_agent.tools.ranking.scoring import bundle_cheapest_score, bundle_highest_rated_score


Objective = Literal["cheapest", "highest_rated"]


def rank_bundles(
    bundles: list[BundleResult],
    *,
    objective: str,
    top: int,
) -> list[RankedItem]:
    """
    Deterministically rank bundles.
    Returns RankedItem indices into the *original* ctx.bundles list.
    """
    obj: Objective = "cheapest" if objective != "highest_rated" else "highest_rated"
    top = max(0, int(top))

    scored: list[tuple[float, int, list[str]]] = []
    for i, b in enumerate(bundles):
        if obj == "highest_rated":
            score, reasons = bundle_highest_rated_score(b)
        else:
            score, reasons = bundle_cheapest_score(b)
        scored.append((float(score), int(i), reasons))

    # Stable sort: score desc, index asc
    scored.sort(key=lambda t: (-t[0], t[1]))

    ranked: list[RankedItem] = []
    for score, idx, reasons in scored[:top]:
        ranked.append(RankedItem(score=score, item_type="bundle", item_index=idx, reasons=reasons or ["baseline"]))
    return ranked
