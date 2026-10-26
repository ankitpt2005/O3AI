"""Tests for Working Tower building, floor specifications, and DAG validation."""

import tempfile
import unittest
from pathlib import Path
from o3ai.evidence import write_evidence
from o3ai.models import Floor, GitLabIssue, StageStatus
from o3ai.session import create_goal_session
from o3ai.tower import PlanTask, WorkingTower, build_tower, is_acyclic_dag


class TestTower(unittest.TestCase):
    """Test 4-floor Working Tower specifications, agents, tasks, and execution."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.tmp.name)
        issue = GitLabIssue(
            issue_id=1,
            title="Implement email verification in signup",
            description="Validate email format using standard regex.",
        )
        self.session = create_goal_session(issue, self.results_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_tower_has_4_floors_with_named_agents_and_tasks(self) -> None:
        """build_tower must produce 4 floors each with named agents, tasks, and statuses."""
        tower = build_tower(self.session, self.results_dir / "results")
        self.assertEqual(len(tower.floors), 4)

        expected_floors = ["planner", "builder", "security_qa", "release_monitor"]
        for i, floor_name in enumerate(expected_floors):
            floor_spec = tower.floors[i]
            self.assertEqual(floor_spec.floor.value, floor_name)
            self.assertTrue(len(floor_spec.agents) >= 2, f"Floor {floor_name} missing agents")
            self.assertTrue(len(floor_spec.tasks) >= 2, f"Floor {floor_name} missing tasks")
            self.assertEqual(floor_spec.status, StageStatus.PENDING)

            for task in floor_spec.tasks:
                self.assertTrue(bool(task.task_id))
                self.assertTrue(bool(task.name))
                self.assertTrue(bool(task.description))
                self.assertIsInstance(task.dependencies, list)

    def test_build_tower_reflects_derived_status(self) -> None:
        """Floor statuses in build_tower dynamically reflect results/*.json."""
        target_results = self.results_dir / "results"
        # Record passing evidence for Planner
        write_evidence(
            "planner",
            "PASS",
            "Planner passed",
            {"tasks": [{"task_id": "1"}], "task_count": 1, "is_dag": True},
            target_results,
        )
        tower = build_tower(self.session, target_results)
        planner_floor = tower.get_floor("planner")
        self.assertIsNotNone(planner_floor)
        self.assertEqual(planner_floor.status, StageStatus.PASS)

        builder_floor = tower.get_floor("builder")
        self.assertIsNotNone(builder_floor)
        self.assertEqual(builder_floor.status, StageStatus.PENDING)

    def test_dag_acyclic_and_cyclic_detection(self) -> None:
        """Validate DAG acyclic detection algorithm."""
        acyclic_tasks = [
            PlanTask(task_id="t1", description="Init"),
            PlanTask(task_id="t2", description="Code", dependencies=["t1"]),
            PlanTask(task_id="t3", description="Test", dependencies=["t2"]),
        ]
        self.assertTrue(is_acyclic_dag(acyclic_tasks))

        cyclic_tasks = [
            PlanTask(task_id="t1", description="A", dependencies=["t2"]),
            PlanTask(task_id="t2", description="B", dependencies=["t1"]),
        ]
        self.assertFalse(is_acyclic_dag(cyclic_tasks))


if __name__ == "__main__":
    unittest.main()
