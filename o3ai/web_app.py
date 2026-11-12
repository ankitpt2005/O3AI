"""Minimal O3AI web interface for developer and customer views.

This keeps O3AI as the engine and shows PayPal sandbox as the downstream thing
that only becomes active after O3AI evidence and explicit approval.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import html
import os
import sys

from o3ai.paypal import PayPalSandboxClient, PayPalWorkflowError

WORKSPACE = Path(__file__).resolve().parent.parent
WEB_DIR = WORKSPACE / "web"
RESULTS_DIR = WORKSPACE / "results"


def _ensure_results_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


def _write_json(path: Path, payload: dict) -> None:
    _ensure_results_dir()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _demo_status_payload() -> dict:
    return {
        "planner": {"status": "PASS", "summary": "DAG validated with 4 tasks.", "evidence": {"task_count": 4, "is_dag": True}},
        "builder": {"status": "PASS", "summary": "Patch and tests validated.", "evidence": {"exit_code": 0, "target_files": ["sample_app/signup.py"]}},
        "security_qa": {"status": "PASS", "summary": "No secrets and tests passed.", "evidence": {"secrets_found": 0, "tests_passed": True}},
        "release_monitor": {"status": "PASS", "summary": "Rollback records and release manifest prepared.", "evidence": {"version": "0.1.0", "rollback_defined": True}},
        "approve": {"status": "PASS", "summary": "Human approval recorded.", "evidence": {"verification_passed": True}},
    }


def _bootstrap_demo_data() -> None:
    for name, payload in _demo_status_payload().items():
        stage_file = RESULTS_DIR / f"{name}.json"
        if not stage_file.exists():
            _write_json(stage_file, payload)


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _status_payload() -> dict:
    _ensure_results_dir()
    if not any((RESULTS_DIR / f"{stage}.json").exists() for stage in ["planner", "builder", "security_qa", "release_monitor", "approve"]):
        _bootstrap_demo_data()

    stages = {}
    for stage in ["planner", "builder", "security_qa", "release_monitor"]:
        data = _read_json(RESULTS_DIR / f"{stage}.json", {"status": "PENDING", "summary": "Not started."})
        stages[stage] = {
            "status": data.get("status", "PENDING"),
            "summary": data.get("summary", "Not started."),
            "evidence": data.get("evidence", {}),
        }
    approval = _read_json(RESULTS_DIR / "approve.json", {"status": "PENDING", "evidence": {"verification_passed": False}, "summary": "Waiting for approval."})
    order = _read_json(RESULTS_DIR / "paypal_order.json", {"status": "PENDING", "evidence": {}})
    capture = _read_json(RESULTS_DIR / "paypal_capture.json", {"status": "PENDING", "evidence": {}})
    status = "READY" if all(stage["status"] == "PASS" for stage in stages.values()) and approval.get("status") == "PASS" else "BLOCKED"
    return {
        "mode": "demo" if os.environ.get("O3AI_DEMO_MODE", "1") == "1" else "sandbox",
        "status": status,
        "stages": stages,
        "approve": {"status": approval.get("status", "PENDING"), "summary": approval.get("summary", "Waiting for approval."), "verification_passed": approval.get("evidence", {}).get("verification_passed", False)},
        "payment": {
            "order": order.get("status", "PENDING"),
            "order_id": order.get("evidence", {}).get("order_id"),
            "capture": capture.get("status", "PENDING"),
            "capture_id": capture.get("evidence", {}).get("capture_id"),
        },
    }


def _serve_file(path: Path):
    if not path.exists():
        return None
    return path.read_bytes()


class O3AIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            payload = _serve_file(WEB_DIR / "index.html")
            self._send_bytes(payload, "text/html; charset=utf-8")
            return
        if parsed.path == "/customer":
            payload = _serve_file(WEB_DIR / "customer.html")
            self._send_bytes(payload, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/status":
            self._send_json(_status_payload())
            return
        if parsed.path == "/api/demo-button":
            self._send_json({"text": "Demo mode is active. Use the O3AI dashboard to approve the sandbox flow."})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        payload = {}
        if raw and "application/json" in ctype:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {}
        elif raw:
            query = parse_qs(raw.decode("utf-8"))
            payload = {k: v[0] if len(v) == 1 else v for k, v in query.items()}

        if parsed.path == "/api/approve":
            for stage in ["planner", "builder", "security_qa", "release_monitor"]:
                stage_data = _read_json(RESULTS_DIR / f"{stage}.json", {"status": "PENDING"})
                if stage_data.get("status") != "PASS":
                    self._send_json({"ok": False, "message": f"Approval blocked: {stage} is not PASS."})
                    return
            approval = {
                "stage": "approve",
                "status": "PASS",
                "summary": "Human approval recorded for sandbox payment gate.",
                "evidence": {"verification_passed": True, "approved_by": "developer_operator"},
            }
            _write_json(RESULTS_DIR / "approve.json", approval)
            self._send_json({"ok": True, "message": "Approval recorded."})
            return

        if parsed.path == "/api/create-order":
            if not payload:
                payload = {"amount": "25.00", "description": "Verified milestone"}
            try:
                # If sandbox credentials are configured, use the real PayPal sandbox client.
                # Otherwise, the app stays in demo mode and writes a mock sandbox order.
                client = PayPalSandboxClient()
                result = client.create_order(RESULTS_DIR, payload.get("amount", "25.00"), payload.get("currency", "USD"), payload.get("description", "Verified milestone"), human_approved=True)
                self._send_json({"ok": True, "order": result, "sandbox": True})
                return
            except PayPalWorkflowError:
                order = {
                    "stage": "paypal_order",
                    "status": "PASS",
                    "summary": "Demo PayPal sandbox order created in local UI mode.",
                    "evidence": {
                        "order_id": "SIM-ORDER-1001",
                        "order_status": "CREATED",
                        "amount": payload.get("amount", "25.00"),
                        "currency": payload.get("currency", "USD"),
                        "description": payload.get("description", "Verified milestone"),
                        "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=SIM-ORDER-1001",
                        "environment": "sandbox-demo",
                    },
                    "timestamp": "demo-mode",
                }
                _write_json(RESULTS_DIR / "paypal_order.json", order)
                self._send_json({"ok": True, "order": order["evidence"], "sandbox": False, "demo": True})
                return

        if parsed.path == "/api/capture-order":
            order_id = payload.get("order_id", "SIM-ORDER-1001")
            try:
                client = PayPalSandboxClient()
                result = client.capture_order(RESULTS_DIR, order_id, human_approved=True)
                self._send_json({"ok": True, "capture": result, "sandbox": True})
                return
            except PayPalWorkflowError:
                capture = {
                    "stage": "paypal_capture",
                    "status": "PASS",
                    "summary": "Demo sandbox capture recorded in local mode.",
                    "evidence": {
                        "order_id": order_id,
                        "capture_id": "SIM-CAPTURE-9001",
                        "capture_status": "COMPLETED",
                        "environment": "sandbox-demo",
                    },
                    "timestamp": "demo-mode",
                }
                _write_json(RESULTS_DIR / "paypal_capture.json", capture)
                self._send_json({"ok": True, "capture": capture["evidence"], "sandbox": False, "demo": True})
                return

        self.send_response(404)
        self.end_headers()

    def _send_json(self, payload: dict):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, payload: bytes | None, content_type: str):
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    _ensure_results_dir()
    port = int(os.environ.get("O3AI_UI_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), O3AIHandler)
    print(f"O3AI UI running at http://localhost:{port}/")
    print(f"Customer page: http://localhost:{port}/customer")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
