"""End-to-end demo runner for O3AI hackathon presentation.

Executes the complete Goal -> Result lifecycle:
1. Resets sample_app/signup.py to broken state (missing email & password validation).
2. Demonstrates RED pipeline (test failures).
3. Executes offline simulation of Duo flow (Planner + Builder) to generate patch and Draft MR.
4. Posts autonomous Security / QA review comment.
5. Executes GREEN pipeline across all 7 stages.
6. Displays Tower report and health monitor pass.
"""

from __future__ import annotations
import argparse
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent


def print_banner(step_num: int, title: str) -> None:
    """Print an eye-catching step banner."""
    print("\n" + "=" * 70)
    print(f"  [STEP {step_num}] {title}")
    print("=" * 70)


def pause_if_needed(interactive: bool, prompt: str = "Press Enter to proceed to next step...") -> None:
    """Pause execution for live video narration if in interactive mode."""
    if interactive:
        input(f"\n>> {prompt} ")
    else:
        time.sleep(1.0)


def run_command_live(cmd: list[str], cwd: Path) -> int:
    """Run command and stream output directly to stdout/stderr."""
    proc = subprocess.run(cmd, cwd=str(cwd))
    return proc.returncode


def run_demo(interactive: bool = False) -> int:
    """Execute the full demo lifecycle."""
    print("*" * 70)
    print("  O3AI DEMO: 'You give it a goal. It delivers a result.'")
    print("  Target Application: sample_app/signup.py")
    print("  Goal Issue #12: 'Add input validation to signup: reject invalid")
    print("                   emails and passwords under 8 chars.'")
    print("*" * 70)

    # -------------------------------------------------------------
    # Step 1: Demo Reset
    # -------------------------------------------------------------
    print_banner(1, "Reset to Baseline (Broken State)")
    print("Restoring sample_app/signup.py without email regex or password checks...")
    run_command_live([sys.executable, "scripts/demo_reset.py"], WORKSPACE)
    pause_if_needed(interactive, "Baseline restored. Press Enter to run RED pipeline...")

    # -------------------------------------------------------------
    # Step 2: RED Pipeline Run
    # -------------------------------------------------------------
    print_banner(2, "Trigger CI Pipeline on Broken State (RED Expected)")
    print("Running 7-stage CI pipeline. Expecting failure in 'test' stage...")
    ret = run_command_live([sys.executable, "scripts/run_pipeline.py"], WORKSPACE)
    if ret != 0:
        print("\n--> [VERIFIED]: Pipeline failed deterministically as expected (RED).")
    else:
        print("\n--> [UNEXPECTED]: Pipeline did not fail on broken code!")
        return 1
    pause_if_needed(interactive, "RED pipeline verified. Press Enter to invoke GitLab Duo Flow...")

    # -------------------------------------------------------------
    # Step 3: GitLab Duo Issue -> Planner -> MR Builder Flow
    # -------------------------------------------------------------
    print_banner(3, "Offline Simulation of Duo Flow (Issue -> Planner -> MR)")
    print("Running offline simulation of the Duo flow for reproducible tests...")
    ret = run_command_live([sys.executable, "scripts/issue_to_mr.py"], WORKSPACE)
    if ret != 0:
        print("[ERROR] issue_to_mr.py failed.")
        return 1

    diff_path = WORKSPACE / "results" / "mr_patch.diff"
    if diff_path.exists():
        print("\n--- [Generated Merge Request Diff] ---")
        print(diff_path.read_text(encoding="utf-8"))
    pause_if_needed(interactive, "MR opened and patch applied. Press Enter to run QA / Security Review...")

    # -------------------------------------------------------------
    # Step 4: Autonomous Security / QA Gate Comment
    # -------------------------------------------------------------
    print_banner(4, "GitLab Duo Security & QA MR Review Bot")
    print("Reviewing code changes against security standards and test coverage...")
    ret = run_command_live([sys.executable, "scripts/mr_review_comment.py"], WORKSPACE)
    if ret != 0:
        print("[ERROR] mr_review_comment.py failed.")
        return 1

    review_file = WORKSPACE / "results" / "mr_review.md"
    if review_file.exists():
        print("\n--- [MR Bot Review Comment] ---")
        content = review_file.read_text(encoding="utf-8")
        safe_encoding = sys.stdout.encoding or "utf-8"
        print(content.encode(safe_encoding, errors="replace").decode(safe_encoding))
    pause_if_needed(interactive, "MR approved. Press Enter to run GREEN pipeline...")

    # -------------------------------------------------------------
    # Step 5: GREEN Pipeline Run
    # -------------------------------------------------------------
    print_banner(5, "Re-run Full 7-Stage CI Pipeline (GREEN Expected)")
    print("Executing: validate -> test -> security -> tower-report -> approve -> deploy -> monitor")
    ret = run_command_live([sys.executable, "scripts/run_pipeline.py"], WORKSPACE)
    if ret != 0:
        print("[ERROR] Pipeline failed unexpectedly on patched code!")
        return 1
    print("\n--> [VERIFIED]: All 7 stages PASSED (GREEN).")
    pause_if_needed(interactive, "GREEN pipeline verified. Press Enter to inspect final evidence...")

    # -------------------------------------------------------------
    # Step 6: Final Evidence & Working Tower Report
    # -------------------------------------------------------------
    print_banner(6, "Evidence Verification & GitLab Pages Deployment")
    print("Artifacts generated and verified:")
    print(f"  - Working Tower Dashboard: {WORKSPACE / 'results' / 'tower.html'}")
    print(f"  - Human Evidence Report:   {WORKSPACE / 'results' / 'report.md'}")
    print(f"  - Deployed Public Site:     {WORKSPACE / 'public' / 'index.html'}")
    print(f"  - Monitor Status:          {WORKSPACE / 'results' / 'monitor.json'}")

    print("\n" + "*" * 70)
    print("  [DEMO COMPLETE] Goal successfully resolved and evidence verified!")
    print("*" * 70 + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="O3AI End-to-End Demo Script")
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Pause between demo steps for live video narration"
    )
    args = parser.parse_args()
    return run_demo(interactive=args.interactive)


if __name__ == "__main__":
    sys.exit(main())
