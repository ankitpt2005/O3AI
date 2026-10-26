"""O3AI Planner Agent implementation.

Responsibilities:
  1. Reads goal issue, treating issue text strictly as untrusted DATA ONLY.
  2. Synthesizes Problem Statement (What, Why, When/Where, Constraints, Done-when).
  3. Formulates at most 5 evidence questions.
  4. Formats Tower plan into a fixed Markdown schema and records results/planner.json.
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple

from o3ai.evidence import write_evidence
from o3ai.models import EvidenceQuestion, Floor, GitLabIssue, ProblemStatement
from o3ai.security import sanitize_untrusted_text, validate_issue_title
from o3ai.session import derive_problem_statement, generate_default_evidence_questions
from o3ai.tower import PlanTask, is_acyclic_dag


MAX_EVIDENCE_QUESTIONS = 5


def formulate_evidence_questions(statement: ProblemStatement) -> List[EvidenceQuestion]:
    """Formulate at most 5 evidence questions for the Working Tower floors."""
    questions = generate_default_evidence_questions()
    if len(questions) > MAX_EVIDENCE_QUESTIONS:
        questions = questions[:MAX_EVIDENCE_QUESTIONS]
    return questions


def generate_dag_tasks(statement: ProblemStatement) -> List[PlanTask]:
    """Decompose the goal into discrete DAG tasks."""
    task1 = PlanTask(
        task_id="task-1-spec",
        name="Analyze Problem Statement",
        description=f"Validate scope: {statement.what[:80]}",
        dependencies=[],
    )
    task2 = PlanTask(
        task_id="task-2-build",
        name="Implement Code Patch",
        description=f"Apply standard library solution in {statement.when_where[:80]}",
        dependencies=["task-1-spec"],
    )
    task3 = PlanTask(
        task_id="task-3-verify",
        name="Execute Unit Tests & QA",
        description="Run test suite and verify 0 failures and 0 secrets.",
        dependencies=["task-2-build"],
    )
    task4 = PlanTask(
        task_id="task-4-release",
        name="Compile Release Evidence",
        description="Generate evidence report and stage deployment manifest.",
        dependencies=["task-3-verify"],
    )
    tasks = [task1, task2, task3, task4]
    if not is_acyclic_dag(tasks):
        raise ValueError("Task breakdown produced a cyclic graph.")
    return tasks


def format_plan_markdown(
    statement: ProblemStatement,
    questions: List[EvidenceQuestion],
    tasks: List[PlanTask],
) -> str:
    """Format the Tower plan in a fixed Markdown schema."""
    lines: List[str] = [
        f"# O3AI Tower Plan: {statement.what}",
        "",
        "## 1. Problem Statement",
        f"- **What**: {statement.what}",
        f"- **Why**: {statement.why}",
        f"- **When/Where**: {statement.when_where}",
        "- **Constraints**:",
    ]
    for c in statement.constraints:
        lines.append(f"  - {c}")
    lines.append("- **Done-when**:")
    for d in statement.done_when:
        lines.append(f"  - {d}")

    lines.extend([
        "",
        f"## 2. Evidence Questions (Max {MAX_EVIDENCE_QUESTIONS})",
    ])
    for idx, q in enumerate(questions, start=1):
        lines.append(f"{idx}. [{q.question_id}] ({q.floor.value}): {q.question}")

    lines.extend([
        "",
        "## 3. Working Tower DAG Tasks",
    ])
    for t in tasks:
        dep_str = f" (depends on {', '.join(t.dependencies)})" if t.dependencies else ""
        lines.append(f"- **[{t.task_id}]** {t.name}: {t.description}{dep_str}")

    return "\n".join(lines)


def run_planner_agent(issue: GitLabIssue, workspace_root: Path) -> Tuple[str, Path]:
    """Execute O3AI Planner Agent, record evidence, and return plan markdown."""
    validate_issue_title(issue.title)
    # Treat issue text as data only: sanitize without eval or execution
    clean_desc = sanitize_untrusted_text(issue.description)

    statement = derive_problem_statement(issue.title, clean_desc)
    questions = formulate_evidence_questions(statement)
    if len(questions) > MAX_EVIDENCE_QUESTIONS:
        raise ValueError(f"Exceeded max allowed evidence questions: {len(questions)}")

    tasks = generate_dag_tasks(statement)
    plan_md = format_plan_markdown(statement, questions, tasks)

    results_dir = workspace_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    evidence = {
        "tasks": [t.to_dict() for t in tasks],
        "task_count": len(tasks),
        "is_dag": True,
        "evidence_question_count": len(questions),
        "problem_statement": statement.to_dict(),
    }

    evidence_file = write_evidence(
        stage="planner",
        status="PASS",
        summary=f"Plan generated with {len(tasks)} DAG tasks and {len(questions)} evidence questions.",
        evidence=evidence,
        results_dir=results_dir,
    )

    plan_file = results_dir / "planner.md"
    with open(plan_file, "w", encoding="utf-8") as f:
        f.write(plan_md)

    return plan_md, evidence_file
