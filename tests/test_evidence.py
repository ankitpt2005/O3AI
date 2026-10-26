"""Tests for evidence recording, checklist verification, and status derivation."""

import tempfile
import unittest
from pathlib import Path
from o3ai.evidence import (
    FLOOR_CHECKLISTS,
    EvidenceError,
    derive_all_floor_statuses,
    derive_floor_status,
    get_floor_checklist,
    load_evidence,
    verify_stage_evidence,
    write_evidence,
)
from o3ai.models import StageStatus


class TestEvidence(unittest.TestCase):
    """Test deterministic evidence verification and status derivation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_floor_checklists_defined(self) -> None:
        """All 4 floors must have explicit checklist definitions."""
        for floor in ["planner", "builder", "security_qa", "release_monitor"]:
            checklist = get_floor_checklist(floor)
            self.assertTrue(len(checklist) >= 2)

    def test_status_pending_when_file_missing(self) -> None:
        """Derived status is PENDING when results/<stage>.json does not exist."""
        status = derive_floor_status("planner", self.results_dir)
        self.assertEqual(status, StageStatus.PENDING)

    def test_stage_name_path_traversal_rejected(self) -> None:
        """Malicious stage names with path traversal must raise EvidenceError."""
        with self.assertRaises(EvidenceError):
            write_evidence("../traversal", "PASS", "bad", {}, self.results_dir)
        with self.assertRaises(EvidenceError):
            load_evidence("../../evil", self.results_dir)

    def test_status_needs_evidence_when_fields_missing(self) -> None:
        """Derived status is NEEDS_EVIDENCE when evidence is empty or missing checklist keys."""
        # Missing required checklist keys for planner
        write_evidence("planner", "PASS", "Incomplete", {"task_count": 1}, self.results_dir)
        status = derive_floor_status("planner", self.results_dir)
        self.assertEqual(status, StageStatus.NEEDS_EVIDENCE)

    def test_status_pass_when_checklist_satisfied(self) -> None:
        """Derived status is PASS when status is PASS and all checklist metrics pass."""
        # Planner PASS
        write_evidence(
            "planner",
            "PASS",
            "Planner completed",
            {"tasks": [{"task_id": "1"}], "task_count": 1, "is_dag": True},
            self.results_dir,
        )
        self.assertEqual(derive_floor_status("planner", self.results_dir), StageStatus.PASS)

        # Builder PASS
        write_evidence(
            "builder",
            "PASS",
            "Build clean",
            {"target_files": ["sample_app/signup.py"], "exit_code": 0},
            self.results_dir,
        )
        self.assertEqual(derive_floor_status("builder", self.results_dir), StageStatus.PASS)

        # Security & QA PASS
        write_evidence(
            "security_qa",
            "PASS",
            "Security clean",
            {"secrets_found": 0, "tests_passed": True},
            self.results_dir,
        )
        self.assertEqual(derive_floor_status("security_qa", self.results_dir), StageStatus.PASS)

        # Release & Monitor PASS
        write_evidence(
            "release_monitor",
            "PASS",
            "Release ready",
            {"version": "1.0.0", "rollback_defined": True},
            self.results_dir,
        )
        self.assertEqual(derive_floor_status("release_monitor", self.results_dir), StageStatus.PASS)

    def test_status_fail_when_metric_or_status_fails(self) -> None:
        """Derived status is FAIL when status is FAIL or metric check fails."""
        # Status field is FAIL
        write_evidence("builder", "FAIL", "Failed build", {"target_files": [], "exit_code": 1}, self.results_dir)
        self.assertEqual(derive_floor_status("builder", self.results_dir), StageStatus.FAIL)

        # Security found secrets
        write_evidence(
            "security_qa",
            "PASS",
            "Failed secrets",
            {"secrets_found": 2, "tests_passed": True},
            self.results_dir,
        )
        self.assertEqual(derive_floor_status("security_qa", self.results_dir), StageStatus.FAIL)

    def test_derive_all_floor_statuses(self) -> None:
        """derive_all_floor_statuses returns dictionary of all 4 floor statuses."""
        statuses = derive_all_floor_statuses(self.results_dir)
        self.assertEqual(len(statuses), 4)
        for stage in ["planner", "builder", "security_qa", "release_monitor"]:
            self.assertIn(stage, statuses)
            self.assertEqual(statuses[stage], StageStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
