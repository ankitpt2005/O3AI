"""The Working Tower: 4-Floor architecture for O3AI.

Floors:
  1. Planner: Task decomposition and DAG dependency validation.
  2. Builder: Code patch generation and compilation/syntax checks.
  3. Security & QA: Secret scanning, injection checks, and test runner.
  4. Release & Monitor: Deployment readiness, manifests, and rollback plan.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from o3ai.evidence import (
    derive_all_floor_statuses,
    derive_floor_status,
    verify_stage_evidence,
    write_evidence,
)
from o3ai.models import Floor, FloorSpec, FloorTask, GoalSession, StageStatus


@dataclass
class PlanTask:
    """A discrete, verifiable unit of work."""

    task_id: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "task_id": self.task_id,
            "name": self.name or self.task_id,
            "description": self.description,
            "dependencies": self.dependencies,
        }


def is_acyclic_dag(tasks: List[PlanTask]) -> bool:
    """Return True if tasks form a valid Directed Acyclic Graph (DAG)."""
    task_map = {t.task_id: t for t in tasks}
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def has_cycle(curr_id: str) -> bool:
        visited.add(curr_id)
        rec_stack.add(curr_id)
        task = task_map.get(curr_id)
        if task:
            for dep in task.dependencies:
                if dep not in visited and has_cycle(dep):
                    return True
                elif dep in rec_stack:
                    return True
        rec_stack.remove(curr_id)
        return False

    for task_id in task_map:
        if task_id not in visited and has_cycle(task_id):
            return False
    return True


@dataclass
class Tower:
    """Represents the complete 4-floor Working Tower built for a GoalSession."""

    session: GoalSession
    floors: List[FloorSpec]
    results_dir: Path

    def get_floor(self, floor_name: str) -> Optional[FloorSpec]:
        """Retrieve a specific floor specification by name or enum string."""
        for f in self.floors:
            if f.floor.value == floor_name:
                return f
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert tower to serializable dictionary."""
        return {
            "session_id": self.session.session_id,
            "floors": [f.to_dict() for f in self.floors],
        }


def build_tower(session: GoalSession, results_dir: Optional[Path] = None) -> Tower:
    """Build the 4 Working Tower floors with named agents, tasks, and derived statuses."""
    target_results = results_dir or Path("results")
    statuses = derive_all_floor_statuses(target_results)

    # Floor 1: Planner
    planner_tasks = [
        FloorTask(
            task_id="plan-1",
            name="Goal Decomposition",
            description=f"Decompose '{session.problem_statement.what}' into discrete tasks.",
            dependencies=[],
            status=statuses["planner"],
        ),
        FloorTask(
            task_id="plan-2",
            name="DAG Validation",
            description="Verify acyclic dependency graph for planned work.",
            dependencies=["plan-1"],
            status=statuses["planner"],
        ),
    ]
    floor_planner = FloorSpec(
        floor=Floor.PLANNER,
        name="Floor 1: Planner",
        agents=["PlannerAgent", "DAGValidatorAgent"],
        tasks=planner_tasks,
        status=statuses["planner"],
        evidence_file="results/planner.json",
    )

    # Floor 2: Builder
    builder_tasks = [
        FloorTask(
            task_id="bld-1",
            name="Patch Implementation",
            description="Implement minimal, standard-library solution.",
            dependencies=["plan-2"],
            status=statuses["builder"],
        ),
        FloorTask(
            task_id="bld-2",
            name="Unit Test Verification",
            description="Run unit tests and assert exit code 0.",
            dependencies=["bld-1"],
            status=statuses["builder"],
        ),
    ]
    floor_builder = FloorSpec(
        floor=Floor.BUILDER,
        name="Floor 2: Builder",
        agents=["CodeBuilderAgent", "TestGeneratorAgent"],
        tasks=builder_tasks,
        status=statuses["builder"],
        evidence_file="results/builder.json",
    )

    # Floor 3: Security & QA
    sec_tasks = [
        FloorTask(
            task_id="sec-1",
            name="Secret & Credential Scan",
            description="Enforce zero leaked API keys, tokens, or hardcoded credentials.",
            dependencies=["bld-2"],
            status=statuses["security_qa"],
        ),
        FloorTask(
            task_id="sec-2",
            name="Static Analysis & QA Runner",
            description="Ensure static checks pass with zero regression.",
            dependencies=["sec-1"],
            status=statuses["security_qa"],
        ),
    ]
    floor_sec = FloorSpec(
        floor=Floor.SECURITY_QA,
        name="Floor 3: Security & QA",
        agents=["SecretScannerAgent", "QARunnerAgent"],
        tasks=sec_tasks,
        status=statuses["security_qa"],
        evidence_file="results/security_qa.json",
    )

    # Floor 4: Release & Monitor
    release_tasks = [
        FloorTask(
            task_id="rel-1",
            name="Release Manifest Packaging",
            description="Compile release version and evidence checklist.",
            dependencies=["sec-2"],
            status=statuses["release_monitor"],
        ),
        FloorTask(
            task_id="rel-2",
            name="Rollback Definition",
            description="Document reproducible rollback sequence.",
            dependencies=["rel-1"],
            status=statuses["release_monitor"],
        ),
    ]
    floor_release = FloorSpec(
        floor=Floor.RELEASE_MONITOR,
        name="Floor 4: Release & Monitor",
        agents=["ReleasePackagerAgent", "DeployGateAgent"],
        tasks=release_tasks,
        status=statuses["release_monitor"],
        evidence_file="results/release_monitor.json",
    )

    return Tower(
        session=session,
        floors=[floor_planner, floor_builder, floor_sec, floor_release],
        results_dir=target_results,
    )


