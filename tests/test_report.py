"""Tests for Working Tower evidence report and HTML dashboard generation."""

import tempfile
import unittest
from pathlib import Path
from o3ai.evidence import write_evidence
from o3ai.report import compute_metrics, generate_reports


class TestReport(unittest.TestCase):
    """Test dynamically computed metrics, report.md, and tower.html generation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_metrics_computed_dynamically_when_empty(self) -> None:
        """When no results exist, all 4 floors are pending and completion is 0%."""
        metrics = compute_metrics(self.results_dir)
        self.assertEqual(metrics["total_floors"], 4)
        self.assertEqual(metrics["passed_count"], 0)
        self.assertEqual(metrics["pending_count"], 4)
        self.assertEqual(metrics["completion_pct"], 0)
        self.assertFalse(metrics["all_passed"])

    def test_metrics_computed_dynamically_partial_pass(self) -> None:
        """When 2 floors pass, metrics show 2/4 (50%) completion."""
        write_evidence(
            "planner",
            "PASS",
            "Planner pass",
            {"tasks": [{"task_id": "1"}], "task_count": 1, "is_dag": True},
            self.results_dir,
        )
        write_evidence(
            "builder",
            "PASS",
            "Builder pass",
            {"target_files": ["sample_app/signup.py"], "exit_code": 0},
            self.results_dir,
        )
        # Security QA has missing evidence -> NEEDS_EVIDENCE
        write_evidence("security_qa", "PASS", "Incomplete", {}, self.results_dir)

        metrics = compute_metrics(self.results_dir)
        self.assertEqual(metrics["passed_count"], 2)
        self.assertEqual(metrics["needs_evidence_count"], 1)
        self.assertEqual(metrics["pending_count"], 1)
        self.assertEqual(metrics["completion_pct"], 50)
        self.assertFalse(metrics["all_passed"])

    def test_generate_reports_outputs_files(self) -> None:
        """generate_reports must write both results/tower.html and results/report.md."""
        tower_html, report_md = generate_reports(self.results_dir)
        self.assertTrue(tower_html.exists())
        self.assertTrue(report_md.exists())

        html_text = tower_html.read_text(encoding="utf-8")
        self.assertIn("O3AI Working Tower Dashboard", html_text)
        self.assertIn("0/4", html_text)

        md_text = report_md.read_text(encoding="utf-8")
        self.assertIn("O3AI Working Tower: Evidence Report", md_text)
        self.assertIn("Human Approval Gate: BLOCKED", md_text)

    def test_all_floors_pass_triggers_ready_human_gate(self) -> None:
        """When all 4 floors pass checklist, human gate is READY FOR DEPLOYMENT."""
        write_evidence(
            "planner", "PASS", "OK",
            {"tasks": [{"task_id": "1"}], "task_count": 1, "is_dag": True},
            self.results_dir,
        )
        write_evidence(
            "builder", "PASS", "OK",
            {"target_files": ["f.py"], "exit_code": 0},
            self.results_dir,
        )
        write_evidence(
            "security_qa", "PASS", "OK",
            {"secrets_found": 0, "tests_passed": True},
            self.results_dir,
        )
        write_evidence(
            "release_monitor", "PASS", "OK",
            {"version": "1.0.0", "rollback_defined": True},
            self.results_dir,
        )

        metrics = compute_metrics(self.results_dir)
        self.assertEqual(metrics["passed_count"], 4)
        self.assertEqual(metrics["completion_pct"], 100)
        self.assertTrue(metrics["all_passed"])

        tower_html, report_md = generate_reports(self.results_dir)
        md_text = report_md.read_text(encoding="utf-8")
        self.assertIn("Human Approval Gate: READY FOR DEPLOYMENT", md_text)
        self.assertIn("/approve", md_text)

        html_text = tower_html.read_text(encoding="utf-8")
        self.assertIn("4/4", html_text)
        self.assertIn("100%", html_text)


if __name__ == "__main__":
    unittest.main()
