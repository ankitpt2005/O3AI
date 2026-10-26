"""Tower report compilation job for O3AI pipeline."""

from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure workspace root is in sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from o3ai.report import generate_reports


def run_tower_report(workspace_root: Path) -> int:
    """Generate tower.html and report.md, recording evidence in results/tower_report.json."""
    results_dir = workspace_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    html_path, md_path = generate_reports(results_dir)
    passed = html_path.exists() and md_path.exists()

    out_file = results_dir / "tower_report.json"
    payload = {
        "stage": "tower-report",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "html_path": str(html_path.relative_to(workspace_root)),
            "md_path": str(md_path.relative_to(workspace_root)),
            "html_size_bytes": html_path.stat().st_size if html_path.exists() else 0,
            "md_size_bytes": md_path.stat().st_size if md_path.exists() else 0,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    if passed:
        print("[SUCCESS] Tower report generated successfully:")
        print(f"  - Dashboard: {html_path}")
        print(f"  - Report:    {md_path}")
        return 0
    else:
        print("[FAILURE] Failed to generate tower report artifacts.")
        return 1


def main() -> int:
    """Main CLI entrypoint for tower-report stage."""
    return run_tower_report(Path.cwd())


if __name__ == "__main__":
    sys.exit(main())
