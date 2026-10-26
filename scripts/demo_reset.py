"""Reset script to restore sample_app/signup.py to its initial broken state for demo re-recording."""

from __future__ import annotations
import shutil
from pathlib import Path

BROKEN_SIGNUP_CODE = '''"""Sample Application: User Signup Validator.

Deliberately missing email format validation and password length checking.
Will be enhanced through O3AI Working Tower flow.
"""

from __future__ import annotations
from typing import Dict, List, Tuple


def validate_signup(data: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Validate user signup payload."""
    errors: List[str] = []

    username = data.get("username", "").strip()
    if not username:
        errors.append("Username is required.")
    elif len(username) < 3:
        errors.append("Username must be at least 3 characters.")

    email = data.get("email", "").strip()
    if not email:
        errors.append("Email is required.")
    # Intentionally missing regex email check

    password = data.get("password", "")
    if not password:
        errors.append("Password is required.")
    # Intentionally missing min length check

    return len(errors) == 0, errors
'''


def reset_demo_state(workspace: Path | None = None) -> None:
    """Restore sample_app/signup.py to broken state and clean demo output artifacts."""
    if workspace is None:
        workspace = Path(__file__).resolve().parent.parent

    # 1. Reset sample_app/signup.py
    signup_file = workspace / "sample_app" / "signup.py"
    signup_file.parent.mkdir(parents=True, exist_ok=True)
    signup_file.write_text(BROKEN_SIGNUP_CODE, encoding="utf-8")

    # 2. Clean transient demo artifacts
    results_dir = workspace / "results"
    if results_dir.exists():
        demo_artifacts = [
            "builder.json",
            "mr_patch.diff",
            "mr_payload.json",
            "mr_review.json",
            "mr_review.md",
            "approve.json",
            "deploy.json",
            "monitor.json",
            "tower_report.json",
            "tower.html",
            "report.md",
            "test.json",
        ]
        for name in demo_artifacts:
            target = results_dir / name
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass

    # 3. Clean public/ deployment folder
    public_dir = workspace / "public"
    if public_dir.exists():
        shutil.rmtree(public_dir, ignore_errors=True)

    print("[RESET COMPLETE] Demo state restored to initial broken baseline:")
    print("  - sample_app/signup.py: missing email regex & password >= 8 checks")
    print("  - results/ and public/ artifacts cleaned for fresh run")
    print("  - Run 'python scripts/run_pipeline.py' -> Observe RED pipeline")


if __name__ == "__main__":
    reset_demo_state()
