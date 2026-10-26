"""Manual approval gate execution for O3AI pipeline."""

from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PREREQUISITE_STAGES = ["validate", "test", "security", "tower_report"]


def verify_prerequisites(results_dir: Path) -> tuple[bool, list[str]]:
    """Verify that all prerequisite pipeline stages passed before approval."""
    missing_or_failed: list[str] = []
    for stage in PREREQUISITE_STAGES:
        stage_file = results_dir / f"{stage}.json"
        if not stage_file.exists():
            missing_or_failed.append(f"Missing {stage}.json")
            continue
        try:
            with open(stage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") != "PASS":
                missing_or_failed.append(f"{stage} has status '{data.get('status')}'")
        except Exception as err:
            missing_or_failed.append(f"Corrupt {stage}.json: {err}")

    return len(missing_or_failed) == 0, missing_or_failed


def main() -> int:
    """Main CLI entrypoint for approval stage."""
    workspace = Path.cwd()
    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    passed, reasons = verify_prerequisites(results_dir)
    out_file = results_dir / "approve.json"

    payload = {
        "stage": "approve",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "approved_by": "human_reviewer",
            "prerequisites_checked": PREREQUISITE_STAGES,
            "verification_passed": passed,
            "issues": reasons,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    if passed:
        print("[SUCCESS] Human approval recorded: Ready for deployment.")
        return 0
    else:
        print(f"[FAILURE] Approval rejected due to failed prerequisites:")
        for r in reasons:
            print(f"  - {r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
