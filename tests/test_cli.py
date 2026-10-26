"""Tests for O3AI CLI entrypoint."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from o3ai.cli import run_generate_report, run_init_session


class TestCli(unittest.TestCase):
    """Test command line execution and error reporting."""

    def test_run_init_session_success(self) -> None:
        """Valid inputs must succeed with returncode 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("pathlib.Path.cwd", return_value=Path(tmp_dir)):
                code = run_init_session(
                    issue_id=7,
                    title="Setup automated testing pipeline",
                    description="Run unit tests on every merge request.",
                )
                self.assertEqual(code, 0)
                session_path = Path(tmp_dir) / "results" / "session.json"
                self.assertTrue(session_path.exists())

    def test_run_init_session_security_violation(self) -> None:
        """Violating security constraints must return exit code 1."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("pathlib.Path.cwd", return_value=Path(tmp_dir)):
                code = run_init_session(
                    issue_id=8,
                    title="   ",
                    description="",
                )
                self.assertEqual(code, 1)

    def test_run_generate_report(self) -> None:
        """run_generate_report generates tower.html and report.md with exit code 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("pathlib.Path.cwd", return_value=Path(tmp_dir)):
                code = run_generate_report()
                self.assertEqual(code, 0)
                self.assertTrue((Path(tmp_dir) / "results" / "tower.html").exists())
                self.assertTrue((Path(tmp_dir) / "results" / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
