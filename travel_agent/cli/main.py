# travel_agent/cli/main.py

from __future__ import annotations

import argparse

from travel_agent.cli.commands.run import add_run_subparser
from travel_agent.cli.commands.replay import add_replay_subparser  # new

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="travel_agent",
        description="Multi-agent travel agent (CLI-first)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    add_run_subparser(sub)
    add_replay_subparser(sub)  # ✅ register replay

    return p

def main() -> None:
    args = build_parser().parse_args()

    fn = getattr(args, "func", None)
    if not callable(fn):
        raise SystemExit("No command selected. Try: travel_agent run -h")

    fn(args)