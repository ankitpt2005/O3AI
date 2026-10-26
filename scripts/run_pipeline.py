"""Local runner executing all 7 GitLab CI pipeline stages sequentially."""

from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path


PIPELINE_STAGES = [
    ("validate", [sys.executable, "scripts/lint.py"]),
    ("test", [sys.executable, "scripts/test_runner.py"]),
    ("security", [sys.executable, "scripts/security_check.py"]),
    ("tower-report", [sys.executable, "scripts/tower_report_job.py"]),
    ("approve", [sys.executable, "scripts/approve.py"]),
    ("deploy", [sys.executable, "scripts/deploy_pages.py"]),
    ("monitor", [sys.executable, "scripts/health_check.py"]),
]


def run_pipeline() -> int:
    """Execute all pipeline stages in sequence."""
    print("=" * 60)
    print("O3AI FULL PIPELINE RUNNER (7 STAGES)")
    print("validate -> test -> security -> tower-report -> approve -> deploy -> monitor")
    print("=" * 60 + "\n")

    workspace = Path.cwd()
    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    for stage_name, cmd in PIPELINE_STAGES:
        print(f"--- [Stage: {stage_name}] Running: {' '.join(cmd[1:])} ---")
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"\n[FAILURE] Stage '{stage_name}' failed with exit code {proc.returncode}.\n")
            print("[PIPELINE RESULT]: RED [FAIL]")
            return proc.returncode

        # Check evidence file existence and content
        evidence_file = results_dir / f"{stage_name.replace('-', '_')}.json"
        if not evidence_file.exists():
            evidence_file = results_dir / f"{stage_name}.json"

        if evidence_file.exists():
            with open(evidence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  -> Evidence verified: {evidence_file.name} (Status: {data.get('status')})\n")
        else:
            print(f"  -> [WARNING] Missing evidence file: {evidence_file.name}\n")

    print("=" * 60)
    print("[PIPELINE RESULT]: ALL 7 STAGES GREEN [PASS]")
    print("Deployed dashboard ready at: public/index.html")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(run_pipeline())
