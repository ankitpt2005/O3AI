"""Tests for the operator reject route of the O3AI web app."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.paypal import PayPalSandboxClient
from integrations.web_app import O3AIWebApp

OPERATOR_TOKEN = "test-op-token-123"
AUTH = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
FLOOR_FIXTURES = {
    "planner": {"is_dag": True, "tasks": [{"task_id": "t1"}], "task_count": 1},
    "builder": {"target_files": ["sample_app/signup.py"], "exit_code": 0},
    "security_qa": {"secrets_found": 0, "tests_passed": True},
    "release_monitor": {"version": "0.1.0", "rollback_defined": True},
}


def _write_passing_floors(results_dir: Path) -> None:
    for stage, evidence in FLOOR_FIXTURES.items():
        (results_dir / f"{stage}.json").write_text(
            json.dumps(
                {
                    "stage": stage,
                    "status": "PASS",
                    "summary": f"{stage} passed",
                    "evidence": evidence,
                    "timestamp": f"2026-11-12T10:00:00Z-{stage}",
                }
            ),
            encoding="utf-8",
        )


class OperatorRejectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results_dir = Path(self.tmp.name) / "results"
        self.results_dir.mkdir(parents=True)
        self.client = PayPalSandboxClient("sandbox-id", "sandbox-secret")
        self.app = O3AIWebApp(
            results_dir=self.results_dir,
            web_dir=Path(__file__).resolve().parent.parent / "web",
            operator_token=OPERATOR_TOKEN,
            paypal_client_factory=lambda: self.client,
        )

    def _post(self, path: str, body: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
        req_headers = {"Host": "127.0.0.1:8080", "Content-Type": "application/json", **(headers or {})}
        status, _, raw = self.app.dispatch("POST", path, req_headers, json.dumps(body).encode("utf-8"))
        return status, json.loads(raw.decode("utf-8"))

    def _get(self, path: str) -> dict:
        status, _, raw = self.app.dispatch("GET", path, {"Host": "127.0.0.1:8080"})
        self.assertEqual(status, 200)
        return json.loads(raw.decode("utf-8"))

    def _approve(self) -> None:
        status, _ = self._post("/api/operator/approve", {"confirm_phrase": "APPROVE"}, AUTH)
        self.assertEqual(status, 200)

    def _approval_doc(self) -> dict:
        return json.loads((self.results_dir / "approve.json").read_text(encoding="utf-8"))

    def test_reject_requires_operator_auth(self) -> None:
        _write_passing_floors(self.results_dir)
        status, _ = self._post("/api/operator/reject", {"confirm_phrase": "REJECT"})
        self.assertEqual(status, 401)
        status, _ = self._post(
            "/api/operator/reject",
            {"confirm_phrase": "REJECT"},
            {"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(status, 401)
        self.assertFalse((self.results_dir / "approve.json").exists())

    def test_reject_requires_explicit_phrase_and_valid_reason(self) -> None:
        _write_passing_floors(self.results_dir)
        for body in (
            {},
            {"confirm_phrase": "APPROVE"},
            {"confirm_phrase": "reject"},
            {"confirm_phrase": "REJECT", "reason": 123},
            {"confirm_phrase": "REJECT", "reason": "x" * 301},
        ):
            with self.subTest(body=body):
                status, data = self._post("/api/operator/reject", body, AUTH)
                self.assertEqual(status, 400)
                self.assertFalse(data["ok"])
        self.assertFalse((self.results_dir / "approve.json").exists())

    def test_reject_records_fail_decision_and_blocks_gate(self) -> None:
        _write_passing_floors(self.results_dir)
        self._approve()
        self.assertEqual(self._get("/api/status")["status"], "READY")

        status, data = self._post(
            "/api/operator/reject",
            {"confirm_phrase": "REJECT", "reason": "Evidence does not\x00 match the milestone"},
            AUTH,
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

        doc = self._approval_doc()
        self.assertEqual(doc["status"], "FAIL")
        evidence = doc["evidence"]
        self.assertFalse(evidence["verification_passed"])
        self.assertEqual(evidence["rejected_by"], "developer_operator")
        self.assertEqual(evidence["rejection_reason"], "Evidence does not match the milestone")
        self.assertEqual(set(evidence["stage_timestamps"]), set(FLOOR_FIXTURES))

        dash = self._get("/api/status")
        self.assertEqual(dash["status"], "BLOCKED")
        self.assertEqual(dash["approve"]["status"], "FAIL")
        self.assertFalse(dash["approve"]["verification_passed"])
        self.assertTrue(any("rejected" in reason.lower() for reason in dash["blocked_reasons"]))
        self.assertIn("operator_rejected", [entry["event"] for entry in dash["activity_trail"]])

        customer = self._get("/api/customer/milestone")
        self.assertFalse(customer["gate_ready"])
        self.assertFalse(customer["operator_approved"])

    def test_rejection_blocks_order_and_capture_before_any_paypal_call(self) -> None:
        _write_passing_floors(self.results_dir)
        self._approve()
        status, _ = self._post("/api/operator/reject", {"confirm_phrase": "REJECT"}, AUTH)
        self.assertEqual(status, 200)

        with patch.object(self.client, "_request") as request:
            status, data = self._post(
                "/api/operator/create-order",
                {"confirm_phrase": "APPROVE", "amount": "25.00", "currency": "USD", "description": "Milestone"},
                AUTH,
            )
            self.assertEqual(status, 400)
            self.assertFalse(data["ok"])
            status, data = self._post(
                "/api/operator/capture-order",
                {"confirm_phrase": "APPROVE", "order_id": "SBX-ORDER-12345"},
                AUTH,
            )
            self.assertEqual(status, 400)
            self.assertFalse(data["ok"])
            request.assert_not_called()
        self.assertFalse((self.results_dir / "paypal_order.json").exists())
        self.assertFalse((self.results_dir / "paypal_capture.json").exists())

    def test_explicit_reapproval_after_rejection_restores_ready(self) -> None:
        _write_passing_floors(self.results_dir)
        status, _ = self._post("/api/operator/reject", {"confirm_phrase": "REJECT"}, AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(self._get("/api/status")["status"], "BLOCKED")

        self._approve()
        self.assertEqual(self._approval_doc()["status"], "PASS")
        self.assertEqual(self._get("/api/status")["status"], "READY")

    def test_reject_refused_after_completed_capture(self) -> None:
        _write_passing_floors(self.results_dir)
        self._approve()
        (self.results_dir / "paypal_capture.json").write_text(
            json.dumps(
                {
                    "stage": "paypal_capture",
                    "status": "PASS",
                    "evidence": {"capture_status": "COMPLETED", "environment": "sandbox"},
                }
            ),
            encoding="utf-8",
        )
        status, data = self._post("/api/operator/reject", {"confirm_phrase": "REJECT"}, AUTH)
        self.assertEqual(status, 409)
        self.assertFalse(data["ok"])
        self.assertEqual(self._approval_doc()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
