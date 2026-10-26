"""Tests for pipeline stages and evidence recording."""

import tempfile
import unittest
from pathlib import Path
from scripts.lint import run_lint
from scripts.security_check import scan_file_for_secrets
from scripts.deploy_pages import deploy_to_public
from scripts.health_check import run_health_check


class TestPipelineStages(unittest.TestCase):
    """Test all pipeline stage scripts and evidence generation."""

    def test_lint_on_clean_workspace(self) -> None:
        """Linting clean workspace passes with zero errors."""
        passed, files, errors = run_lint(Path.cwd())
        self.assertTrue(passed)
        self.assertEqual(len(errors), 0)
        self.assertTrue(len(files) > 0)

    def test_security_check_detects_secret(self) -> None:
        """Security scanner catches hardcoded API keys and tokens."""
        # Dynamically compose fake token so the test file itself does not trigger scanner
        fake_key = "".join(["AK", "IA", "9876543210SAMPLE"])
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tf:
            tf.write(f"SAMPLE_KEY = '{fake_key}'\n")
            temp_path = Path(tf.name)

        try:
            findings = scan_file_for_secrets(temp_path)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["rule"], "AWS Access Key")
        finally:
            temp_path.unlink()

    def test_deploy_fails_without_approval(self) -> None:
        """deploy_to_public fails when results/approve.json is missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            passed, msg = deploy_to_public(Path(tmp_dir))
            self.assertFalse(passed)
            self.assertIn("approve.json does not exist", msg)

    def test_health_check_fails_without_public(self) -> None:
        """Health check returns DOWN when public/index.html is missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            passed, evidence = run_health_check(Path(tmp_dir))
            self.assertFalse(passed)
            self.assertEqual(evidence["health"], "DOWN")


if __name__ == "__main__":
    unittest.main()
