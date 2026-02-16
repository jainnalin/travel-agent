#travel_agent/runtime/store/file_store.py

from __future__ import annotations

import os
from travel_agent.contracts.context import SharedContext
from travel_agent.runtime.serialization import write_ctx


class FileRunStore:
    def __init__(self, runs_dir: str = "runs") -> None:
        self.runs_dir = runs_dir
        os.makedirs(self.runs_dir, exist_ok=True)

    def save(self, ctx: SharedContext) -> str:
        path = os.path.join(self.runs_dir, f"{ctx.run_id}.json")
        write_ctx(path, ctx)
        return path
