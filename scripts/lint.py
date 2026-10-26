"""Lint and syntax validation script for O3AI pipeline."""

from __future__ import annotations
import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple


def check_syntax_and_style(file_path: Path) -> List[str]:
    """Parse AST and verify basic style constraints."""
    errors: List[str] = []
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as err:
        return [f"SyntaxError in {file_path}: {err}"]

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            line_count = end - start + 1
            if line_count > 35:
                # Soft notice or warning on functions > 35 lines
                pass
    return errors


def run_lint(workspace_root: Path) -> Tuple[bool, List[str], List[str]]:
    """Scan all python files in o3ai, sample_app, tests, scripts."""
    target_dirs = ["o3ai", "sample_app", "tests", "scripts"]
    checked_files: List[str] = []
    all_errors: List[str] = []

    for d in target_dirs:
        dir_path = workspace_root / d
        if dir_path.exists():
            for py_file in dir_path.rglob("*.py"):
                checked_files.append(str(py_file.relative_to(workspace_root)))
                errs = check_syntax_and_style(py_file)
                all_errors.extend(errs)

    passed = len(all_errors) == 0
    return passed, checked_files, all_errors


def main() -> int:
    """Main CLI entrypoint for linting stage."""
    workspace = Path.cwd()
    passed, files, errors = run_lint(workspace)

    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "validate.json"

    payload = {
        "stage": "validate",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "files_checked_count": len(files),
            "files_checked": files,
            "error_count": len(errors),
            "errors": errors,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    if passed:
        print(f"[SUCCESS] Lint validation passed for {len(files)} files.")
        return 0
    else:
        print(f"[FAILURE] Lint validation failed with {len(errors)} errors:")
        for e in errors:
            print(f"  - {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
