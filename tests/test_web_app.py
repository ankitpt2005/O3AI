"""Integration tests for O3AI Developer/Operator Dashboard & Customer Web App."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.paypal import PayPalSandboxClient, PayPalWorkflowError
from integrations.web_app import O3AIWebApp, create_server


def _write_passing_floors(results_dir: Path) -> None:
    fixtures = {
        "planner": {"is_dag": True, "tasks": [{"task_id": "t1"}], "task_count": 1},
        "builder": {"target_files": ["sample_app/signup.py"], "exit_code": 0},
        "security_qa": {"secrets_found": 0, "tests_passed": True},
        "release_monitor": {"version": "0.1.0", "rollback_defined": True},
    }
    for stage, ev in fixtures.items():
        (results_dir / f"{stage}.json").write_text(
            json.dumps(
                {
                    "stage": stage,
                    "status": "PASS",
                    "summary": f"{stage} passed",
                    "evidence": ev,
                    "timestamp": f"2026-11-12T10:00:00Z-{stage}",
                }
            ),
            encoding="utf-8",
        )


def _header(headers: list[tuple[str, str]], name: str) -> str:
    lower = name.lower()
    for k, v in headers:
        if k.lower() == lower:
            return v
    return ""


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.tmp.name) / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.web_dir = Path(__file__).resolve().parent.parent / "web"
        self.mock_client = PayPalSandboxClient("sandbox-id", "sandbox-secret")
        self.app = O3AIWebApp(
            results_dir=self.results_dir,
            web_dir=self.web_dir,
            operator_token="test-op-token-123",
            paypal_client_factory=lambda: self.mock_client,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _req(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, list[tuple[str, str]], str]:
        req_headers = {"Host": "127.0.0.1:8080", **(headers or {})}
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        status, resp_headers, resp_bytes = self.app.dispatch(method, path, req_headers, raw)
        return status, resp_headers, resp_bytes.decode("utf-8")

    def _login(self) -> tuple[str, str]:
        status, headers, text = self._req("POST", "/api/operator/login", {"token": "test-op-token-123"})
        self.assertEqual(status, 200)
        data = json.loads(text)
        cookie = _header(headers, "Set-Cookie").split(";", 1)[0]
        self.assertIn("HttpOnly", _header(headers, "Set-Cookie"))
        self.assertIn("SameSite=Strict", _header(headers, "Set-Cookie"))
        return cookie, data["csrf_token"]

    def test_empty_results_never_bootstraps_fake_pass(self) -> None:
        status, _, text = self._req("GET", "/api/status")
        self.assertEqual(status, 200)
        data = json.loads(text)
        self.assertEqual(data["status"], "BLOCKED")
        for stage in ("planner", "builder", "security_qa", "release_monitor"):
            self.assertEqual(data["stages"][stage]["status"], "PENDING")
        self.assertFalse((self.results_dir / "approve.json").exists())
        self.assertFalse((self.results_dir / "paypal_order.json").exists())

    def test_operator_endpoints_require_auth_and_csrf(self) -> None:
        _write_passing_floors(self.results_dir)
        for path in (
            "/api/operator/init-session",
            "/api/operator/run-checks",
            "/api/operator/approve",
            "/api/operator/create-order",
            "/api/operator/capture-order",
            "/api/operator/milestone",
            "/api/operator/ai-plan",
        ):
            status, _, _ = self._req("POST", path, {"confirm_phrase": "APPROVE"})
            self.assertEqual(status, 401, msg=f"Expected 401 for unauthenticated {path}")

        status, _, _ = self._req("POST", "/api/operator/login", {"token": "wrong"})
        self.assertEqual(status, 401)

        cookie, csrf = self._login()
        status, _, _ = self._req(
            "POST",
            "/api/operator/approve",
            {"confirm_phrase": "APPROVE"},
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 403)

        status, _, _ = self._req(
            "POST",
            "/api/operator/approve",
            {"confirm_phrase": "APPROVE"},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_customer_views_never_leak_operator_token_or_allow_bypass(self) -> None:
        status, _, html_dev = self._req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("O3AI", html_dev)
        self.assertNotIn("test-op-token-123", html_dev)

        status, _, html_cust = self._req("GET", "/customer")
        self.assertEqual(status, 200)
        self.assertIn("PayPal Sandbox", html_cust)
        self.assertNotIn("test-op-token-123", html_cust)

        status, _, cust_json = self._req("GET", "/api/customer/milestone")
        self.assertEqual(status, 200)
        cust = json.loads(cust_json)
        self.assertFalse(cust["gate_ready"])
        self.assertEqual(cust["gate_status"], "BLOCKED")
        self.assertIsNone(cust["order"]["approval_url"])
        self.assertNotIn("test-op-token-123", cust_json)

    def test_approval_blocked_until_all_floors_pass(self) -> None:
        cookie, csrf = self._login()
        status, _, text = self._req(
            "POST",
            "/api/operator/approve",
            {"confirm_phrase": "APPROVE"},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 409)
        self.assertFalse(json.loads(text)["ok"])
        self.assertFalse((self.results_dir / "approve.json").exists())

    def test_paypal_failure_never_creates_fake_pass_order(self) -> None:
        _write_passing_floors(self.results_dir)
        cookie, csrf = self._login()
        self._req(
            "POST",
            "/api/operator/approve",
            {"confirm_phrase": "APPROVE"},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )

        with patch.object(
            self.mock_client,
            "_request",
            side_effect=PayPalWorkflowError("PayPal sandbox returned HTTP 500; verify the sandbox app and request."),
        ):
            status, _, text = self._req(
                "POST",
                "/api/operator/create-order",
                {"confirm_phrase": "APPROVE", "amount": "25.00", "currency": "USD", "description": "Milestone 1"},
                headers={"Cookie": cookie, "X-CSRF-Token": csrf},
            )
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(text)["ok"])
        self.assertFalse((self.results_dir / "paypal_order.json").exists())

    def test_end_to_end_mocked_sandbox_flow_and_stale_gate_block(self) -> None:
        _write_passing_floors(self.results_dir)
        cookie, csrf = self._login()
        headers = {"Cookie": cookie, "X-CSRF-Token": csrf}

        # 1. Operator approves
        status, _, _ = self._req("POST", "/api/operator/approve", {"confirm_phrase": "APPROVE"}, headers=headers)
        self.assertEqual(status, 200)

        # 2. Operator creates PayPal Sandbox order
        with patch.object(
            self.mock_client,
            "_request",
            return_value={
                "id": "SBX-ORDER-99999",
                "status": "CREATED",
                "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SBX-ORDER-99999"}],
            },
        ):
            status, _, text = self._req(
                "POST",
                "/api/operator/create-order",
                {"confirm_phrase": "APPROVE", "amount": "40.00", "currency": "USD", "description": "Signup validation"},
                headers=headers,
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["order"]["order_id"], "SBX-ORDER-99999")

        # 3. Customer sees READY + real PayPal approval URL
        _, _, cust_text = self._req("GET", "/api/customer/milestone")
        cust = json.loads(cust_text)
        self.assertTrue(cust["gate_ready"])
        self.assertEqual(cust["milestone"]["amount"], "40.00")
        self.assertEqual(
            cust["order"]["approval_url"],
            "https://www.sandbox.paypal.com/checkoutnow?token=SBX-ORDER-99999",
        )

        # 4. Customer returns from PayPal -> backend verifies buyer approval without capturing
        with patch.object(
            self.mock_client,
            "_request",
            return_value={"id": "SBX-ORDER-99999", "status": "APPROVED"},
        ):
            status, _, ret_text = self._req("GET", "/api/customer/verify-return?token=SBX-ORDER-99999")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(ret_text)["buyer_approved"])
        self.assertFalse((self.results_dir / "paypal_capture.json").exists())

        # 5. Operator explicitly captures
        with patch.object(
            self.mock_client,
            "_request",
            return_value={
                "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "CAP-77777", "status": "COMPLETED"}]}}],
            },
        ):
            status, _, cap_text = self._req(
                "POST",
                "/api/operator/capture-order",
                {"confirm_phrase": "APPROVE", "order_id": "SBX-ORDER-99999"},
                headers=headers,
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(cap_text)["capture"]["capture_id"], "CAP-77777")

        # 6. Dashboard reflects activity trail & distinct states
        _, _, dash_text = self._req("GET", "/api/status")
        dash = json.loads(dash_text)
        self.assertEqual(dash["paypal"]["order"]["order_id"], "SBX-ORDER-99999")
        self.assertEqual(dash["paypal"]["buyer_approval"]["state"], "APPROVED")
        self.assertEqual(dash["paypal"]["capture"]["state"], "COMPLETED")
        self.assertTrue(len(dash["activity_trail"]) >= 4)

    def test_refuses_non_loopback_without_operator_token(self) -> None:
        with patch.dict(os.environ, {"O3AI_OPERATOR_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                create_server("0.0.0.0", 0)

    def test_invalid_amount_currency_and_capture_guards_via_web_api(self) -> None:
        _write_passing_floors(self.results_dir)
        auth_header = {"Authorization": "Bearer test-op-token-123"}

        status, _, _ = self._req("POST", "/api/operator/approve", {"confirm_phrase": "APPROVE"}, headers=auth_header)
        self.assertEqual(status, 200)

        # Invalid amount / currency rejected without creating order
        for bad in (
            {"confirm_phrase": "APPROVE", "amount": "-5.00", "currency": "USD", "description": "Bad"},
            {"confirm_phrase": "APPROVE", "amount": "10.00", "currency": "usd", "description": "Bad"},
        ):
            status, _, _ = self._req("POST", "/api/operator/create-order", bad, headers=auth_header)
            self.assertEqual(status, 400)
        self.assertFalse((self.results_dir / "paypal_order.json").exists())

        # Create valid order
        with patch.object(
            self.mock_client,
            "_request",
            return_value={
                "id": "SBX-ORDER-22222",
                "status": "CREATED",
                "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SBX-ORDER-22222"}],
            },
        ):
            status, _, _ = self._req(
                "POST",
                "/api/operator/create-order",
                {"confirm_phrase": "APPROVE", "amount": "25.00", "currency": "USD", "description": "Valid"},
                headers=auth_header,
            )
            self.assertEqual(status, 200)

        # Capture blocked when buyer has not approved yet
        with patch.object(
            self.mock_client,
            "_request",
            return_value={"id": "SBX-ORDER-22222", "status": "CREATED"},
        ):
            status, _, text = self._req(
                "POST",
                "/api/operator/capture-order",
                {"confirm_phrase": "APPROVE", "order_id": "SBX-ORDER-22222"},
                headers=auth_header,
            )
            self.assertEqual(status, 400)
            self.assertIn("buyer has not approved", json.loads(text)["error"])

        # Buyer approves, then capture fails -> returns 400 and never writes PASS
        with patch.object(
            self.mock_client,
            "_request",
            return_value={"id": "SBX-ORDER-22222", "status": "APPROVED"},
        ):
            status, _, _ = self._req("GET", "/api/customer/verify-return?token=SBX-ORDER-22222")
            self.assertEqual(status, 200)

        with patch.object(
            self.mock_client,
            "_request",
            return_value={"status": "DECLINED", "purchase_units": []},
        ):
            status, _, _ = self._req(
                "POST",
                "/api/operator/capture-order",
                {"confirm_phrase": "APPROVE", "order_id": "SBX-ORDER-22222"},
                headers=auth_header,
            )
            self.assertEqual(status, 400)
        cap_doc = json.loads((self.results_dir / "paypal_capture.json").read_text(encoding="utf-8"))
        self.assertEqual(cap_doc["status"], "FAIL")

        # Floor evidence changes after approval -> capture blocked due to stale approval
        (self.results_dir / "builder.json").write_text(
            json.dumps(
                {
                    "stage": "builder",
                    "status": "PASS",
                    "evidence": {"target_files": ["sample_app/signup.py"], "exit_code": 0},
                    "timestamp": "2026-11-12T12:00:00Z-changed",
                }
            ),
            encoding="utf-8",
        )
        status, _, text = self._req(
            "POST",
            "/api/operator/capture-order",
            {"confirm_phrase": "APPROVE", "order_id": "SBX-ORDER-22222"},
            headers=auth_header,
        )
        self.assertEqual(status, 400)
        self.assertIn("re-approval required", json.loads(text)["error"])

    def test_ai_plan_route_stores_proposal_only_and_logout_invalidates_session(self) -> None:
        fake_plan = {
            "status": "PROPOSAL_ONLY",
            "is_evidence": False,
            "can_authorize_payment": False,
            "summary": "Proposed tasks",
            "tasks": [{"task_id": "p1", "name": "Plan", "description": "Inspect", "dependencies": []}],
            "evidence_questions": ["Did tests pass?"],
            "verification_note": "LLM proposal only; not Tower evidence and cannot authorize PayPal.",
        }

        def stub_ai(goal: str, out: Path) -> dict:
            out.write_text(json.dumps(fake_plan), encoding="utf-8")
            return fake_plan

        self.app.ai_planner_fn = stub_ai
        cookie, csrf = self._login()
        status, _, text = self._req(
            "POST",
            "/api/operator/ai-plan",
            {"goal": "Verify milestone"},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["proposal"]["status"], "PROPOSAL_ONLY")

        # Gate remains BLOCKED even after AI plan succeeds
        _, _, dash_text = self._req("GET", "/api/status")
        dash = json.loads(dash_text)
        self.assertEqual(dash["status"], "BLOCKED")
        self.assertEqual(dash["ai_proposal"]["status"], "PROPOSAL_ONLY")
        self.assertFalse(dash["ai_proposal"]["is_evidence"])

        # Logout invalidates session cookie
        status, _, _ = self._req("POST", "/api/operator/logout", {}, headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        status, _, _ = self._req(
            "POST",
            "/api/operator/ai-plan",
            {"goal": "Verify milestone"},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 401)

    def test_missing_approval_stage_timestamps_marks_stale_and_blocks(self) -> None:
        _write_passing_floors(self.results_dir)
        (self.results_dir / "approve.json").write_text(
            json.dumps(
                {
                    "stage": "approve",
                    "status": "PASS",
                    "evidence": {"verification_passed": True, "approved_by": "legacy"},
                    "timestamp": "2026-11-12T10:01:00Z",
                }
            ),
            encoding="utf-8",
        )
        _, _, dash_text = self._req("GET", "/api/status")
        dash = json.loads(dash_text)
        self.assertEqual(dash["status"], "BLOCKED")
        self.assertEqual(dash["approve"]["status"], "STALE")
        self.assertTrue(dash["approve"]["stale"])

    def test_init_session_and_run_checks_endpoints(self) -> None:
        def stub_checks(results_dir: Path, issue: object) -> dict:
            _write_passing_floors(results_dir)
            return {"lint_ok": True, "tests_ok": True, "tests_run": 82, "sec_ok": True, "secrets_found": 0}

        self.app.checks_runner_fn = stub_checks
        cookie, csrf = self._login()
        headers = {"Cookie": cookie, "X-CSRF-Token": csrf}

        # run-checks requires an active goal session first
        status, _, _ = self._req("POST", "/api/operator/run-checks", {}, headers=headers)
        self.assertEqual(status, 409)

        # Reject >35-word bounded title
        long_title = " ".join(["word"] * 36)
        status, _, _ = self._req(
            "POST",
            "/api/operator/init-session",
            {"issue_id": 102, "title": long_title, "description": "Valid"},
            headers=headers,
        )
        self.assertEqual(status, 400)

        # Valid session creation clears stale downstream files and runs checks cleanly
        (self.results_dir / "approve.json").write_text('{"status": "PASS"}', encoding="utf-8")
        status, _, init_text = self._req(
            "POST",
            "/api/operator/init-session",
            {
                "issue_id": 102,
                "title": "Verify email validation milestone",
                "description": "Ensure unit tests and security gate pass.",
                "amount": "15.00",
                "currency": "USD",
            },
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(init_text)["ok"])
        self.assertTrue((self.results_dir / "session.json").is_file())
        self.assertFalse((self.results_dir / "approve.json").exists())

        status, _, run_text = self._req("POST", "/api/operator/run-checks", {}, headers=headers)
        self.assertEqual(status, 200)
        run_data = json.loads(run_text)
        self.assertTrue(run_data["checks"]["tests_ok"])
        for stage in ("planner", "builder", "security_qa", "release_monitor"):
            self.assertEqual(run_data["stages"][stage]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
