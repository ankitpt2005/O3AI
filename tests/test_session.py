"""Tests for Goal Session lifecycle, ProblemStatement derivation, and edge cases."""

import tempfile
import unittest
from pathlib import Path
from o3ai.models import Floor, GitLabIssue, StageStatus
from o3ai.security import SecurityValidationError
from o3ai.session import (
    create_goal_session,
    derive_problem_statement,
    record_stage_evidence,
    verify_stage_evidence,
)


class TestSession(unittest.TestCase):
    """Test session lifecycle, rule-based extraction, and edge cases."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_goal_title_rejected(self) -> None:
        """Edge case: Empty or whitespace-only title must raise SecurityValidationError."""
        with self.assertRaises(SecurityValidationError):
            derive_problem_statement("", "Valid description of the task.")
        with self.assertRaises(SecurityValidationError):
            derive_problem_statement("   ", "Valid description of the task.")

    def test_empty_goal_text_rejected(self) -> None:
        """Edge case: Empty or whitespace-only goal text must raise SecurityValidationError."""
        with self.assertRaises(SecurityValidationError):
            derive_problem_statement("Valid title here", "")
        with self.assertRaises(SecurityValidationError):
            derive_problem_statement("Valid title here", "   \n\t  ")

    def test_36_word_title_rejected_and_35_word_passes(self) -> None:
        """Edge case: 36 words must fail; 35 words must pass."""
        words_35 = " ".join([f"word{i}" for i in range(35)])
        words_36 = " ".join([f"word{i}" for i in range(36)])

        # 35 words passes
        statement = derive_problem_statement(words_35, "Valid goal details.")
        self.assertEqual(statement.what, words_35)

        # 36 words fails
        with self.assertRaises(SecurityValidationError) as ctx:
            derive_problem_statement(words_36, "Valid goal details.")
        self.assertIn("exceeds limit", str(ctx.exception))

    def test_unicode_input_handling(self) -> None:
        """Edge case: Unicode characters, foreign alphabets, and emojis are preserved."""
        unicode_title = "Améliorer l'authentification 🚀 et vérifier 日本語"
        unicode_body = "Détails: validation complète du système avec accents et caractères spéciaux."
        statement = derive_problem_statement(unicode_title, unicode_body)
        self.assertIn("Améliorer", statement.what)
        self.assertIn("🚀", statement.what)
        self.assertIn("日本語", statement.what)
        self.assertIn("Détails", statement.why)

    def test_huge_input_handled_safely(self) -> None:
        """Edge case: Huge input (>100KB) is handled safely without hanging."""
        huge_text = "Task details line.\n" * 10000  # ~190,000 characters
        statement = derive_problem_statement("Process large input data", huge_text)
        self.assertEqual(statement.what, "Process large input data")
        self.assertTrue(len(statement.why) > 0)

    def test_parse_gitlab_template_sections(self) -> None:
        """ProblemStatement extracts What, Why, When/Where, Constraints, Done-when."""
        template_body = """
### Goal
Sanitize all user inputs before rendering in web template.

### Why
Prevent Cross-Site Scripting (XSS) attacks across the application.

### Where/When
sample_app/signup.py and related presentation layer templates.

### Constraints
- Python 3.12 standard library only
- Zero external dependencies
- Sub-millisecond response time

### Done-when
- [ ] Working Tower Floor 1: Task decomposition
- [ ] Working Tower Floor 2: Implementation complete
- [ ] Working Tower Floor 3: Security checks clean
"""
        ps = derive_problem_statement("Sanitize all user inputs", template_body)
        self.assertEqual(ps.what, "Sanitize all user inputs before rendering in web template.")
        self.assertIn("Prevent Cross-Site Scripting", ps.why)
        self.assertIn("sample_app/signup.py", ps.when_where)
        self.assertEqual(len(ps.constraints), 3)
        self.assertEqual(len(ps.done_when), 3)

    def test_create_goal_session(self) -> None:
        """create_goal_session writes session.json into results/ directory."""
        issue = GitLabIssue(
            issue_id=101,
            title="Implement input sanitization middleware",
            description="Sanitize all HTTP body strings before processing.",
        )
        session = create_goal_session(issue, self.workspace)
        self.assertTrue(session.session_id.startswith("session-issue-101-"))
        self.assertEqual(session.problem_statement.what, "Implement input sanitization middleware")

        session_file = self.workspace / "results" / "session.json"
        self.assertTrue(session_file.exists())


if __name__ == "__main__":
    unittest.main()
