# tests/test_smoke.py

import os
from datetime import date, timedelta

import pytest


@pytest.mark.smoke
def test_agent_smoke():
    """
    Minimal smoke test:
    - imports agent
    - runs with fake inputs
    - validates output shape
    No network mocking. Cache should protect repeated calls.
    """

    # Skip if Amadeus creds are missing (local dev / CI safety)
    if not os.getenv("AMADEUS_CLIENT_ID"):
        pytest.skip("AMADEUS_CLIENT_ID not set")

    from agent import agent_run

    today = date.today()
    goal = {
        "city": "Plano",
        "check_in": (today + timedelta(days=30)).isoformat(),
        "check_out": (today + timedelta(days=33)).isoformat(),
        "adults": 2,
        "domain": "US",
        "locale": "en_US",
        "currency": "USD",

        # keep it simple
        "min_results": 3,
        "enrich_top_n": 0,
        "max_attempts": 2,
    }

    result = agent_run(goal)

    # ---- shape checks only ----
    assert isinstance(result, dict)
    assert "run_id" in result
    assert "hotels" in result
    assert "api_stats" in result
    assert isinstance(result["hotels"], list)

    # If hotels exist, spot-check fields
    if result["hotels"]:
        h = result["hotels"][0]
        assert "id" in h
        assert "name" in h
        assert "provider" in h
