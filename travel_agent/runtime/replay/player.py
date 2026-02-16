# travel_agent/runtime/replay/player.py
from __future__ import annotations

import os
import json
import time
from typing import Generator

from travel_agent.runtime.store.file_store import FileRunStore
from travel_agent.contracts.context import SharedContext


class ReplayPlayer:
    """
    Reads a saved run from FileRunStore and replays events.
    """
    def __init__(self, runs_dir: str = "runs") -> None:
        self.store = FileRunStore(runs_dir=runs_dir)

    def _run_path(self, run_id: str) -> str:
        return os.path.join(self.store.runs_dir, f"{run_id}.json")

    def load_ctx(self, run_id: str) -> SharedContext:
        """
        Load the full SharedContext from disk.
        """
        path = self._run_path(run_id)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Run {run_id} not found in {self.store.runs_dir}")
        from travel_agent.runtime.serialization import read_ctx
        return read_ctx(path)

    def replay_events(
        self,
        run_id: str,
        since: int = 0,
        follow: bool = False,
        follow_seconds: int = 0,
    ) -> Generator[dict, None, None]:
        """
        Yield events from a saved run starting from index `since`.
        If follow=True, keep yielding new events until file stops updating or follow_seconds elapses.
        Emits heartbeat events when waiting for new events.
        """
        path = self._run_path(run_id)
        idx = since
        start_ts = time.time()

        yield {
            "ts": time.time(),
            "kind": "stream.open",
            "data": {"run_id": run_id, "since": since, "follow": follow},
        }

        while True:
            ctx = self.load_ctx(run_id)
            events = getattr(ctx, "events", []) or []

            emitted_any = False
            while idx < len(events):
                yield events[idx]
                idx += 1
                emitted_any = True

            if not follow:
                if not emitted_any:
                    # emit one final heartbeat if nothing happened
                    yield {"ts": time.time(), "kind": "stream.heartbeat", "data": {"note": "no events"}}
                break

            # check follow_seconds timeout
            if follow_seconds and (time.time() - start_ts) >= float(follow_seconds):
                yield {"ts": time.time(), "kind": "stream.heartbeat", "data": {"note": "follow_seconds elapsed"}}
                break

            # emit heartbeat if no events since last iteration
            if not emitted_any:
                yield {"ts": time.time(), "kind": "stream.heartbeat", "data": {"note": "waiting"}}

            time.sleep(0.25)  # small delay to avoid busy loop


# ✅ Module-level helper for CLI
def replay_run(
    run_id: str,
    since: int = 0,
    runs_dir: str = "runs",
    follow: bool = False,
    follow_seconds: int = 0,
) -> None:
    """
    CLI-friendly helper to replay events from a run.
    """
    player = ReplayPlayer(runs_dir=runs_dir)
    for ev in player.replay_events(run_id, since=since, follow=follow, follow_seconds=follow_seconds):
        print(json.dumps(ev, ensure_ascii=False))