# travel_agent/runtime/run_id.py

from __future__ import annotations

from datetime import datetime, timezone
import uuid


def new_run_id(prefix: str = "travel") -> str:
    # Example: travel-20260128-031500Z-8f3e9a19
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts}-{short}"
