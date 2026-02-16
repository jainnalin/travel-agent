# travel_agent/agents/planner/llm_based.py

from __future__ import annotations
from typing import List
import json
import requests

from travel_agent.agents.base import AgentBase
from travel_agent.contracts.context import SharedContext
from travel_agent.contracts.plan import Plan, Step


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"

ALLOWED_TOOLS = {
    "search_hotels",
    "search_flights",
    "geo_enrich",
    "enrich_results",
}


class LLMPlannerAgent(AgentBase):

    name = "llm_planner_agent"

    def handles(self) -> List[str]:
        return ["plan"]

    # =====================================================
    # ENTRY
    # =====================================================

    def run(self, ctx: SharedContext) -> Plan:

        try:
            plan = self._generate_plan_with_ollama(ctx)
            ctx.scratch["last_plan"] = plan
            return plan

        except Exception as e:
            ctx.warnings.append(f"Ollama failed → using mock planner: {str(e)}")
            return self._mock_plan(ctx)

    # =====================================================
    # OLLAMA LOGIC
    # =====================================================

    def _generate_plan_with_ollama(self, ctx: SharedContext) -> Plan:

        user_query = getattr(ctx.intent, "raw_text", None) or str(ctx.intent)

        prompt = f"""Generate JSON plan for: {user_query}

Tools:
search_hotels: {{"city": "city_name"}}
search_flights: {{"origin": "IATA_code", "destination": "IATA_code"}}
geo_enrich: {{}}
enrich_results: {{}}

Rules:
- JSON only
- Flight requests need origin+destination IATA codes
- Hotel requests need city names
- Bundle requests need both hotel AND flight steps

Output format:
{{"steps": [{{"tool": "tool_name", "args": {{...}}, "depends_on": []}}]}}"""

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=60,
        )

        response.raise_for_status()

        content = response.json()["response"]

        json_start = content.find("{")
        json_end = content.rfind("}") + 1

        if json_start == -1 or json_end == -1:
            raise ValueError("Invalid JSON from Ollama")

        plan_json = json.loads(content[json_start:json_end])

        return self._convert_json_to_plan(plan_json)

    # =====================================================
    # JSON → PLAN
    # =====================================================

    def _convert_json_to_plan(self, plan_json: dict) -> Plan:

        plan = Plan(steps=[])
        step_counter = 1

        for step_data in plan_json.get("steps", []):
            tool = step_data.get("tool")

            if tool not in ALLOWED_TOOLS:
                continue

            step_id = f"s{step_counter}"

            plan.steps.append(
                Step(
                    id=step_id,
                    tool=tool,
                    args=step_data.get("args", {}) or {},
                    depends_on=step_data.get("depends_on", []),
                )
            )

            step_counter += 1

        if not plan.steps:
            raise ValueError("LLM returned empty or invalid plan")

        return plan

    # =====================================================
    # ORIGINAL MOCK FALLBACK
    # =====================================================

    def _mock_plan(self, ctx: SharedContext) -> Plan:

        intent = getattr(ctx, "intent", None)
        plan = Plan(steps=[])

        if not intent:
            return plan

        domain = getattr(intent, "domain", None)
        step_counter = 1

        if domain == "bundle":

            plan.steps.append(
                Step(
                    id=f"s{step_counter}",
                    tool="search_hotels",
                    args={"city": getattr(intent, "city", None)},
                )
            )
            hotel_id = f"s{step_counter}"
            step_counter += 1

            plan.steps.append(
                Step(
                    id=f"s{step_counter}",
                    tool="search_flights",
                    args={
                        "origin": getattr(intent, "origin", None),
                        "destination": getattr(intent, "destination", None),
                        "return_date": getattr(intent, "return_date", None),
                    },
                )
            )
            flight_id = f"s{step_counter}"
            step_counter += 1

            # Add return flight search for bundle trips
            if domain == "bundle" and getattr(intent, "return_date", None):
                plan.steps.append(
                    Step(
                        id=f"s{step_counter}",
                        tool="search_flights",
                        args={
                            "origin": getattr(intent, "destination", None),  # Return from destination
                            "destination": getattr(intent, "origin", None),      # Back to origin
                            "depart_date": getattr(intent, "return_date", None),  # Depart on return date
                        },
                    )
                )
                step_counter += 1

            plan.steps.append(
                Step(
                    id=f"s{step_counter}",
                    tool="geo_enrich",
                    args={},
                    depends_on=[hotel_id],
                )
            )
            step_counter += 1

            plan.steps.append(
                Step(
                    id=f"s{step_counter}",
                    tool="enrich_results",
                    args={},
                    depends_on=[hotel_id, flight_id],
                )
            )

        elif domain == "hotel_only":
            plan.steps.append(
                Step(
                    id=f"s{step_counter}",
                    tool="search_hotels",
                    args={"city": getattr(intent, "city", None)},
                )
            )

        elif domain == "flight_only":
            plan.steps.append(
                Step(
                    id=f"s{step_counter}",
                    tool="search_flights",
                    args={
                        "origin": getattr(intent, "origin", None),
                        "destination": getattr(intent, "destination", None),
                    },
                )
            )

        return plan