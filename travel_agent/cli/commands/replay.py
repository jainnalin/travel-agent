# travel_agent/cli/commands/replay.py
from __future__ import annotations

import argparse
from typing import Any

from travel_agent.runtime.replay.player import replay_run  # top-level function

def add_replay_subparser(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "replay",
        help="Replay a previous run by run_id"
    )
    p.add_argument("--run_id", type=str, required=True, help="Run ID to replay")
    p.add_argument("--since", type=int, default=0, help="Replay events since this index")
    p.add_argument("--follow", action="store_true", help="Keep streaming new events like tail -f")
    p.add_argument("--follow_seconds", type=int, default=0, help="Stop following after N seconds (0 = no limit)")
    p.set_defaults(func=_replay_cmd)


def _replay_cmd(args: argparse.Namespace) -> None:
    replay_run(
        run_id=args.run_id,
        since=args.since,
        follow=args.follow,
        follow_seconds=args.follow_seconds,
    )