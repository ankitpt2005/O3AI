"""Goal Session lifecycle and deterministic ProblemStatement parsing.

Enforces:
1. One GitLab issue = one Goal Session.
2. GoalSession(title<=35 words, goal text) -> ProblemStatement (What, Why, When/Where, Constraints, Done-when).
3. Rule-based, offline parsing with edge case validation.
"""

from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from o3ai.models import (
    EvidenceQuestion,
    Floor,
    GitLabIssue,
    GoalSession,
    ProblemStatement,
    StageResult,
    StageStatus,
)
from o3ai.security import (
    assert_safe_path,
    sanitize_untrusted_text,
    validate_goal_text,
    validate_issue_title,
)


DEFAULT_CONSTRAINTS = [
    "Python 3.12 standard library only",
    "Zero external secrets, API keys, or unauthorized dependencies",
    "Deterministic verification required in results/<stage>.json",
]

DEFAULT_DONE_WHEN = [
    "Floor 1 (Planner): Verified DAG task breakdown in results/planner.json",
    "Floor 2 (Builder): Validated code patch and unit tests in results/builder.json",
    "Floor 3 (Security & QA): Zero secrets found and tests pass in results/security_qa.json",
    "Floor 4 (Release & Monitor): Release manifest ready in results/release_monitor.json",
    "Human Approval: Human reviewer sign-off before deployment",
]


def extract_sections(text: str) -> Dict[str, str]:
    """Rule-based offline extraction of markdown sections from issue templates."""
    pattern = r"(?:^|\n)###?\s+([A-Za-z0-9_/ -]+)\s*\n"
    parts = re.split(pattern, text)
    sections: Dict[str, str] = {}

    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            sec_name = parts[i].strip().lower()
            sec_content = parts[i + 1].strip() if (i + 1) < len(parts) else ""
            sections[sec_name] = sec_content

    return sections


def parse_bullet_list(content: str) -> List[str]:
    """Extract list items from bulleted text lines."""
    items: List[str] = []
    for line in content.splitlines():
        cleaned = line.strip()
        if cleaned.startswith(("- [ ]", "- [x]", "-", "*")):
            item_text = re.sub(r"^[-*]\s*(\[[ x]\]\s*)?", "", cleaned).strip()
            if item_text and not item_text.startswith("<!--"):
                items.append(item_text)
    return items


def derive_problem_statement(title: str, goal_text: str) -> ProblemStatement:
    """Derive What, Why, When/Where, Constraints, Done-when rule-based and offline."""
    validate_issue_title(title)
    validate_goal_text(goal_text)

    clean_title = sanitize_untrusted_text(title)
    clean_goal = sanitize_untrusted_text(goal_text)
    sections = extract_sections(clean_goal)
    what_raw = sections.get("goal")
    if what_raw and what_raw.strip():
        validate_issue_title(what_raw)
        what = what_raw.strip()
    else:
        what = clean_title
    why = sections.get("why") or (clean_goal.split("\n\n")[0] if "\n\n" in clean_goal else clean_goal)
    when_where = sections.get("where/when") or sections.get("when/where") or "Workspace repository"

    constraints = parse_bullet_list(sections.get("constraints", ""))
    if not constraints:
        constraints = list(DEFAULT_CONSTRAINTS)

    done_when = parse_bullet_list(sections.get("done-when", ""))
    if not done_when:
        done_when = list(DEFAULT_DONE_WHEN)

    return ProblemStatement(
        what=what,
        why=why,
        when_where=when_where,
        constraints=constraints,
        done_when=done_when,
    )


def generate_default_evidence_questions() -> List[EvidenceQuestion]:
    """Generate default evidence questions for all 4 tower floors."""
    return [
        EvidenceQuestion(
            question_id="EV-PLAN-1",
            floor=Floor.PLANNER,
            question="Are all goals decomposed into discrete, verified DAG tasks?",
            expected_metric="task_count >= 1 and is_dag == True",
        ),
        EvidenceQuestion(
            question_id="EV-BLD-1",
            floor=Floor.BUILDER,
            question="Do all generated files parse cleanly and pass unit tests?",
            expected_metric="exit_code == 0",
        ),
        EvidenceQuestion(
            question_id="EV-SEC-1",
            floor=Floor.SECURITY_QA,
            question="Are there zero leaked secrets and tests passed?",
            expected_metric="secrets_found == 0 and tests_passed == True",
        ),
        EvidenceQuestion(
            question_id="EV-REL-1",
            floor=Floor.RELEASE_MONITOR,
            question="Is release artifact packaged with rollback steps documented?",
            expected_metric="rollback_defined == True",
        ),
    ]


def build_session_id(issue_id: int, title: str) -> str:
    """Create a deterministic, URL-safe session identifier."""
    digest = hashlib.sha256(f"{issue_id}:{title}".encode("utf-8")).hexdigest()[:8]
    return f"session-issue-{issue_id}-{digest}"


def create_goal_session(issue: GitLabIssue, workspace_root: Path) -> GoalSession:
    """Initialize a Goal Session from a GitLab issue payload and create results/ directory."""
    problem = derive_problem_statement(issue.title, issue.description)
    questions = generate_default_evidence_questions()
    session_id = build_session_id(issue.issue_id, issue.title)

    session = GoalSession(
        session_id=session_id,
        issue=issue,
        problem_statement=problem,
        evidence_questions=questions,
        status="INITIALIZED",
    )

    results_dir = workspace_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    session_file = results_dir / "session.json"
    assert_safe_path(session_file, workspace_root)

    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, indent=2, sort_keys=True)

    return session


def record_stage_evidence(
    stage: Floor,
    status: StageStatus,
    summary: str,
    evidence: dict,
    workspace_root: Path,
) -> StageResult:
    """Record stage evidence into results/<stage>.json deterministically."""
    results_dir = workspace_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    target_file = results_dir / f"{stage.value}.json"
    assert_safe_path(target_file, workspace_root)

    timestamp = datetime.now(timezone.utc).isoformat()
    result = StageResult(
        stage=stage,
        status=status,
        summary=summary,
        evidence=evidence,
        timestamp=timestamp,
    )

    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, sort_keys=True)

    return result


def verify_stage_evidence(stage: Floor, workspace_root: Path) -> StageResult:
    """Verify that stage evidence exists in results/<stage>.json and reported PASS."""
    target_file = workspace_root / "results" / f"{stage.value}.json"
    assert_safe_path(target_file, workspace_root)

    if not target_file.exists():
        raise FileNotFoundError(f"Missing required evidence file: {target_file}")

    with open(target_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = StageResult.from_dict(data)
    if result.status != StageStatus.PASS:
        raise ValueError(
            f"Stage {stage.value} did not PASS. Current status: {result.status.value}"
        )
    if not result.evidence:
        raise ValueError(f"Stage {stage.value} reported PASS but provided empty evidence.")

    return result
