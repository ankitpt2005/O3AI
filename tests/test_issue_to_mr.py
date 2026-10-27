"""Tests for offline simulation of the Duo flow for reproducible tests (Issue -> Planner -> MR)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from o3ai.models import GitLabIssue
from scripts.issue_to_mr import run_issue_to_mr


class TestIssueToMR(unittest.TestCase):
    """Test offline simulation of the Duo flow for reproducible tests."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        # Seed sample_app/signup.py
        app_dir = self.workspace / "sample_app"
        app_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path.cwd() / "sample_app" / "signup.py", app_dir / "signup.py")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_issue_to_mr_generates_artifacts(self) -> None:
        """Flow produces MR payload, diff file, and updates signup validator."""
        issue = GitLabIssue(
            issue_id=55,
            title="Add email and password validation to signup",
            description="Enforce valid email regex and password length >= 8.",
        )
        mr = run_issue_to_mr(issue, self.workspace)
        self.assertEqual(mr["status"], "DRAFT_MR_READY")
        self.assertIn("Draft: Resolve #55", mr["title"])

        # Check artifacts
        results_dir = self.workspace / "results"
        self.assertTrue((results_dir / "mr_payload.json").exists())
        self.assertTrue((results_dir / "mr_patch.diff").exists())
        self.assertTrue((results_dir / "builder.json").exists())

        # Check patched signup validator code
        patched_file = self.workspace / "sample_app" / "signup.py"
        code = patched_file.read_text(encoding="utf-8")
        self.assertIn("EMAIL_REGEX", code)
        self.assertIn("Invalid email address format", code)
        self.assertIn("Password must be at least 8 characters", code)


if __name__ == "__main__":
    unittest.main()
