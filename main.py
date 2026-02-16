# main.py
import sys
import os
from pathlib import Path

# Auto-source environment variables if env.sh exists
env_file = Path(__file__).parent / "env.sh"
if env_file.exists():
    import subprocess
    try:
        # Source env.sh and export variables to current process
        result = subprocess.run(
            f"source {env_file} && env",
            shell=True,
            capture_output=True,
            text=True
        )
        for line in result.stdout.splitlines():
            if '=' in line and not line.startswith('_'):
                key, value = line.split('=', 1)
                os.environ[key] = value
    except Exception as e:
        print(f"Warning: Could not load env.sh: {e}")

from travel_agent.cli.run_query import run_text_query


def main():
    import argparse
    p = argparse.ArgumentParser(description="Travel agent CLI")
    p.add_argument("--debug", action="store_true", help="Print raw intent/output for debugging")
    p.add_argument("query", nargs="*", help="Natural language query (e.g. 'hotels in Denver for 4 days')")
    args = p.parse_args()

    query = " ".join(args.query or []).strip()
    if not query:
        print("Usage: python3 main.py [--debug] 'your natural language query'")
        sys.exit(1)

    run_text_query(query, debug=args.debug)


if __name__ == "__main__":
    main()
