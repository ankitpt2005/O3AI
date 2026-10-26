"""Deploy stage script packaging artifacts for GitLab Pages."""

from __future__ import annotations
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def deploy_to_public(workspace_root: Path) -> tuple[bool, str]:
    """Copy dashboard and reports into public/ directory for GitLab Pages."""
    results_dir = workspace_root / "results"
    approve_file = results_dir / "approve.json"

    # Enforce human approval gate
    if not approve_file.exists():
        return False, "Deployment blocked: results/approve.json does not exist."

    try:
        with open(approve_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("status") != "PASS":
            return False, f"Deployment blocked: approve stage status is {data.get('status')}."
    except Exception as err:
        return False, f"Error reading approve.json: {err}"

    public_dir = workspace_root / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    tower_src = results_dir / "tower.html"
    if not tower_src.exists():
        return False, "Missing results/tower.html."

    # Copy tower.html as the primary landing page
    shutil.copyfile(tower_src, public_dir / "index.html")

    report_src = results_dir / "report.md"
    if report_src.exists():
        shutil.copyfile(report_src, public_dir / "report.md")

    # Copy results directory into public/results for evidence audit
    dest_results = public_dir / "results"
    dest_results.mkdir(parents=True, exist_ok=True)
    for jf in results_dir.glob("*.json"):
        shutil.copyfile(jf, dest_results / jf.name)

    return True, "Published to public/index.html"


def main() -> int:
    """Main CLI entrypoint for deploy stage."""
    workspace = Path.cwd()
    passed, msg = deploy_to_public(workspace)

    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "deploy.json"

    payload = {
        "stage": "deploy",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "target": "GitLab Pages (public/)",
            "message": msg,
            "index_published": (workspace / "public" / "index.html").exists(),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    if passed:
        print(f"[SUCCESS] GitLab Pages deploy package created: {msg}")
        return 0
    else:
        print(f"[FAILURE] Deployment failed: {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
