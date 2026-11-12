"""Offline tests for PayPal Sandbox integration and evidence-gate enforcement."""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from integrations.paypal import (
    SANDBOX_API,
    PayPalSandboxClient,
    PayPalWorkflowError,
    _validate_amount,
    verify_o3ai_gate,
)


class GateTests(unittest.TestCase):
    def make_gate(self, root: Path, *, with_timestamps: bool = False) -> None:
        fixtures = {
            "planner": {"is_dag": True, "tasks": [{"task_id": "one"}], "task_count": 1},
            "builder": {"target_files": ["app.py"], "exit_code": 0},
            "security_qa": {"secrets_found": 0, "tests_passed": True},
            "release_monitor": {"version": "0.1.0", "rollback_defined": True},
        }
        timestamps = {}
        for stage, evidence in fixtures.items():
            ts = f"2026-11-12T10:00:00Z-{stage}"
            timestamps[stage] = ts
            (root / f"{stage}.json").write_text(
                json.dumps({"status": "PASS", "evidence": evidence, "timestamp": ts}),
                encoding="utf-8",
            )
        approve_ev: dict[str, object] = {"verification_passed": True, "approved_by": "developer_operator"}
        if with_timestamps:
            approve_ev["stage_timestamps"] = timestamps
        (root / "approve.json").write_text(
            json.dumps({"status": "PASS", "evidence": approve_ev}),
            encoding="utf-8",
        )

    def test_sandbox_host_is_fixed_https(self):
        self.assertEqual(SANDBOX_API, "https://api-m.sandbox.paypal.com")

    def test_requires_all_tower_proofs_and_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(PayPalWorkflowError):
                verify_o3ai_gate(root)
            self.make_gate(root)
            self.assertEqual(verify_o3ai_gate(root)["approval_status"], "PASS")

    def test_malformed_or_failed_floor_evidence_blocks_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "security_qa.json").write_text("{bad json", encoding="utf-8")
            with self.assertRaises(PayPalWorkflowError):
                verify_o3ai_gate(root)

            self.make_gate(root)
            (root / "planner.json").write_text(
                json.dumps({"status": "PASS", "evidence": {"is_dag": True, "tasks": [], "task_count": 0}}),
                encoding="utf-8",
            )
            with self.assertRaises(PayPalWorkflowError):
                verify_o3ai_gate(root)

    def test_failed_or_unapproved_gate_blocks_order_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "approve.json").write_text(
                json.dumps({"status": "FAIL", "evidence": {"verification_passed": False}}),
                encoding="utf-8",
            )
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

    def test_amount_and_currency_validation(self):
        self.assertEqual(_validate_amount("12", "USD"), "12.00")
        for amount, currency in (
            ("0", "USD"),
            ("-1", "USD"),
            ("1.001", "USD"),
            ("1e2", "USD"),
            ("1", "US"),
            ("10.00", "usd"),
            ("abc", "USD"),
        ):
            with self.subTest(amount=amount, currency=currency), self.assertRaises(PayPalWorkflowError):
                _validate_amount(amount, currency)

    def test_create_order_writes_evidence_after_gate_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            client = PayPalSandboxClient("id", "secret")
            with patch.object(
                client,
                "_request",
                return_value={
                    "id": "TEST-ORDER-12345",
                    "status": "CREATED",
                    "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=TEST"}],
                },
            ) as req:
                result = client.create_order(root, "25.00", "USD", "Verified work", human_approved=True)
                reused = client.create_order(root, "25.00", "USD", "Verified work", human_approved=True)
            self.assertEqual(req.call_count, 1)
            self.assertEqual(result["order_id"], "TEST-ORDER-12345")
            self.assertFalse(result["buyer_approved"])
            self.assertTrue(reused["idempotent_reuse"])
            self.assertEqual(json.loads((root / "paypal_order.json").read_text())["status"], "PASS")

    def test_create_order_rejects_bad_paypal_response_without_fake_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            client = PayPalSandboxClient("id", "super-secret-value")
            with patch.object(client, "_request", return_value={"id": "TEST-ORDER-12345", "links": []}):
                with self.assertRaises(PayPalWorkflowError):
                    client.create_order(root, "25.00", "USD", "Verified work", human_approved=True)
            self.assertFalse((root / "paypal_order.json").exists())

            with patch.object(
                client,
                "_request",
                return_value={
                    "id": "TEST-ORDER-12345",
                    "links": [{"rel": "approve", "href": "https://evil.example.com/pay"}],
                },
            ):
                with self.assertRaises(PayPalWorkflowError):
                    client.create_order(root, "25.00", "USD", "Verified work", human_approved=True)
            self.assertFalse((root / "paypal_order.json").exists())

    def test_api_failure_does_not_leak_credentials_or_write_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            client = PayPalSandboxClient("MY_SANDBOX_ID", "MY_SUPER_SECRET_12345")
            err = urllib.error.HTTPError(
                "https://api-m.sandbox.paypal.com/v1/oauth2/token",
                401,
                "Unauthorized",
                {},
                None,
            )
            self.addCleanup(err.close)
            with patch("integrations.paypal.urllib.request.urlopen", side_effect=err):
                with self.assertRaises(PayPalWorkflowError) as ctx:
                    client.create_order(root, "25.00", "USD", "Verified work", human_approved=True)
            self.assertNotIn("MY_SUPER_SECRET_12345", str(ctx.exception))
            self.assertNotIn("MY_SANDBOX_ID", str(ctx.exception))
            self.assertFalse((root / "paypal_order.json").exists())

    def test_capture_blocked_when_buyer_not_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "paypal_order.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "evidence": {
                            "order_id": "TEST-ORDER-12345",
                            "order_status": "CREATED",
                            "buyer_approved": False,
                            "environment": "sandbox",
                        },
                    }
                ),
                encoding="utf-8",
            )
            client = PayPalSandboxClient("id", "secret")
            with patch.object(
                client,
                "_request",
                return_value={"id": "TEST-ORDER-12345", "status": "CREATED"},
            ):
                with self.assertRaises(PayPalWorkflowError):
                    client.capture_order(root, "TEST-ORDER-12345", human_approved=True)
            self.assertFalse((root / "paypal_capture.json").exists())

    def test_buyer_approved_then_capture_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "paypal_order.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "evidence": {
                            "order_id": "TEST-ORDER-12345",
                            "order_status": "CREATED",
                            "buyer_approved": False,
                            "amount": "25.00",
                            "currency": "USD",
                            "environment": "sandbox",
                        },
                    }
                ),
                encoding="utf-8",
            )
            client = PayPalSandboxClient("id", "secret")
            with patch.object(
                client,
                "_request",
                side_effect=[
                    {"id": "TEST-ORDER-12345", "status": "APPROVED"},
                    {
                        "status": "COMPLETED",
                        "purchase_units": [
                            {"payments": {"captures": [{"id": "CAPTURE-12345", "status": "COMPLETED"}]}}
                        ],
                    },
                ],
            ):
                verified = client.verify_buyer_approval(root, "TEST-ORDER-12345")
                self.assertTrue(verified["buyer_approved"])
                result = client.capture_order(root, "TEST-ORDER-12345", human_approved=True)
            self.assertEqual(result["capture_status"], "COMPLETED")
            self.assertEqual(json.loads((root / "paypal_capture.json").read_text())["status"], "PASS")

    def test_capture_failed_writes_fail_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root)
            (root / "paypal_order.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "evidence": {
                            "order_id": "TEST-ORDER-12345",
                            "order_status": "APPROVED",
                            "buyer_approved": True,
                            "environment": "sandbox",
                        },
                    }
                ),
                encoding="utf-8",
            )
            client = PayPalSandboxClient("id", "secret")
            with patch.object(
                client,
                "_request",
                return_value={"status": "DECLINED", "purchase_units": []},
            ):
                with self.assertRaises(PayPalWorkflowError):
                    client.capture_order(root, "TEST-ORDER-12345", human_approved=True)
            cap_doc = json.loads((root / "paypal_capture.json").read_text())
            self.assertEqual(cap_doc["status"], "FAIL")

    def test_capture_blocked_after_evidence_or_approval_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_gate(root, with_timestamps=True)
            (root / "paypal_order.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "evidence": {
                            "order_id": "TEST-ORDER-12345",
                            "order_status": "APPROVED",
                            "buyer_approved": True,
                            "environment": "sandbox",
                        },
                    }
                ),
                encoding="utf-8",
            )
            # Floor evidence timestamp changes after approval (stale approval).
            (root / "builder.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "evidence": {"target_files": ["app.py"], "exit_code": 0},
                        "timestamp": "2026-11-12T11:00:00Z-newer",
                    }
                ),
                encoding="utf-8",
            )
            client = PayPalSandboxClient("id", "secret")
            with patch.object(client, "_request") as req:
                with self.assertRaises(PayPalWorkflowError):
                    client.capture_order(root, "TEST-ORDER-12345", human_approved=True)
                req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
