"""Offline tests for PayPal integration and evidence-gate enforcement."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from o3ai.paypal import PayPalSandboxClient, PayPalWorkflowError, _validate_amount, verify_o3ai_gate


class GateTests(unittest.TestCase):
    def make_gate(self, root: Path) -> None:
        fixtures = {
            "planner": {"is_dag": True, "tasks": [{"task_id": "one"}], "task_count": 1},
            "builder": {"target_files": ["app.py"], "exit_code": 0},
            "security_qa": {"secrets_found": 0, "tests_passed": True},
            "release_monitor": {"version": "0.1.0", "rollback_defined": True},
        }
        for stage, evidence in fixtures.items():
            (root / f"{stage}.json").write_text(json.dumps({"status": "PASS", "evidence": evidence}))
        (root / "approve.json").write_text(json.dumps({"status": "PASS", "evidence": {"verification_passed": True}}))

    def test_requires_all_tower_proofs_and_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(PayPalWorkflowError):
                verify_o3ai_gate(root)
            self.make_gate(root)
            self.assertEqual(verify_o3ai_gate(root)["approval_status"], "PASS")

    def test_failed_or_unapproved_gate_blocks_order_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "approve.json").write_text(json.dumps({"status": "FAIL", "evidence": {"verification_passed": False}}))
            client = PayPalSandboxClient("id", "secret")
            with patch.object(client, "_request") as request:
                with self.assertRaises(PayPalWorkflowError):
                    client.create_order(root, "10.00", "USD", "Verified milestone", human_approved=True)
                request.assert_not_called()

    def test_explicit_human_confirmation_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            client = PayPalSandboxClient("id", "secret")
            with patch.object(client, "_request") as request:
                with self.assertRaises(PayPalWorkflowError):
                    client.create_order(root, "10.00", "USD", "Milestone")
                request.assert_not_called()

    def test_amount_validation(self):
        self.assertEqual(_validate_amount("12", "USD"), "12.00")
        for amount, currency in (("0", "USD"), ("-1", "USD"), ("1.001", "USD"), ("1", "US")):
            with self.subTest(amount=amount, currency=currency), self.assertRaises(PayPalWorkflowError):
                _validate_amount(amount, currency)

    def test_create_order_writes_evidence_after_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            client = PayPalSandboxClient("id", "secret")
            with patch.object(client, "_request", return_value={
                "id": "TEST-ORDER-12345", "status": "CREATED",
                "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=TEST"}],
            }):
                result = client.create_order(root, "25.00", "USD", "Verified work", human_approved=True)
            self.assertEqual(result["order_id"], "TEST-ORDER-12345")
            self.assertEqual(json.loads((root / "paypal_order.json").read_text())["status"], "PASS")

    def test_capture_requires_matching_recorded_sandbox_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "paypal_order.json").write_text(json.dumps({"status": "PASS", "evidence": {
                "order_id": "TEST-ORDER-12345", "environment": "sandbox"}}))
            client = PayPalSandboxClient("id", "secret")
            with patch.object(client, "_request", return_value={
                "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
                    {"id": "CAPTURE-12345", "status": "COMPLETED"}]}}],
            }):
                result = client.capture_order(root, "TEST-ORDER-12345", human_approved=True)
            self.assertEqual(result["capture_status"], "COMPLETED")
            self.assertEqual(json.loads((root / "paypal_capture.json").read_text())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
