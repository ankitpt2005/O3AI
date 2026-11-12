"""CLI for O3AI's optional live AI planning proposal."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from integrations.ai_planner import AIPlannerError, create_ai_plan
from integrations.env import load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a validated O3AI planning proposal with an LLM")
    parser.add_argument("--goal", required=True, help="A single bounded goal to plan")
    parser.add_argument("--output", type=Path, default=Path("results/ai_plan.json"))
    args = parser.parse_args(argv)
    load_dotenv(_root)
    try:
        result = create_ai_plan(args.goal, args.output)
    except AIPlannerError as exc:
        print(f"AI plan unavailable: {exc}", file=sys.stderr)
        return 1
    print(f"Validated proposal ({len(result['tasks'])} DAG tasks): {args.output}")
    print("This proposal is not evidence and cannot authorize a PayPal action.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
