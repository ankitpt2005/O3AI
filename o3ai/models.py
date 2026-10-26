"""Core data models for O3AI Goal Sessions and Working Tower floors.

All models are strictly typed and serialize to JSON standard formats.
"""

from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List


class Floor(str, Enum):
    """The four floors of the O3AI Working Tower."""

    PLANNER = "planner"
    BUILDER = "builder"
    SECURITY_QA = "security_qa"
    RELEASE_MONITOR = "release_monitor"


class StageStatus(str, Enum):
    """Derived status for tower stages and evidence verification."""

    PENDING = "PENDING"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class GitLabIssue:
    """Represents an untrusted GitLab issue payload."""

    issue_id: int
    title: str
    description: str
    author: str = "gitlab_user"
    labels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert issue to serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GitLabIssue:
        """Instantiate GitLabIssue from dictionary."""
        return cls(
            issue_id=int(data["issue_id"]),
            title=str(data["title"]),
            description=str(data.get("description", "")),
            author=str(data.get("author", "gitlab_user")),
            labels=list(data.get("labels", [])),
        )


@dataclass(frozen=True)
class ProblemStatement:
    """Structured Problem Statement: What, Why, When/Where, Constraints, Done-when."""

    what: str
    why: str
    when_where: str
    constraints: List[str]
    done_when: List[str]

    @property
    def raw_goal(self) -> str:
        """Backward-compatible alias for what."""
        return self.what

    @property
    def synthesized_problem(self) -> str:
        """Backward-compatible alias combining what and why."""
        return f"Goal: {self.what}. Details: {self.why}"

    @property
    def scope_boundaries(self) -> List[str]:
        """Backward-compatible alias for constraints."""
        return self.constraints

    @property
    def target_outcomes(self) -> List[str]:
        """Backward-compatible alias for done_when."""
        return self.done_when

    def to_dict(self) -> Dict[str, Any]:
        """Convert statement to serializable dictionary."""
        return {
            "what": self.what,
            "why": self.why,
            "when_where": self.when_where,
            "constraints": self.constraints,
            "done_when": self.done_when,
            # Legacy keys preserved for backward compatibility
            "raw_goal": self.raw_goal,
            "synthesized_problem": self.synthesized_problem,
            "scope_boundaries": self.scope_boundaries,
            "target_outcomes": self.target_outcomes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProblemStatement:
        """Instantiate ProblemStatement supporting both new and legacy formats."""
        what = data.get("what") or data.get("raw_goal", "")
        why = data.get("why") or data.get("synthesized_problem", "")
        when_where = data.get("when_where", "Current workspace / repository")
        constraints = data.get("constraints") or data.get("scope_boundaries", [])
        done_when = data.get("done_when") or data.get("target_outcomes", [])
        return cls(
            what=str(what),
            why=str(why),
            when_where=str(when_where),
            constraints=list(constraints),
            done_when=list(done_when),
        )


@dataclass(frozen=True)
class EvidenceQuestion:
    """Specific question requiring verifiable evidence before passing."""

    question_id: str
    floor: Floor
    question: str
    expected_metric: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert question to serializable dictionary."""
        return {
            "question_id": self.question_id,
            "floor": self.floor.value,
            "question": self.question,
            "expected_metric": self.expected_metric,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceQuestion:
        """Instantiate EvidenceQuestion from dictionary."""
        return cls(
            question_id=str(data["question_id"]),
            floor=Floor(data["floor"]),
            question=str(data["question"]),
            expected_metric=str(data["expected_metric"]),
        )


@dataclass
class FloorTask:
    """A discrete task belonging to a floor."""

    task_id: str
    name: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    status: StageStatus = StageStatus.PENDING

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "dependencies": self.dependencies,
            "status": self.status.value,
        }


@dataclass
class FloorSpec:
    """Specification of a floor in the Working Tower."""

    floor: Floor
    name: str
    agents: List[str]
    tasks: List[FloorTask]
    status: StageStatus = StageStatus.PENDING
    evidence_file: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert floor specification to dictionary."""
        return {
            "floor": self.floor.value,
            "name": self.name,
            "agents": self.agents,
            "tasks": [t.to_dict() for t in self.tasks],
            "status": self.status.value,
            "evidence_file": self.evidence_file,
        }


@dataclass
class StageResult:
    """Deterministic evidence record for a Working Tower floor."""

    stage: Floor
    status: StageStatus
    summary: str
    evidence: Dict[str, Any]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary."""
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "summary": self.summary,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StageResult:
        """Instantiate StageResult from dictionary."""
        return cls(
            stage=Floor(data["stage"]),
            status=StageStatus(data["status"]),
            summary=str(data["summary"]),
            evidence=dict(data.get("evidence", {})),
            timestamp=str(data["timestamp"]),
        )


@dataclass
class GoalSession:
    """A 1:1 mapping of a validated GitLab issue to an active Goal Session."""

    session_id: str
    issue: GitLabIssue
    problem_statement: ProblemStatement
    evidence_questions: List[EvidenceQuestion]
    status: str = "INITIALIZED"
    stage_results: Dict[str, StageResult] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert session to serializable dictionary."""
        return {
            "session_id": self.session_id,
            "issue": self.issue.to_dict(),
            "problem_statement": self.problem_statement.to_dict(),
            "evidence_questions": [q.to_dict() for q in self.evidence_questions],
            "status": self.status,
            "stage_results": {
                k: v.to_dict() for k, v in self.stage_results.items()
            },
        }
