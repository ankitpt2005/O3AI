"""Command line interface for O3AI Goal Sessions and Tower Reports."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from o3ai.models import GitLabIssue
from o3ai.report import generate_reports
from o3ai.security import SecurityValidationError
from o3ai.session import create_goal_session


def parse_args(args: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="o3ai",
        description="O3AI: GitLab-native goal-to-result engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init-session", help="Initialize a Goal Session from a GitLab issue"
    )
    init_parser.add_argument("--issue-id", type=int, required=True, help="GitLab Issue ID")
    init_parser.add_argument("--title", type=str, required=True, help="Issue Title (max 35 words)")
    init_parser.add_argument(
        "--description", type=str, default="", help="Issue description text"
    )

    subparsers.add_parser(
        "generate-report", help="Read results/*.json and compile tower.html and report.md"
    )

    return parser.parse_args(args)


def run_init_session(issue_id: int, title: str, description: str) -> int:
    """Initialize session and print confirmation."""
    issue = GitLabIssue(issue_id=issue_id, title=title, description=description)
    workspace = Path.cwd()
    try:
        session = create_goal_session(issue, workspace)
        print(f"Goal Session created: {session.session_id}")
        print(f"Results recorded in: {workspace / 'results' / 'session.json'}")
        return 0
    except SecurityValidationError as err:
        print(f"Security validation failed: {err}", file=sys.stderr)
        return 1


def run_generate_report() -> int:
    """Read results/*.json and compile reports."""
    workspace = Path.cwd()
    results_dir = workspace / "results"
    html_path, md_path = generate_reports(results_dir)
    print(f"Generated Tower Dashboard: {html_path}")
    print(f"Generated Evidence Report: {md_path}")
    return 0


def main() -> None:
    """Main CLI entry point."""
    parsed = parse_args(sys.argv[1:])
    if parsed.command == "init-session":
        code = run_init_session(parsed.issue_id, parsed.title, parsed.description)
        sys.exit(code)
    elif parsed.command == "generate-report":
        code = run_generate_report()
        sys.exit(code)


if __name__ == "__main__":
    main()
