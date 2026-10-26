"""Tests for O3AI core dataclasses and serialization."""

import unittest
from o3ai.models import (
    EvidenceQuestion,
    Floor,
    FloorSpec,
    FloorTask,
    GitLabIssue,
    GoalSession,
    ProblemStatement,
    StageResult,
    StageStatus,
)


class TestModels(unittest.TestCase):
    """Test serialization, deserialization, and integrity of models."""

    def test_gitlab_issue_roundtrip(self) -> None:
        """GitLabIssue must correctly serialize to dict and restore from dict."""
        issue = GitLabIssue(
            issue_id=42,
            title="Implement user auth token revocation endpoint",
            description="Revoke tokens immediately upon password reset.",
            author="security_lead",
            labels=["security", "backend"],
        )
        data = issue.to_dict()
        restored = GitLabIssue.from_dict(data)
        self.assertEqual(issue, restored)

    def test_problem_statement_roundtrip_new_fields(self) -> None:
        """ProblemStatement roundtrips What, Why, When/Where, Constraints, Done-when."""
        statement = ProblemStatement(
            what="Fix race condition in payment webhook",
            why="Handle concurrent webhook events cleanly.",
            when_where="Billing service handlers",
            constraints=["Billing service only", "Stdlib only"],
            done_when=["Zero double-charges", "100% idempotency"],
        )
        data = statement.to_dict()
        restored = ProblemStatement.from_dict(data)
        self.assertEqual(statement, restored)
        self.assertEqual(restored.what, "Fix race condition in payment webhook")
        self.assertEqual(restored.raw_goal, "Fix race condition in payment webhook")

    def test_problem_statement_legacy_dict_compatibility(self) -> None:
        """ProblemStatement from_dict handles legacy keys seamlessly."""
        legacy_data = {
            "raw_goal": "Add metrics endpoint",
            "synthesized_problem": "Export Prometheus metrics.",
            "scope_boundaries": ["Metrics only"],
            "target_outcomes": ["200 OK on /metrics"],
        }
        restored = ProblemStatement.from_dict(legacy_data)
        self.assertEqual(restored.what, "Add metrics endpoint")
        self.assertEqual(restored.constraints, ["Metrics only"])

    def test_floor_task_and_floor_spec(self) -> None:
        """FloorTask and FloorSpec serialize and deserialize cleanly."""
        task = FloorTask(
            task_id="t1",
            name="Decompose",
            description="Decompose goal",
            dependencies=[],
            status=StageStatus.PASS,
        )
        self.assertEqual(task.status, StageStatus.PASS)
        task_dict = task.to_dict()
        self.assertEqual(task_dict["status"], "PASS")

        spec = FloorSpec(
            floor=Floor.PLANNER,
            name="Floor 1: Planner",
            agents=["PlannerAgent"],
            tasks=[task],
            status=StageStatus.PASS,
            evidence_file="results/planner.json",
        )
        spec_dict = spec.to_dict()
        self.assertEqual(spec_dict["floor"], "planner")
        self.assertEqual(len(spec_dict["agents"]), 1)

    def test_evidence_question_and_stage_result(self) -> None:
        """EvidenceQuestion and StageResult must strictly validate enums."""
        q = EvidenceQuestion(
            question_id="EV-1",
            floor=Floor.PLANNER,
            question="Is DAG acyclic?",
            expected_metric="is_dag == True",
        )
        self.assertEqual(q.floor, Floor.PLANNER)
        restored_q = EvidenceQuestion.from_dict(q.to_dict())
        self.assertEqual(restored_q.floor, Floor.PLANNER)

        result = StageResult(
            stage=Floor.BUILDER,
            status=StageStatus.PASS,
            summary="All tests passed",
            evidence={"test_count": 12, "failures": 0},
            timestamp="2026-10-25T14:00:00Z",
        )
        restored_res = StageResult.from_dict(result.to_dict())
        self.assertEqual(restored_res.status, StageStatus.PASS)
        self.assertEqual(restored_res.evidence["failures"], 0)


if __name__ == "__main__":
    unittest.main()
