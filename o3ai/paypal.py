"""PayPal sandbox execution, gated by O3AI's evidence and human confirmations.

O3AI remains the decision engine. This adapter can only call PayPal's sandbox
host; live API hosts are intentionally not configurable.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from o3ai.security import assert_safe_path

SANDBOX_API = "https://api-m.sandbox.paypal.com"
REQUIRED_TOWER_STAGES = ("planner", "builder", "security_qa", "release_monitor")


class PayPalWorkflowError(RuntimeError):
    """Raised when O3AI evidence, human approval, or a PayPal response is invalid."""


def verify_o3ai_gate(results_dir: Path) -> dict[str, Any]:
    """Validate complete O3AI floor evidence and the existing approval-stage proof."""
    results_dir = results_dir.resolve()
    proofs: dict[str, Any] = {}
    for stage in REQUIRED_TOWER_STAGES:
        path = assert_safe_path(results_dir / f"{stage}.json", results_dir)
        if not path.is_file():
            raise PayPalWorkflowError(f"O3AI gate blocked: missing {stage}.json evidence.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PayPalWorkflowError(f"O3AI gate blocked: invalid {stage}.json evidence.") from exc
        evidence = data.get("evidence") if isinstance(data, dict) else None
        if data.get("status") != "PASS" or not isinstance(evidence, dict):
            raise PayPalWorkflowError(f"O3AI gate blocked: {stage} evidence is not PASS.")
        if stage == "planner" and not (evidence.get("is_dag") is True and isinstance(evidence.get("tasks"), list)
                                        and evidence.get("task_count", 0) >= 1):
            raise PayPalWorkflowError("O3AI gate blocked: planner DAG evidence is incomplete.")
        if stage == "builder" and not (evidence.get("exit_code") == 0 and isinstance(evidence.get("target_files"), list)):
            raise PayPalWorkflowError("O3AI gate blocked: builder verification is incomplete.")
        if stage == "security_qa" and not (evidence.get("secrets_found") == 0 and evidence.get("tests_passed") is True):
            raise PayPalWorkflowError("O3AI gate blocked: security/QA evidence is incomplete.")
        if stage == "release_monitor" and not (evidence.get("rollback_defined") is True and evidence.get("version")):
            raise PayPalWorkflowError("O3AI gate blocked: release/rollback evidence is incomplete.")
        proofs[stage] = data

    approval_path = assert_safe_path(results_dir / "approve.json", results_dir)
    if not approval_path.is_file():
        raise PayPalWorkflowError("O3AI gate blocked: human approval (approve.json) is missing.")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PayPalWorkflowError("O3AI gate blocked: invalid human approval evidence.") from exc
    approval_evidence = approval.get("evidence", {}) if isinstance(approval, dict) else {}
    if (not isinstance(approval, dict) or approval.get("status") != "PASS"
            or not isinstance(approval_evidence, dict)
            or approval_evidence.get("verification_passed") is not True):
        raise PayPalWorkflowError("O3AI gate blocked: human approval has not passed.")
    return {"tower_stages": list(REQUIRED_TOWER_STAGES), "approval_status": "PASS"}


def _validate_amount(amount: str, currency: str) -> str:
    try:
        value = Decimal(amount)
    except (InvalidOperation, TypeError) as exc:
        raise PayPalWorkflowError("Amount must be positive with at most two decimal places.") from exc
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -2:
        raise PayPalWorkflowError("Amount must be positive with at most two decimal places.")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise PayPalWorkflowError("Currency must be a three-letter uppercase code, e.g. USD.")
    return format(value, ".2f")


def _write_evidence(results_dir: Path, filename: str, payload: dict[str, Any]) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    target = assert_safe_path(results_dir / filename, results_dir)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


class PayPalSandboxClient:
    """Small standard-library PayPal REST client; credentials are read from env vars."""

    def __init__(self, client_id: str | None = None, client_secret: str | None = None) -> None:
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self._token: str | None = None

    def _send(self, url: str, method: str, body: bytes | None, headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Keep credentials and raw response bodies out of logs/errors.
            raise PayPalWorkflowError(f"PayPal sandbox returned HTTP {exc.code}; verify the sandbox app and request.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PayPalWorkflowError("PayPal sandbox request failed; check connectivity and sandbox configuration.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PayPalWorkflowError("PayPal sandbox returned an invalid response.") from exc
        if not isinstance(parsed, dict):
            raise PayPalWorkflowError("PayPal sandbox returned an unexpected response.")
        return parsed

    def _access_token(self) -> str:
        if self._token:
            return self._token
        if not self.client_id or not self.client_secret:
            raise PayPalWorkflowError("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET for a sandbox app.")
        encoded = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode("ascii")
        response = self._send(
            f"{SANDBOX_API}/v1/oauth2/token", "POST", b"grant_type=client_credentials",
            {"Authorization": f"Basic {encoded}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise PayPalWorkflowError("PayPal sandbox authentication failed; verify sandbox credentials.")
        self._token = token
        return token

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._send(
            f"{SANDBOX_API}{path}", method, body,
            {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json",
                "PayPal-Request-Id": str(uuid.uuid4())},
        )

    def create_order(self, results_dir: Path, amount: str, currency: str, description: str,
                     *, human_approved: bool = False) -> dict[str, Any]:
        """Create sandbox order only after evidence, approval proof, and explicit confirmation."""
        gate = verify_o3ai_gate(results_dir)
        if human_approved is not True:
            raise PayPalWorkflowError("Payment not sent: explicitly confirm the O3AI-approved sandbox order.")
        amount = _validate_amount(amount, currency)
        description = description.strip()
        if not description or len(description) > 127:
            raise PayPalWorkflowError("Description must contain 1–127 characters.")
        response = self._request("POST", "/v2/checkout/orders", {
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": currency, "value": amount}, "description": description}],
            "application_context": {"user_action": "PAY_NOW"},
        })
        order_id = response.get("id")
        if not isinstance(order_id, str) or not re.fullmatch(r"[A-Z0-9-]{5,64}", order_id):
            raise PayPalWorkflowError("PayPal sandbox did not return a valid order ID.")
        approval_url = next((link.get("href") for link in response.get("links", [])
                             if isinstance(link, dict) and link.get("rel") in {"approve", "payer-action"}), None)
        evidence = {
            "stage": "paypal_order", "status": "PASS",
            "summary": "PayPal sandbox order created after O3AI evidence and approval.",
            "evidence": {**gate, "order_id": order_id, "order_status": response.get("status", "CREATED"),
                         "amount": amount, "currency": currency, "description": description,
                         "approval_url": approval_url, "environment": "sandbox"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_evidence(results_dir, "paypal_order.json", evidence)
        return evidence["evidence"]

    def capture_order(self, results_dir: Path, order_id: str, *, human_approved: bool = False) -> dict[str, Any]:
        """Capture a buyer-approved sandbox order, rechecking O3AI and explicit confirmation."""
        gate = verify_o3ai_gate(results_dir)
        if human_approved is not True:
            raise PayPalWorkflowError("Capture blocked: explicitly confirm this sandbox capture.")
        if not re.fullmatch(r"[A-Z0-9-]{5,64}", order_id):
            raise PayPalWorkflowError("Invalid PayPal order ID.")
        order_path = assert_safe_path(results_dir.resolve() / "paypal_order.json", results_dir.resolve())
        try:
            order_evidence = json.loads(order_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PayPalWorkflowError("Create an O3AI-gated sandbox order before capturing it.") from exc
        recorded = order_evidence.get("evidence", {}) if isinstance(order_evidence, dict) else {}
        if (not isinstance(order_evidence, dict) or order_evidence.get("status") != "PASS"
                or recorded.get("order_id") != order_id or recorded.get("environment") != "sandbox"):
            raise PayPalWorkflowError("Capture blocked: order is not a recorded O3AI sandbox order.")
        response = self._request("POST", f"/v2/checkout/orders/{order_id}/capture", {})
        captures = [capture for unit in response.get("purchase_units", []) if isinstance(unit, dict)
                    for capture in unit.get("payments", {}).get("captures", []) if isinstance(capture, dict)]
        if response.get("status") != "COMPLETED" or not captures or captures[0].get("status") != "COMPLETED":
            raise PayPalWorkflowError("Capture is not completed; no payment is reported as successful.")
        capture = captures[0]
        evidence = {
            "stage": "paypal_capture", "status": "PASS",
            "summary": "Buyer-approved PayPal sandbox order captured; O3AI gate re-verified.",
            "evidence": {**gate, "order_id": order_id, "capture_id": capture.get("id"),
                         "capture_status": capture.get("status"), "environment": "sandbox"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_evidence(results_dir, "paypal_capture.json", evidence)
        return evidence["evidence"]
