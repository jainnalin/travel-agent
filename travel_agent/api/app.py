#travel-agent/travel_agent/api/app.py
from __future__ import annotations

from fastapi import FastAPI

from travel_agent.api.routes.runs import router as runs_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="travel-agent",
        version="0.1.0",
    )

    # Phase 5: Run lifecycle API + event streaming
    app.include_router(runs_router)

    # Basic health endpoint (useful for k8s / local checks)
    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app


# For `uvicorn travel_agent.api.app:app`
app = create_app()
