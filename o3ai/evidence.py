"""Evidence recording, checklist verification, and status derivation.

Status is derived ONLY from results/*.json files:
  - PENDING: File results/<floor>.json does not exist.
  - NEEDS_EVIDENCE: File exists but lacks required checklist fields or metrics.
  - PASS: File exists, status is PASS, and all checklist conditions met.
  - FAIL: File exists and status is FAIL or validation check failed.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

import re
from o3ai.models import Floor, StageStatus
from o3ai.security import assert_safe_path, SecurityValidationError


REQUIRED_STAGES = ["planner", "builder", "security_qa", "release_monitor"]

# Per-floor required evidence checklist
FLOOR_CHECKLISTS: Dict[str, Dict[str, str]] = {
    "planner": {
        "tasks": "List of discrete tasks",
        "task_count": "Count of tasks (must be >= 1)",
        "is_dag": "DAG validity check (must be True)",
    },
    "builder": {
        "target_files": "List of modified or generated files",
        "exit_code": "Build execution code (must be 0)",
    },
    "security_qa": {
        "secrets_found": "Count of detected leaked secrets (must be 0)",
        "tests_passed": "Test suite result flag (must be True)",
    },
    "release_monitor": {
        "version": "Semantic version string",
        "rollback_defined": "Rollback procedure flag (must be True)",
    },
}


class EvidenceError(Exception):
    """Raised when evidence verification fails or schema is violated."""
    pass


def get_floor_checklist(floor_name: str) -> Dict[str, str]:
    """Return the required evidence checklist for a floor."""
    return FLOOR_CHECKLISTS.get(floor_name, {})


def _get_safe_stage_file(stage: str, results_dir: Path) -> Path:
    """Validate stage name against traversal and return safe path."""
    if not isinstance(stage, str) or not re.match(r"^[A-Za-z0-9_-]+$", stage):
        raise EvidenceError(f"Invalid stage identifier '{stage}'.")
    target_file = results_dir / f"{stage}.json"
    try:
        return assert_safe_path(target_file, results_dir)
    except SecurityValidationError as err:
        raise EvidenceError(f"Path traversal detected: {err}")


def write_evidence(
    stage: str,
    status: str,
    summary: str,
    evidence: Dict[str, Any],
    results_dir: Path,
) -> Path:
    """Write deterministic evidence file into results/<stage>.json."""
    results_dir.mkdir(parents=True, exist_ok=True)
    target_file = _get_safe_stage_file(stage, results_dir)

    payload = {
        "stage": stage,
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    return target_file


def load_evidence(stage: str, results_dir: Path) -> Dict[str, Any]:
    """Read and parse evidence file from results/<stage>.json.

    Raises:
        EvidenceError: If file is missing or invalid JSON.
    """
    target_file = _get_safe_stage_file(stage, results_dir)
    if not target_file.exists():
        raise EvidenceError(f"Missing evidence file for stage '{stage}': {target_file}")

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as err:
        raise EvidenceError(f"Malformed JSON in evidence for '{stage}': {err}")


def _evaluate_floor_evidence(stage: str, evidence: Dict[str, Any]) -> bool:
    """Evaluate if evidence payload satisfies specific metric requirements."""
    if stage == "planner":
        return bool(evidence.get("is_dag") is True and int(evidence.get("task_count", 0)) >= 1)
    if stage == "builder":
        return bool(evidence.get("exit_code") == 0 and isinstance(evidence.get("target_files"), list))
    if stage == "security_qa":
        return bool(evidence.get("secrets_found") == 0 and evidence.get("tests_passed") is True)
    if stage == "release_monitor":
        return bool(evidence.get("rollback_defined") is True and bool(evidence.get("version")))
    return True


def derive_floor_status(stage: str, results_dir: Path) -> StageStatus:
    """Derive status strictly from results/<stage>.json file.

    Returns:
        PENDING, NEEDS_EVIDENCE, PASS, or FAIL.
    """
    try:
        target_file = _get_safe_stage_file(stage, results_dir)
    except EvidenceError:
        return StageStatus.FAIL

    if not target_file.exists():
        return StageStatus.PENDING

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return StageStatus.FAIL

    if not isinstance(data, dict):
        return StageStatus.FAIL

    raw_status = str(data.get("status", "")).upper()
    if raw_status == "FAIL":
        return StageStatus.FAIL

    evidence = data.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return StageStatus.NEEDS_EVIDENCE

    # Check required checklist keys
    checklist = get_floor_checklist(stage)
    for required_key in checklist:
        if required_key not in evidence:
            return StageStatus.NEEDS_EVIDENCE

    # Check metric validation
    if not _evaluate_floor_evidence(stage, evidence):
        return StageStatus.FAIL

    if raw_status == "PASS":
        return StageStatus.PASS

    return StageStatus.NEEDS_EVIDENCE


def derive_all_floor_statuses(results_dir: Path) -> Dict[str, StageStatus]:
    """Derive statuses for all 4 Working Tower floors."""
    return {stage: derive_floor_status(stage, results_dir) for stage in REQUIRED_STAGES}


def verify_stage_evidence(stage: str, results_dir: Path) -> Dict[str, Any]:
    """Verify that stage evidence exists and has derived status PASS.

    Raises:
        EvidenceError: If status is not PASS.
    """
    status = derive_floor_status(stage, results_dir)
    if status != StageStatus.PASS:
        raise EvidenceError(f"Stage '{stage}' derived status is '{status.value}', expected 'PASS'.")
    return load_evidence(stage, results_dir)
