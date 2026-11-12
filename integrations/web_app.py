"""O3AI Web Application for Developer/Operator Dashboard and Customer Milestone Checkout.

Preserves O3AI ("Optimum Objective Operational AI") as the authoritative engine and
evidence gate. PayPal Sandbox is strictly downstream and cannot be executed, simulated,
or marked PASS unless O3AI floor evidence, operator approval, and real PayPal Sandbox
responses succeed.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from integrations.ai_planner import AIPlannerError, create_ai_plan
from integrations.paypal import (
    REQUIRED_TOWER_STAGES,
    SANDBOX_API,
    PayPalSandboxClient,
    PayPalWorkflowError,
    _validate_amount,
    _validate_description,
    verify_o3ai_gate,
)
from o3ai.evidence import derive_all_floor_statuses, get_floor_checklist
from o3ai.models import GitLabIssue, StageStatus
from o3ai.planner_agent import run_planner_agent
from o3ai.report import generate_reports
from o3ai.security import SecurityValidationError, assert_safe_path, sanitize_untrusted_text
from o3ai.session import create_goal_session
from o3ai.tower import WorkingTower

WORKSPACE = Path(__file__).resolve().parent.parent
WEB_DIR = WORKSPACE / "web"
RESULTS_DIR = WORKSPACE / "results"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MAX_BODY_BYTES = 65_536
_ORDER_ID_RE = re.compile(r"^[A-Z0-9-]{5,64}$")


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_json_list_safe(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_json_safe(results_dir: Path, filename: str, payload: Any) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    target = assert_safe_path(results_dir / filename, results_dir)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _append_activity(
    results_dir: Path,
    event: str,
    actor: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    log_path = assert_safe_path(results_dir / "activity_log.json", results_dir)
    entries = _read_json_list_safe(log_path)
    session_data = _read_json_safe(results_dir / "session.json") or {}
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "status": status,
        "session_id": session_data.get("session_id"),
    }
    if details:
        entry["details"] = details
    entries.append(entry)
    _write_json_safe(results_dir, "activity_log.json", entries[-100:])


def _load_milestone(results_dir: Path) -> dict[str, str]:
    milestone = _read_json_safe(results_dir / "milestone.json")
    session = _read_json_safe(results_dir / "session.json") or {}
    issue = session.get("issue", {}) if isinstance(session.get("issue"), dict) else {}
    default_title = str(issue.get("title") or "Verified O3AI Engineering Milestone")[:127]
    if milestone:
        return {
            "title": str(milestone.get("title") or default_title),
            "description": str(milestone.get("description") or default_title),
            "amount": str(milestone.get("amount") or "25.00"),
            "currency": str(milestone.get("currency") or "USD"),
        }
    return {
        "title": default_title,
        "description": default_title,
        "amount": "25.00",
        "currency": "USD",
    }


def _collect_floor_states(results_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    derived = derive_all_floor_statuses(results_dir)
    stages: dict[str, dict[str, Any]] = {}
    timestamps: dict[str, Any] = {}
    blocked_reasons: list[str] = []

    for stage in REQUIRED_TOWER_STAGES:
        raw = _read_json_safe(results_dir / f"{stage}.json")
        derived_status = derived[stage]
        if raw is None:
            summary = f"Missing results/{stage}.json"
            evidence: dict[str, Any] = {}
            ts = None
        else:
            summary = str(raw.get("summary") or f"{stage} evidence recorded.")
            raw_ev = raw.get("evidence")
            evidence = raw_ev if isinstance(raw_ev, dict) else {}
            ts = raw.get("timestamp")

        stages[stage] = {
            "status": derived_status.value,
            "summary": summary,
            "evidence": evidence,
            "checklist": list(get_floor_checklist(stage).keys()),
            "timestamp": ts,
        }
        timestamps[stage] = ts
        if derived_status != StageStatus.PASS:
            blocked_reasons.append(f"Floor '{stage}' is {derived_status.value} (must be PASS).")

    return stages, timestamps, blocked_reasons


def _collect_approval_state(
    results_dir: Path,
    floor_timestamps: dict[str, Any],
    blocked_reasons: list[str],
) -> dict[str, Any]:
    approval = _read_json_safe(results_dir / "approve.json")
    if approval is None:
        blocked_reasons.append("Operator approval (results/approve.json) is missing.")
        return {
            "status": "PENDING",
            "verification_passed": False,
            "approved_by": None,
            "timestamp": None,
            "stale": False,
            "summary": "Waiting for authenticated operator approval.",
        }

    ev = approval.get("evidence")
    ev_dict = ev if isinstance(ev, dict) else {}
    raw_status = str(approval.get("status", "PENDING")).upper()
    verification_passed = ev_dict.get("verification_passed") is True
    recorded_ts = ev_dict.get("stage_timestamps")
    missing_or_mismatched_ts = (
        not isinstance(recorded_ts, dict)
        or any(not recorded_ts.get(stage) or recorded_ts.get(stage) != floor_timestamps.get(stage) for stage in REQUIRED_TOWER_STAGES)
    )
    stale = raw_status == "PASS" and verification_passed and missing_or_mismatched_ts

    if stale:
        blocked_reasons.append(
            "Operator approval is missing valid stage_timestamps or floor evidence changed; re-approval required."
        )
        effective_status = "STALE"
    elif raw_status == "PASS" and verification_passed:
        effective_status = "PASS"
    elif raw_status == "FAIL" and ev_dict.get("rejected_by"):
        blocked_reasons.append("Operator rejected the O3AI evidence gate; explicit re-approval is required.")
        effective_status = "FAIL"
    else:
        blocked_reasons.append(f"Operator approval status is {raw_status} (verification_passed={verification_passed}).")
        effective_status = "FAIL" if raw_status == "FAIL" else "PENDING"

    return {
        "status": effective_status,
        "verification_passed": verification_passed and not stale and effective_status == "PASS",
        "approved_by": ev_dict.get("approved_by"),
        "timestamp": approval.get("timestamp"),
        "stale": stale,
        "summary": str(approval.get("summary") or ("Operator approval recorded." if effective_status == "PASS" else "Approval not granted.")),
    }


def _collect_paypal_state(results_dir: Path) -> dict[str, Any]:
    order_doc = _read_json_safe(results_dir / "paypal_order.json")
    capture_doc = _read_json_safe(results_dir / "paypal_capture.json")
    order_ev = order_doc.get("evidence", {}) if isinstance(order_doc, dict) and isinstance(order_doc.get("evidence"), dict) else {}
    cap_ev = capture_doc.get("evidence", {}) if isinstance(capture_doc, dict) and isinstance(capture_doc.get("evidence"), dict) else {}

    has_order = bool(order_doc and order_doc.get("status") == "PASS" and order_ev.get("environment") == "sandbox" and order_ev.get("order_id"))
    order_status = str(order_ev.get("order_status") or "NOT_CREATED").upper() if has_order else "NOT_CREATED"
    buyer_approved = bool(has_order and order_ev.get("buyer_approved") is True and order_status == "APPROVED")

    if not has_order:
        buyer_state = "NOT_STARTED"
    elif buyer_approved:
        buyer_state = "APPROVED"
    elif order_status in {"VOIDED", "CANCELLED"}:
        buyer_state = order_status
    else:
        buyer_state = "AWAITING_BUYER"

    cap_status_doc = str(capture_doc.get("status") or "").upper() if capture_doc else ""
    if capture_doc and cap_status_doc == "PASS" and cap_ev.get("capture_status") == "COMPLETED" and cap_ev.get("environment") == "sandbox":
        capture_state = "COMPLETED"
    elif capture_doc and cap_status_doc == "FAIL":
        capture_state = "FAILED"
    else:
        capture_state = "NOT_CAPTURED"

    return {
        "configured": bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET")),
        "sandbox_host": SANDBOX_API,
        "order": {
            "exists": has_order,
            "state": order_status,
            "order_id": order_ev.get("order_id") if has_order else None,
            "approval_url": order_ev.get("approval_url") if has_order else None,
            "amount": order_ev.get("amount") if has_order else None,
            "currency": order_ev.get("currency") if has_order else None,
            "description": order_ev.get("description") if has_order else None,
            "environment": order_ev.get("environment") if has_order else None,
            "timestamp": order_doc.get("timestamp") if has_order else None,
        },
        "buyer_approval": {
            "state": buyer_state,
            "buyer_approved": buyer_approved,
            "verified_at": order_ev.get("buyer_verified_at") if has_order else None,
        },
        "capture": {
            "state": capture_state,
            "capture_id": cap_ev.get("capture_id") if capture_state == "COMPLETED" else None,
            "capture_status": cap_ev.get("capture_status") if capture_doc else "NOT_CAPTURED",
            "environment": cap_ev.get("environment") if capture_doc else None,
            "timestamp": capture_doc.get("timestamp") if capture_doc else None,
            "error_summary": str(capture_doc.get("summary")) if capture_state == "FAILED" else None,
        },
    }


def build_status_payload(results_dir: Path) -> dict[str, Any]:
    """Derive complete O3AI engine, gate, AI proposal, and PayPal Sandbox state."""
    results_dir = results_dir.resolve()
    stages, floor_timestamps, blocked_reasons = _collect_floor_states(results_dir)
    approval = _collect_approval_state(results_dir, floor_timestamps, blocked_reasons)

    try:
        verify_o3ai_gate(results_dir)
        gate_ready = True
    except PayPalWorkflowError as exc:
        gate_ready = False
        if not blocked_reasons:
            blocked_reasons.append(str(exc))

    session_raw = _read_json_safe(results_dir / "session.json") or {}
    issue = session_raw.get("issue", {}) if isinstance(session_raw.get("issue"), dict) else {}
    ps = session_raw.get("problem_statement", {}) if isinstance(session_raw.get("problem_statement"), dict) else {}
    session_summary = {
        "exists": bool(session_raw),
        "session_id": session_raw.get("session_id"),
        "issue_id": issue.get("issue_id"),
        "title": issue.get("title"),
        "what": ps.get("what"),
        "why": ps.get("why"),
    }

    ai_plan = _read_json_safe(results_dir / "ai_plan.json")
    ai_proposal = {
        "exists": bool(ai_plan),
        "status": "PROPOSAL_ONLY" if ai_plan else "NONE",
        "is_evidence": False,
        "summary": ai_plan.get("summary") if ai_plan else None,
        "tasks": ai_plan.get("tasks", []) if ai_plan else [],
        "evidence_questions": ai_plan.get("evidence_questions", []) if ai_plan else [],
        "verification_note": ai_plan.get("verification_note") if ai_plan else None,
    }

    milestone = _load_milestone(results_dir)
    paypal_state = _collect_paypal_state(results_dir)
    activity_trail = list(reversed(_read_json_list_safe(results_dir / "activity_log.json")))

    return {
        "engine": "O3AI — Optimum Objective Operational AI",
        "thesis": "O3AI proves the work before money moves.",
        "mode": "sandbox",
        "status": "READY" if gate_ready else "BLOCKED",
        "blocked_reasons": blocked_reasons,
        "session": session_summary,
        "milestone": milestone,
        "stages": stages,
        "approve": approval,
        "ai_proposal": ai_proposal,
        "ai_configured": bool(os.environ.get("O3AI_AI_API_KEY") and os.environ.get("O3AI_AI_MODEL")),
        "paypal": paypal_state,
        "activity_trail": activity_trail,
    }


def build_customer_payload(results_dir: Path) -> dict[str, Any]:
    """Return a sanitized customer-facing view without internal tokens or paths."""
    full = build_status_payload(results_dir)
    gate_ready = full["status"] == "READY"
    order = full["paypal"]["order"]
    buyer = full["paypal"]["buyer_approval"]
    capture = full["paypal"]["capture"]
    milestone = full["milestone"]

    approval_url = None
    if gate_ready and order["exists"] and capture["state"] != "COMPLETED":
        approval_url = order["approval_url"]

    if gate_ready and capture["state"] == "COMPLETED":
        gate_reason = "Payment completed on PayPal Sandbox after O3AI verification and operator confirmation."
    elif gate_ready and buyer["buyer_approved"]:
        gate_reason = "Buyer approved on PayPal Sandbox. Waiting for operator explicit capture."
    elif gate_ready and order["exists"]:
        gate_reason = "O3AI verification and operator approval passed. Ready for PayPal Sandbox buyer approval."
    elif gate_ready:
        gate_reason = "O3AI verification and operator approval passed. Waiting for operator to initialize PayPal Sandbox order."
    else:
        gate_reason = "Payment is blocked until all 4 O3AI Tower stages and operator approval pass."

    return {
        "engine": full["engine"],
        "thesis": full["thesis"],
        "gate_status": full["status"],
        "gate_ready": gate_ready,
        "gate_reason": gate_reason,
        "operator_approved": full["approve"]["status"] == "PASS",
        "paypal_configured": full["paypal"]["configured"],
        "milestone": {
            "title": milestone["title"],
            "description": milestone["description"],
            "amount": order["amount"] if order["exists"] else milestone["amount"],
            "currency": order["currency"] if order["exists"] else milestone["currency"],
        },
        "order": {
            "exists": order["exists"],
            "order_id": order["order_id"],
            "order_status": order["state"],
            "buyer_approved": buyer["buyer_approved"],
            "buyer_approval_state": buyer["state"],
            "approval_url": approval_url,
        },
        "capture": {
            "completed": capture["state"] == "COMPLETED",
            "capture_id": capture["capture_id"],
            "capture_status": capture["capture_status"],
        },
    }


def _header_get(headers: dict[str, str], name: str, default: str = "") -> str:
    lower = name.lower()
    for key, val in headers.items():
        if key.lower() == lower:
            return val
    return default


def _run_real_workspace_checks(results_dir: Path, issue: GitLabIssue) -> dict[str, Any]:
    """Run real deterministic O3AI verification stages and record floor evidence."""
    workspace_root = results_dir.parent
    results_dir.mkdir(parents=True, exist_ok=True)

    run_planner_agent(issue, workspace_root)

    lint_proc = subprocess.run(
        [sys.executable, str(workspace_root / "scripts" / "lint.py")],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )
    test_proc = subprocess.run(
        [sys.executable, str(workspace_root / "scripts" / "test_runner.py")],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )
    sec_proc = subprocess.run(
        [sys.executable, str(workspace_root / "scripts" / "security_check.py")],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )

    lint_ok = lint_proc.returncode == 0
    tests_ok = test_proc.returncode == 0
    sec_ok = sec_proc.returncode == 0
    test_doc = _read_json_safe(results_dir / "test.json") or {}
    test_ev = test_doc.get("evidence") if isinstance(test_doc.get("evidence"), dict) else {}
    sec_doc = _read_json_safe(results_dir / "security.json") or {}
    sec_ev = sec_doc.get("evidence") if isinstance(sec_doc.get("evidence"), dict) else {}
    secrets_found = int(sec_ev.get("secrets_found", 0 if sec_ok else 1))

    tower = WorkingTower(results_dir)
    tower.run_builder(
        target_files=["sample_app/signup.py", "o3ai/", "integrations/", "web/"],
        build_exit_code=0 if (lint_ok and tests_ok) else 1,
    )

    if lint_ok and tests_ok:
        tower.run_security_qa(
            secrets_found=secrets_found,
            tests_passed=tests_ok and sec_ok,
        )
        if sec_ok and secrets_found == 0:
            session_doc = _read_json_safe(results_dir / "session.json") or {}
            session_id = str(session_doc.get("session_id") or f"issue-{issue.issue_id}")
            tower.run_release_monitor(version=session_id, rollback_defined=True)

    generate_reports(results_dir)
    return {
        "lint_ok": lint_ok,
        "tests_ok": tests_ok,
        "tests_run": test_ev.get("tests_run", 0),
        "sec_ok": sec_ok,
        "secrets_found": secrets_found,
    }


class O3AIWebApp:
    """Stateful handler coordinator for O3AI local/authenticated web routes."""

    def __init__(
        self,
        results_dir: Path = RESULTS_DIR,
        web_dir: Path = WEB_DIR,
        operator_token: str | None = None,
        paypal_client_factory: Callable[[], PayPalSandboxClient] = PayPalSandboxClient,
        ai_planner_fn: Callable[[str, Path], dict[str, Any]] = create_ai_plan,
        checks_runner_fn: Callable[[Path, GitLabIssue], dict[str, Any]] = _run_real_workspace_checks,
    ) -> None:
        self.results_dir = results_dir.resolve()
        self.web_dir = web_dir.resolve()
        self.operator_token = operator_token or os.environ.get("O3AI_OPERATOR_TOKEN") or secrets.token_urlsafe(24)
        self.paypal_client_factory = paypal_client_factory
        self.ai_planner_fn = ai_planner_fn
        self.checks_runner_fn = checks_runner_fn
        self.sessions: dict[str, dict[str, str]] = {}

    def create_session(self) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(24)
        csrf_token = secrets.token_urlsafe(24)
        self.sessions[session_id] = {"csrf_token": csrf_token}
        return session_id, csrf_token

    def get_session(self, cookie_header: str | None) -> tuple[str | None, dict[str, str] | None]:
        if not cookie_header:
            return None, None
        jar = cookies.SimpleCookie()
        try:
            jar.load(cookie_header)
        except cookies.CookieError:
            return None, None
        morsel = jar.get("o3ai_op_session")
        if not morsel:
            return None, None
        sid = morsel.value
        session = self.sessions.get(sid)
        return (sid, session) if session else (None, None)

    def authorize_operator(self, headers: dict[str, str]) -> tuple[bool, int, str]:
        """Authorize operator via bearer/header token or cookie session + CSRF + origin."""
        origin = _header_get(headers, "Origin")
        host = _header_get(headers, "Host")
        if origin and host and urlparse(origin).netloc != host:
            return False, 403, "Cross-origin request rejected."
        sec_fetch_site = _header_get(headers, "Sec-Fetch-Site")
        if sec_fetch_site and sec_fetch_site not in {"same-origin", "none"}:
            return False, 403, "Cross-site request rejected."

        bearer = _header_get(headers, "Authorization")
        header_token = _header_get(headers, "X-O3AI-Operator-Token")
        candidate = ""
        if bearer.startswith("Bearer "):
            candidate = bearer[7:].strip()
        elif header_token:
            candidate = header_token.strip()

        if candidate:
            if hmac.compare_digest(candidate, self.operator_token):
                return True, 200, "ok"
            return False, 401, "Invalid operator token."

        _, session = self.get_session(_header_get(headers, "Cookie"))
        if not session:
            return False, 401, "Operator authentication required."
        csrf_header = _header_get(headers, "X-CSRF-Token")
        if not csrf_header or not hmac.compare_digest(csrf_header, session["csrf_token"]):
            return False, 403, "Missing or invalid CSRF token."
        return True, 200, "ok"

    def _json_response(
        self,
        status_code: int,
        payload: dict[str, Any],
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        body = json.dumps(payload, indent=2).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "same-origin"),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        return status_code, headers, body

    def _html_response(self, path: Path) -> tuple[int, list[tuple[str, str]], bytes]:
        if not path.is_file():
            return self._json_response(404, {"ok": False, "error": "Page not found."})
        body = path.read_bytes()
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "same-origin"),
        ]
        return 200, headers, body

    def dispatch(
        self,
        method: str,
        raw_path: str,
        headers: dict[str, str],
        body: bytes = b"",
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        """Route a single HTTP request cleanly without requiring live sockets in tests."""
        parsed = urlparse(raw_path)
        if method.upper() == "GET":
            return self._handle_get(parsed.path, parsed.query, headers)
        if method.upper() == "POST":
            return self._handle_post(parsed.path, headers, body)
        return self._json_response(405, {"ok": False, "error": "Method not allowed."})

    def _handle_get(
        self,
        path: str,
        query: str,
        headers: dict[str, str],
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        if path in ("/", "/index.html"):
            return self._html_response(self.web_dir / "index.html")
        if path == "/customer":
            return self._html_response(self.web_dir / "customer.html")
        if path == "/api/status":
            return self._json_response(200, build_status_payload(self.results_dir))
        if path == "/api/operator/session":
            _, session = self.get_session(_header_get(headers, "Cookie"))
            return self._json_response(
                200,
                {
                    "authenticated": session is not None,
                    "csrf_token": session["csrf_token"] if session else None,
                },
            )
        if path == "/api/customer/milestone":
            return self._json_response(200, build_customer_payload(self.results_dir))
        if path == "/api/customer/verify-return":
            return self._handle_customer_verify_return(query)
        return self._json_response(404, {"ok": False, "error": "Endpoint not found."})

    def _handle_customer_verify_return(self, query_str: str) -> tuple[int, list[tuple[str, str]], bytes]:
        params = parse_qs(query_str)
        token = (params.get("token") or [""])[0].strip()
        if not _ORDER_ID_RE.fullmatch(token):
            return self._json_response(400, {"ok": False, "error": "Invalid PayPal order token."})
        try:
            client = self.paypal_client_factory()
            updated = client.verify_buyer_approval(self.results_dir, token)
        except PayPalWorkflowError as exc:
            return self._json_response(400, {"ok": False, "error": str(exc)})
        _append_activity(
            self.results_dir,
            "buyer_approval_verified",
            "customer_return",
            "PASS" if updated.get("buyer_approved") else "PENDING",
            {"order_id": token, "order_status": updated.get("order_status")},
        )
        return self._json_response(
            200,
            {
                "ok": True,
                "order_id": updated["order_id"],
                "order_status": updated["order_status"],
                "buyer_approved": updated["buyer_approved"],
                "message": (
                    "PayPal Sandbox buyer approval confirmed. Awaiting operator explicit capture."
                    if updated.get("buyer_approved")
                    else f"PayPal order status is {updated.get('order_status')}; buyer approval not yet completed."
                ),
            },
        )

    def _handle_post(
        self,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        if len(body) > _MAX_BODY_BYTES:
            return self._json_response(413, {"ok": False, "error": "Request payload too large."})
        if not body:
            payload: dict[str, Any] = {}
        else:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json_response(400, {"ok": False, "error": "Invalid JSON request body."})
            if not isinstance(parsed, dict):
                return self._json_response(400, {"ok": False, "error": "JSON body must be an object."})
            payload = parsed

        if path == "/api/operator/login":
            origin = _header_get(headers, "Origin")
            host = _header_get(headers, "Host")
            if origin and host and urlparse(origin).netloc != host:
                return self._json_response(403, {"ok": False, "error": "Cross-origin login rejected."})
            candidate = str(payload.get("token") or "").strip()
            if not candidate or not hmac.compare_digest(candidate, self.operator_token):
                return self._json_response(401, {"ok": False, "error": "Invalid operator token."})
            sid, csrf = self.create_session()
            return self._json_response(
                200,
                {"ok": True, "csrf_token": csrf},
                extra_headers=[("Set-Cookie", f"o3ai_op_session={sid}; HttpOnly; SameSite=Strict; Path=/")],
            )

        if path == "/api/operator/logout":
            sid, _ = self.get_session(_header_get(headers, "Cookie"))
            if sid:
                self.sessions.pop(sid, None)
            return self._json_response(
                200,
                {"ok": True},
                extra_headers=[("Set-Cookie", "o3ai_op_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")],
            )

        if not path.startswith("/api/operator/"):
            return self._json_response(404, {"ok": False, "error": "Endpoint not found."})

        authorized, auth_code, auth_msg = self.authorize_operator(headers)
        if not authorized:
            return self._json_response(auth_code, {"ok": False, "error": auth_msg})

        if path == "/api/operator/init-session":
            return self._handle_init_session(payload)
        if path == "/api/operator/run-checks":
            return self._handle_run_checks()
        if path == "/api/operator/milestone":
            return self._handle_milestone(payload)
        if path == "/api/operator/approve":
            return self._handle_approve(payload)
        if path == "/api/operator/create-order":
            return self._handle_create_order(payload, headers)
        if path == "/api/operator/verify-order":
            return self._handle_verify_order(payload)
        if path == "/api/operator/capture-order":
            return self._handle_capture_order(payload)
        if path == "/api/operator/ai-plan":
            return self._handle_ai_plan(payload)
        if path == "/api/operator/reject":
            return self._handle_reject(payload)
        return self._json_response(404, {"ok": False, "error": "Endpoint not found."})

    def _handle_reject(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        """Record an explicit operator rejection; PayPal actions stay blocked until re-approval."""
        if payload.get("confirm_phrase") != "REJECT":
            return self._json_response(400, {"ok": False, "error": "Explicit operator confirmation ('REJECT') is required."})
        raw_reason = payload.get("reason")
        if raw_reason is not None and not isinstance(raw_reason, str):
            return self._json_response(400, {"ok": False, "error": "Rejection reason must be text."})
        reason = sanitize_untrusted_text(raw_reason or "")
        if len(reason) > 300:
            return self._json_response(400, {"ok": False, "error": "Rejection reason must be at most 300 characters."})

        cap_doc = _read_json_safe(self.results_dir / "paypal_capture.json")
        if cap_doc and cap_doc.get("status") == "PASS":
            return self._json_response(409, {"ok": False, "error": "Cannot reject after payment capture completed."})

        _, timestamps, _ = _collect_floor_states(self.results_dir)
        summary = "Authenticated operator rejected the O3AI evidence gate."
        if reason:
            summary = f"{summary} Reason: {reason}"
        rejection_doc = {
            "stage": "approve",
            "status": "FAIL",
            "summary": summary,
            "evidence": {
                "rejected_by": "developer_operator",
                "prerequisites_checked": list(REQUIRED_TOWER_STAGES),
                "verification_passed": False,
                "rejection_reason": reason,
                "stage_timestamps": timestamps,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_safe(self.results_dir, "approve.json", rejection_doc)
        _append_activity(
            self.results_dir,
            "operator_rejected",
            "developer_operator",
            "FAIL",
            {"reason": reason} if reason else None,
        )
        return self._json_response(
            200,
            {
                "ok": True,
                "message": "Operator rejection recorded. PayPal actions are blocked until explicit re-approval.",
                "approve": rejection_doc,
            },
        )

    def _handle_init_session(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        cap_doc = _read_json_safe(self.results_dir / "paypal_capture.json")
        if cap_doc and cap_doc.get("status") == "PASS" and payload.get("force_reset") is not True:
            return self._json_response(
                409,
                {"ok": False, "error": "Payment capture already completed. Pass force_reset=true to start a new Goal Session."},
            )

        title = str(payload.get("title") or "").strip()
        description = str(payload.get("description") or "").strip()
        try:
            raw_issue_id = int(payload.get("issue_id", 101))
        except (TypeError, ValueError):
            return self._json_response(400, {"ok": False, "error": "issue_id must be a positive integer."})
        if raw_issue_id <= 0:
            return self._json_response(400, {"ok": False, "error": "issue_id must be a positive integer."})

        milestone_updates: dict[str, str] | None = None
        if payload.get("amount") is not None or payload.get("currency") is not None:
            try:
                currency = str(payload.get("currency") or "USD").strip()
                amount = _validate_amount(str(payload.get("amount", "")), currency)
                ms_desc = _validate_description(str(payload.get("milestone_description") or title))
            except PayPalWorkflowError as exc:
                return self._json_response(400, {"ok": False, "error": str(exc)})
            milestone_updates = {"title": ms_desc, "description": ms_desc, "amount": amount, "currency": currency}

        issue = GitLabIssue(
            issue_id=raw_issue_id,
            title=title,
            description=description,
            author="developer_operator",
            labels=["goal-session"],
        )
        workspace_root = self.results_dir.parent
        try:
            session = create_goal_session(issue, workspace_root)
        except (SecurityValidationError, ValueError) as exc:
            return self._json_response(400, {"ok": False, "error": str(exc)})

        for stale_name in (
            "planner.json",
            "planner.md",
            "builder.json",
            "security_qa.json",
            "release_monitor.json",
            "approve.json",
            "paypal_order.json",
            "paypal_capture.json",
            "tower_report.json",
            "validate.json",
            "test.json",
            "security.json",
            "ai_plan.json",
        ):
            stale_path = assert_safe_path(self.results_dir / stale_name, self.results_dir)
            if stale_path.is_file():
                stale_path.unlink()

        if milestone_updates is not None:
            _write_json_safe(
                self.results_dir,
                "milestone.json",
                {
                    **milestone_updates,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        _append_activity(
            self.results_dir,
            "goal_session_created",
            "developer_operator",
            "PASS",
            {"session_id": session.session_id, "issue_id": raw_issue_id, "title": session.issue.title},
        )
        return self._json_response(
            200,
            {"ok": True, "session": session.to_dict(), "message": "Bounded Goal Session initialized. Run verification checks next."},
        )

    def _handle_run_checks(self) -> tuple[int, list[tuple[str, str]], bytes]:
        session_doc = _read_json_safe(self.results_dir / "session.json")
        if not session_doc or not isinstance(session_doc.get("issue"), dict):
            return self._json_response(409, {"ok": False, "error": "Create a Goal Session before running verification checks."})

        issue = GitLabIssue.from_dict(session_doc["issue"])
        try:
            summary = self.checks_runner_fn(self.results_dir, issue)
        except Exception as exc:
            _append_activity(self.results_dir, "verification_checks_error", "developer_operator", "FAIL", {"reason": str(exc)})
            return self._json_response(500, {"ok": False, "error": f"Verification run failed: {exc}"})

        status_payload = build_status_payload(self.results_dir)
        overall_passed = bool(summary.get("lint_ok") and summary.get("tests_ok") and summary.get("sec_ok"))
        _append_activity(
            self.results_dir,
            "verification_checks_run",
            "developer_operator",
            "PASS" if overall_passed else "FAIL",
            summary,
        )
        return self._json_response(
            200,
            {"ok": True, "checks": summary, "gate_status": status_payload["status"], "stages": status_payload["stages"]},
        )

    def _handle_milestone(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        try:
            amount = _validate_amount(str(payload.get("amount", "")), str(payload.get("currency", "USD")))
            currency = str(payload.get("currency", "USD")).strip()
            description = _validate_description(str(payload.get("description", "")))
            title = _validate_description(str(payload.get("title") or description))
        except PayPalWorkflowError as exc:
            return self._json_response(400, {"ok": False, "error": str(exc)})

        cap_doc = _read_json_safe(self.results_dir / "paypal_capture.json")
        if cap_doc and cap_doc.get("status") == "PASS":
            return self._json_response(409, {"ok": False, "error": "Cannot modify milestone after payment capture completed."})

        milestone = {
            "title": title,
            "description": description,
            "amount": amount,
            "currency": currency,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_safe(self.results_dir, "milestone.json", milestone)
        _append_activity(self.results_dir, "milestone_updated", "developer_operator", "PASS", {"amount": amount, "currency": currency})
        return self._json_response(200, {"ok": True, "milestone": milestone})

    def _handle_approve(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        if payload.get("confirm_phrase") != "APPROVE":
            return self._json_response(400, {"ok": False, "error": "Explicit operator confirmation ('APPROVE') is required."})
        stages, timestamps, blocked = _collect_floor_states(self.results_dir)
        if blocked:
            _append_activity(self.results_dir, "operator_approval_blocked", "developer_operator", "BLOCKED", {"reasons": blocked})
            return self._json_response(409, {"ok": False, "error": f"Approval blocked: {'; '.join(blocked)}"})
        approval_doc = {
            "stage": "approve",
            "status": "PASS",
            "summary": "Authenticated operator approved O3AI evidence gate.",
            "evidence": {
                "approved_by": "developer_operator",
                "prerequisites_checked": list(REQUIRED_TOWER_STAGES),
                "verification_passed": True,
                "stage_timestamps": timestamps,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_safe(self.results_dir, "approve.json", approval_doc)
        _append_activity(self.results_dir, "operator_approved", "developer_operator", "PASS", {"stages": list(stages.keys())})
        return self._json_response(200, {"ok": True, "message": "Operator approval recorded.", "approve": approval_doc})

    def _handle_create_order(self, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
        if payload.get("confirm_phrase") != "APPROVE":
            return self._json_response(400, {"ok": False, "error": "Explicit confirmation ('APPROVE') required to create sandbox order."})
        milestone = _load_milestone(self.results_dir)
        amount = str(payload.get("amount") or milestone["amount"])
        currency = str(payload.get("currency") or milestone["currency"])
        description = str(payload.get("description") or milestone["description"])
        session_doc = _read_json_safe(self.results_dir / "session.json") or {}
        session_id = session_doc.get("session_id")
        host_header = _header_get(headers, "Host") or "127.0.0.1:8080"
        return_url = f"http://{host_header}/customer?paypal=return"
        cancel_url = f"http://{host_header}/customer?paypal=cancel"

        try:
            client = self.paypal_client_factory()
            order = client.create_order(
                self.results_dir,
                amount,
                currency,
                description,
                human_approved=True,
                return_url=return_url,
                cancel_url=cancel_url,
                session_id=session_id if isinstance(session_id, str) else None,
            )
        except PayPalWorkflowError as exc:
            _append_activity(self.results_dir, "paypal_order_blocked", "developer_operator", "BLOCKED", {"reason": str(exc)})
            return self._json_response(400, {"ok": False, "error": str(exc)})

        _write_json_safe(
            self.results_dir,
            "milestone.json",
            {
                "title": milestone["title"],
                "description": order["description"],
                "amount": order["amount"],
                "currency": order["currency"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        _append_activity(
            self.results_dir,
            "paypal_order_created",
            "developer_operator",
            "PASS",
            {"order_id": order["order_id"], "amount": order["amount"], "currency": order["currency"]},
        )
        return self._json_response(200, {"ok": True, "order": order, "sandbox": True})

    def _handle_verify_order(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        order_doc = _read_json_safe(self.results_dir / "paypal_order.json") or {}
        recorded_id = (order_doc.get("evidence") or {}).get("order_id") if isinstance(order_doc.get("evidence"), dict) else None
        order_id = str(payload.get("order_id") or recorded_id or "").strip()
        try:
            client = self.paypal_client_factory()
            updated = client.verify_buyer_approval(self.results_dir, order_id)
        except PayPalWorkflowError as exc:
            _append_activity(self.results_dir, "paypal_order_verify_failed", "developer_operator", "FAIL", {"order_id": order_id, "reason": str(exc)})
            return self._json_response(400, {"ok": False, "error": str(exc)})
        _append_activity(
            self.results_dir,
            "paypal_order_verified",
            "developer_operator",
            "PASS" if updated.get("buyer_approved") else "PENDING",
            {"order_id": updated["order_id"], "order_status": updated["order_status"]},
        )
        return self._json_response(200, {"ok": True, "order": updated})

    def _handle_capture_order(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        if payload.get("confirm_phrase") != "APPROVE":
            return self._json_response(400, {"ok": False, "error": "Explicit confirmation ('APPROVE') required to capture sandbox order."})
        order_doc = _read_json_safe(self.results_dir / "paypal_order.json") or {}
        recorded_id = (order_doc.get("evidence") or {}).get("order_id") if isinstance(order_doc.get("evidence"), dict) else None
        order_id = str(payload.get("order_id") or recorded_id or "").strip()
        try:
            client = self.paypal_client_factory()
            capture = client.capture_order(self.results_dir, order_id, human_approved=True)
        except PayPalWorkflowError as exc:
            _append_activity(self.results_dir, "paypal_capture_failed", "developer_operator", "FAIL", {"order_id": order_id, "reason": str(exc)})
            return self._json_response(400, {"ok": False, "error": str(exc)})
        _append_activity(
            self.results_dir,
            "paypal_capture_completed",
            "developer_operator",
            "PASS",
            {"order_id": order_id, "capture_id": capture.get("capture_id")},
        )
        return self._json_response(200, {"ok": True, "capture": capture, "sandbox": True})

    def _handle_ai_plan(self, payload: dict[str, Any]) -> tuple[int, list[tuple[str, str]], bytes]:
        session_raw = _read_json_safe(self.results_dir / "session.json") or {}
        issue = session_raw.get("issue", {}) if isinstance(session_raw.get("issue"), dict) else {}
        goal = str(payload.get("goal") or issue.get("title") or "").strip()
        try:
            clean_goal = sanitize_untrusted_text(goal)
            plan = self.ai_planner_fn(clean_goal, self.results_dir / "ai_plan.json")
        except (AIPlannerError, SecurityValidationError) as exc:
            _append_activity(self.results_dir, "ai_plan_failed", "developer_operator", "FAIL", {"reason": str(exc)})
            return self._json_response(400, {"ok": False, "error": str(exc)})
        _append_activity(self.results_dir, "ai_plan_proposed", "developer_operator", "PROPOSAL_ONLY", {"task_count": len(plan.get("tasks", []))})
        return self._json_response(200, {"ok": True, "proposal": plan})


def _make_handler(app: O3AIWebApp) -> type[BaseHTTPRequestHandler]:
    class O3AIHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _handle(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > _MAX_BODY_BYTES:
                status, hdrs, resp_body = app._json_response(413, {"ok": False, "error": "Request payload too large."})
            else:
                req_body = self.rfile.read(length) if length > 0 else b""
                hdr_dict = {k: v for k, v in self.headers.items()}
                status, hdrs, resp_body = app.dispatch(method, self.path, hdr_dict, req_body)
            self.send_response(status)
            for k, v in hdrs:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp_body)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

    return O3AIHandler


def create_server(host: str = "127.0.0.1", port: int = 8080, app: O3AIWebApp | None = None) -> tuple[ThreadingHTTPServer, O3AIWebApp]:
    """Create the O3AI HTTP server, refusing unsafe non-loopback hosting without a token."""
    if host not in _LOOPBACK_HOSTS and not os.environ.get("O3AI_OPERATOR_TOKEN") and app is None:
        raise RuntimeError(
            "Refusing to bind non-loopback address without O3AI_OPERATOR_TOKEN configured."
        )
    web_app = app or O3AIWebApp()
    server = ThreadingHTTPServer((host, port), _make_handler(web_app))
    return server, web_app


def main() -> int:
    try:
        from integrations.env import load_dotenv

        load_dotenv(WORKSPACE)
    except ImportError:
        pass
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("O3AI_UI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("O3AI_UI_PORT", "8080"))
    server, web_app = create_server(host=host, port=port)
    print(f"O3AI Developer/Operator Console: http://{host}:{port}/")
    print(f"O3AI Customer Milestone Page:    http://{host}:{port}/customer")
    if os.environ.get("O3AI_OPERATOR_TOKEN"):
        print("Operator auth: using configured O3AI_OPERATOR_TOKEN.")
    else:
        print(f"Local-only Operator Token (ephemeral): {web_app.operator_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