class WorkingTower:
    """Execution helper for running tower floors and recording evidence."""

    def __init__(self, results_dir: Path) -> None:
        self.results_dir = results_dir

    def run_planner(self, tasks: List[PlanTask]) -> Path:
        """Floor 1: Validate task graph and record planner evidence."""
        if not tasks:
            raise ValueError("Planner requires at least one task.")
        if not is_acyclic_dag(tasks):
            raise ValueError("Cyclic dependency detected in task plan.")

        evidence = {
            "task_count": len(tasks),
            "tasks": [t.to_dict() for t in tasks],
            "is_dag": True,
        }
        return write_evidence(
            stage="planner",
            status="PASS",
            summary=f"Plan decomposed into {len(tasks)} DAG tasks.",
            evidence=evidence,
            results_dir=self.results_dir,
        )

    def run_builder(self, target_files: List[str], build_exit_code: int) -> Path:
        """Floor 2: Verify build and record builder evidence."""
        verify_stage_evidence("planner", self.results_dir)
        status = "PASS" if build_exit_code == 0 else "FAIL"
        summary = "Build succeeded." if status == "PASS" else "Build failed."
        evidence = {
            "target_files": target_files,
            "exit_code": build_exit_code,
        }
        return write_evidence(
            stage="builder",
            status=status,
            summary=summary,
            evidence=evidence,
            results_dir=self.results_dir,
        )

    def run_security_qa(self, secrets_found: int, tests_passed: bool) -> Path:
        """Floor 3: Scan for secrets and verify QA test run."""
        verify_stage_evidence("builder", self.results_dir)
        passed = secrets_found == 0 and tests_passed
        status = "PASS" if passed else "FAIL"
        summary = "Security & QA passed." if passed else "Security/QA checks failed."
        evidence = {
            "secrets_found": secrets_found,
            "tests_passed": tests_passed,
        }
        return write_evidence(
            stage="security_qa",
            status=status,
            summary=summary,
            evidence=evidence,
            results_dir=self.results_dir,
        )

    def run_release_monitor(self, version: str, rollback_defined: bool) -> Path:
        """Floor 4: Prepare release manifest and verification gate."""
        verify_stage_evidence("security_qa", self.results_dir)
        status = "PASS" if rollback_defined else "FAIL"
        summary = "Release ready." if status == "PASS" else "Missing rollback plan."
        evidence = {
            "version": version,
            "rollback_defined": rollback_defined,
        }
        return write_evidence(
            stage="release_monitor",
            status=status,
            summary=summary,
            evidence=evidence,
            results_dir=self.results_dir,
        )
