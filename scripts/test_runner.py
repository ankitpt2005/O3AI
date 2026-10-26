"""Test runner script for the test stage of the O3AI pipeline."""

from __future__ import annotations
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


def run_tests_and_record(workspace_root: Path) -> int:
    """Run unittest suite and record evidence in results/test.json."""
    # Ensure workspace root is in sys.path for test discovery
    workspace_str = str(workspace_root.resolve())
    if workspace_str not in sys.path:
        sys.path.insert(0, workspace_str)

    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=str(workspace_root / "tests"),
        pattern="test_*.py",
        top_level_dir=workspace_str,
    )

    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    passed = result.wasSuccessful()
    results_dir = workspace_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "test.json"

    payload = {
        "stage": "test",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    if passed:
        print(f"[SUCCESS] Test stage passed: {result.testsRun} tests ran clean.")
        return 0
    else:
        print(f"[FAILURE] Test stage failed: {len(result.failures)} failures, {len(result.errors)} errors.")
        return 1


def main() -> int:
    """Main CLI entrypoint for test stage."""
    return run_tests_and_record(Path.cwd())


if __name__ == "__main__":
    sys.exit(main())
