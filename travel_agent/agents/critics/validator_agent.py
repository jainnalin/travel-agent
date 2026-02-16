#travel_agent/agents/critics/validator_agent.py
from __future__ import annotations

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Step
from travel_agent.contracts.warnings import Warning, WarningCode


class ValidatorAgent(AgentBase):
    name = "agent.validator"

    def handles(self):
        return ["critic.validate"]

    def run(self, ctx: SharedContext, step: Step) -> None:
        min_results = int((step.args or {}).get("min_results", 5))
        target_confidence = float((step.args or {}).get("target_confidence", 0.8))

        domain = getattr(ctx.intent, "domain", None) or "hotel_only"

        hotels_n = len(getattr(ctx, "hotels", []) or [])
        flights_n = len(getattr(ctx, "flights", []) or [])
        bundles_n = len(getattr(ctx, "bundles", []) or [])

        # --- Domain-aware “what counts as a result” ---
        if domain == "bundle":
            required_total = bundles_n
        elif domain == "flight_only":
            required_total = flights_n
        else:
            required_total = hotels_n

        # Breadcrumbs
        ctx.scratch.setdefault("validate_meta", {})
        ctx.scratch["validate_meta"].update(
            {
                "domain": domain,
                "min_results": int(min_results),
                "target_confidence": float(target_confidence),
                "count_used": int(required_total),
                "hotels": int(hotels_n),
                "flights": int(flights_n),
                "bundles": int(bundles_n),
            }
        )

        # If we have enough required-domain results, raise confidence to target
        if required_total >= min_results:
            ctx.confidence = max(float(getattr(ctx, "confidence", 0.0)), float(target_confidence))
            return

        # --- Better bundle-domain messaging ---
        if domain == "bundle":
            # Bundles can't exist without flights+hotels. If hotels exist but flights are missing,
            # this is not "no results overall"; it's "bundle not possible".
            if hotels_n > 0 and flights_n == 0 and bundles_n == 0:
                ctx.add_warning(
                    Warning(
                        code=WarningCode.PARTIAL_RESULTS,
                        title="Bundle unavailable (missing flights)",
                        details={
                            "domain": "bundle",
                            "min_results": int(min_results),
                            "required_domain_results": int(bundles_n),
                            "hotels": int(hotels_n),
                            "flights": int(flights_n),
                            "bundles": int(bundles_n),
                        },
                        step_id=step.id,
                        agent=self.name,
                    )
                )
                ctx.confidence = min(float(getattr(ctx, "confidence", 0.0)), 0.4)
                return

        # Default warning path
        ctx.add_warning(
            Warning(
                code=WarningCode.NO_RESULTS if required_total == 0 else WarningCode.PARTIAL_RESULTS,
                title="No results across required domain" if required_total == 0 else "Not enough results",
                details={
                    "domain": domain,
                    "total": int(required_total),
                    "min_results": int(min_results),
                    "hotels": int(hotels_n),
                    "flights": int(flights_n),
                    "bundles": int(bundles_n),
                },
                step_id=step.id,
                agent=self.name,
            )
        )

        ctx.confidence = min(float(getattr(ctx, "confidence", 0.0)), 0.4)