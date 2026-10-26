"""Tests for MR security and QA review comment generation."""

import tempfile
import unittest
from pathlib import Path
from o3ai.evidence import write_evidence
from scripts.mr_review_comment import generate_mr_review_comment


class TestMRReviewComment(unittest.TestCase):
    """Test MR comment generation and security evaluation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.results_dir = self.workspace / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mr_comment_approved_when_clean(self) -> None:
        """When security and builder pass, comment contains APPROVED and /approve."""
        write_evidence(
            "planner", "PASS", "Plan OK",
            {"tasks": [{"task_id": "1"}], "task_count": 1, "is_dag": True},
            self.results_dir,
        )
        write_evidence(
            "builder", "PASS", "Build OK",
            {"target_files": ["f.py"], "exit_code": 0},
            self.results_dir,
        )
        write_evidence(
            "security_qa", "PASS", "Security clean",
            {"secrets_found": 0, "tests_passed": True},
            self.results_dir,
        )

        comment, approved = generate_mr_review_comment(self.workspace)
        self.assertTrue(approved)
        self.assertIn("APPROVED", comment)
        self.assertIn("/approve", comment)
        self.assertIn("O3AI Security & QA Gate", comment)

    def test_mr_comment_changes_required_when_unclean(self) -> None:
        """When security fails, comment requires changes."""
        write_evidence(
            "security_qa", "FAIL", "Secrets found",
            {"secrets_found": 3, "tests_passed": False},
            self.results_dir,
        )
        comment, approved = generate_mr_review_comment(self.workspace)
        self.assertFalse(approved)
        self.assertIn("CHANGES REQUIRED", comment)


if __name__ == "__main__":
    unittest.main()
