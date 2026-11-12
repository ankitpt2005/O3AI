"""PayPal sandbox execution, gated by O3AI's evidence and human confirmations.

O3AI remains the decision engine. This adapter can only call PayPal's sandbox
host (https://api-m.sandbox.paypal.com); live API hosts are intentionally not
configurable.
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
from urllib.parse import urlparse

from o3ai.evidence import derive_floor_status
from o3ai.models import StageStatus
from o3ai.security import assert_safe_path, sanitize_untrusted_text

SANDBOX_API = "https://api-m.sandbox.paypal.com"
REQUIRED_TOWER_STAGES = ("planner", "builder", "security_qa", "release_monitor")
_ORDER_ID_RE = re.compile(r"^[A-Z0-9-]{5,64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_AMOUNT_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")


class PayPalWorkflowError(RuntimeError):
    """Raised when O3AI evidence, human approval, or a PayPal response is invalid."""


def _read_json_dict(path: Path, error_message: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PayPalWorkflowError(error_message) from exc
    if not isinstance(data, dict):
        raise PayPalWorkflowError(error_message)
    return data


def verify_o3ai_gate(results_dir: Path) -> dict[str, Any]:
    """Validate complete O3AI floor evidence and human approval proof."""
    results_dir = results_dir.resolve()
    floor_timestamps: dict[str, Any] = {}

    for stage in REQUIRED_TOWER_STAGES:
        path = assert_safe_path(results_dir / f"{stage}.json", results_dir)
        if not path.is_file():
            raise PayPalWorkflowError(f"O3AI gate blocked: missing {stage}.json evidence.")
        derived = derive_floor_status(stage, results_dir)
        if derived != StageStatus.PASS:
            raise PayPalWorkflowError(
                f"O3AI gate blocked: {stage} derived status is {derived.value}, expected PASS."
            )
        data = _read_json_dict(path, f"O3AI gate blocked: invalid {stage}.json evidence.")
        evidence = data.get("evidence")
        if data.get("status") != "PASS" or not isinstance(evidence, dict):
            raise PayPalWorkflowError(f"O3AI gate blocked: {stage} evidence is not PASS.")
        if stage == "planner" and not (
            evidence.get("is_dag") is True
            and isinstance(evidence.get("tasks"), list)
            and len(evidence.get("tasks", [])) >= 1
            and type(evidence.get("task_count")) is int
            and evidence.get("task_count", 0) >= 1
        ):
            raise PayPalWorkflowError("O3AI gate blocked: planner DAG evidence is incomplete.")
        if stage == "builder" and not (
            type(evidence.get("exit_code")) is int
            and evidence.get("exit_code") == 0
            and isinstance(evidence.get("target_files"), list)
        ):
            raise PayPalWorkflowError("O3AI gate blocked: builder verification is incomplete.")
        if stage == "security_qa" and not (
            type(evidence.get("secrets_found")) is int
            and evidence.get("secrets_found") == 0
            and evidence.get("tests_passed") is True
        ):
            raise PayPalWorkflowError("O3AI gate blocked: security/QA evidence is incomplete.")
        if stage == "release_monitor" and not (
            evidence.get("rollback_defined") is True
            and isinstance(evidence.get("version"), str)
            and bool(evidence.get("version", "").strip())
        ):
            raise PayPalWorkflowError("O3AI gate blocked: release/rollback evidence is incomplete.")
        floor_timestamps[stage] = data.get("timestamp")

    approval_path = assert_safe_path(results_dir / "approve.json", results_dir)
    if not approval_path.is_file():
        raise PayPalWorkflowError("O3AI gate blocked: human approval (approve.json) is missing.")
    approval = _read_json_dict(approval_path, "O3AI gate blocked: invalid human approval evidence.")
    approval_evidence = approval.get("evidence")
    if (
        approval.get("status") != "PASS"
        or not isinstance(approval_evidence, dict)
        or approval_evidence.get("verification_passed") is not True
    ):
        raise PayPalWorkflowError("O3AI gate blocked: human approval has not passed.")

    recorded_timestamps = approval_evidence.get("stage_timestamps")
    if isinstance(recorded_timestamps, dict):
        for stage in REQUIRED_TOWER_STAGES:
            if recorded_timestamps.get(stage) != floor_timestamps.get(stage):
                raise PayPalWorkflowError(
                    f"O3AI gate blocked: {stage} evidence changed after approval; re-approval required."
                )

    return {
        "tower_stages": list(REQUIRED_TOWER_STAGES),
        "approval_status": "PASS",
        "approved_by": approval_evidence.get("approved_by", "human_reviewer"),
    }


def _validate_amount(amount: str, currency: str) -> str:
    if not isinstance(amount, str) or not _AMOUNT_RE.fullmatch(amount.strip()):
        raise PayPalWorkflowError("Amount must be positive with at most two decimal places.")
    try:
        value = Decimal(amount.strip())
    except (InvalidOperation, TypeError) as exc:
        raise PayPalWorkflowError("Amount must be positive with at most two decimal places.") from exc
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -2:
        raise PayPalWorkflowError("Amount must be positive with at most two decimal places.")
    if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency.strip()):
        raise PayPalWorkflowError("Currency must be a three-letter uppercase code, e.g. USD.")
    return format(value, ".2f")


def _validate_description(description: str) -> str:
    if not isinstance(description, str):
        raise PayPalWorkflowError("Description must contain 1–127 characters.")
    cleaned = sanitize_untrusted_text(description).strip()
    if not cleaned or len(cleaned) > 127:
        raise PayPalWorkflowError("Description must contain 1–127 characters.")
    return cleaned


def _validate_sandbox_approval_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise PayPalWorkflowError("PayPal sandbox did not return an approval URL.")
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "sandbox.paypal.com" or host.endswith(".sandbox.paypal.com")):
        raise PayPalWorkflowError("PayPal sandbox returned an invalid or non-sandbox approval URL.")
    return url.strip()


def _validate_redirect_url(url: str | None) -> str | None:
    if url is None:
        return None
    if not isinstance(url, str) or not url.strip():
        raise PayPalWorkflowError("Invalid redirect URL.")
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and bool(parsed.netloc):
        return url.strip()
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost"}:
        return url.strip()
    raise PayPalWorkflowError("Redirect URL must use HTTPS or localhost.")


def _write_evidence(results_dir: Path, filename: str, payload: dict[str, Any]) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    target = assert_safe_path(results_dir / filename, results_dir)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


class PayPalSandboxClient:
    """Small standard-library PayPal Sandbox REST client; credentials from env vars."""

    def __init__(self, client_id: str | None = None, client_secret: str | None = None) -> None:
        if not SANDBOX_API.startswith("https://"):
            raise PayPalWorkflowError("PayPal sandbox API host must use HTTPS.")
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self._token: str | None = None

    def _send(self, url: str, method: str, body: bytes | None, headers: dict[str, str]) -> dict[str, Any]:
        if not url.startswith(f"{SANDBOX_API}/"):
            raise PayPalWorkflowError("Refusing non-sandbox or non-HTTPS PayPal endpoint.")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never leak credentials, bearer tokens, or raw PayPal response bodies.
            raise PayPalWorkflowError(
                f"PayPal sandbox returned HTTP {exc.code}; verify the sandbox app and request."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PayPalWorkflowError(
                "PayPal sandbox request failed; check connectivity and sandbox configuration."
            ) from exc
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
        encoded = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
        response = self._send(
            f"{SANDBOX_API}/v1/oauth2/token",
            "POST",
            b"grant_type=client_credentials",
            {"Authorization": f"Basic {encoded}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise PayPalWorkflowError("PayPal sandbox authentication failed; verify sandbox credentials.")
        self._token = token
        return token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        body: bytes | None = None
        if method.upper() != "GET":
            body = json.dumps(payload or {}).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["PayPal-Request-Id"] = request_id or str(uuid.uuid4())
        return self._send(f"{SANDBOX_API}{path}", method.upper(), body, headers)

    def create_order(
        self,
        results_dir: Path,
        amount: str,
        currency: str,
        description: str,
        *,
        human_approved: bool = False,
        return_url: str | None = None,
        cancel_url: str | None = None,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create sandbox order only after evidence, approval proof, and explicit confirmation."""
        results_dir = results_dir.resolve()
        gate = verify_o3ai_gate(results_dir)
        if human_approved is not True:
            raise PayPalWorkflowError("Payment not sent: explicitly confirm the O3AI-approved sandbox order.")

        clean_amount = _validate_amount(amount, currency)
        clean_currency = currency.strip()
        clean_description = _validate_description(description)
        safe_return = _validate_redirect_url(return_url)
        safe_cancel = _validate_redirect_url(cancel_url)

        capture_path = assert_safe_path(results_dir / "paypal_capture.json", results_dir)
        if capture_path.is_file():
            try:
                existing_cap = json.loads(capture_path.read_text(encoding="utf-8"))
                cap_ev = existing_cap.get("evidence", {}) if isinstance(existing_cap, dict) else {}
                if (
                    isinstance(existing_cap, dict)
                    and existing_cap.get("status") == "PASS"
                    and cap_ev.get("capture_status") == "COMPLETED"
                    and cap_ev.get("environment") == "sandbox"
                ):
                    raise PayPalWorkflowError(
                        "Duplicate payment blocked: a completed sandbox capture already exists for this session."
                    )
            except (OSError, json.JSONDecodeError):
                pass

        order_path = assert_safe_path(results_dir / "paypal_order.json", results_dir)
        if order_path.is_file():
            try:
                existing_order = json.loads(order_path.read_text(encoding="utf-8"))
                ord_ev = existing_order.get("evidence", {}) if isinstance(existing_order, dict) else {}
                if (
                    isinstance(existing_order, dict)
                    and existing_order.get("status") == "PASS"
                    and ord_ev.get("environment") == "sandbox"
                    and ord_ev.get("order_status") in {"CREATED", "PAYER_ACTION_REQUIRED", "APPROVED"}
                    and ord_ev.get("amount") == clean_amount
                    and ord_ev.get("currency") == clean_currency
                    and ord_ev.get("description") == clean_description
                    and isinstance(ord_ev.get("order_id"), str)
                    and _ORDER_ID_RE.fullmatch(ord_ev["order_id"])
                ):
                    return {**ord_ev, "idempotent_reuse": True}
            except (OSError, json.JSONDecodeError):
                pass

        app_context: dict[str, Any] = {"user_action": "PAY_NOW"}
        if safe_return:
            app_context["return_url"] = safe_return
        if safe_cancel:
            app_context["cancel_url"] = safe_cancel

        req_id = idempotency_key or str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"o3ai-order:{session_id or 'default'}:{clean_amount}:{clean_currency}:{clean_description}",
            )
        )
        response = self._request(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "amount": {"currency_code": clean_currency, "value": clean_amount},
                        "description": clean_description,
                    }
                ],
                "application_context": app_context,
            },
            request_id=req_id,
        )
        order_id = response.get("id")
        if not isinstance(order_id, str) or not _ORDER_ID_RE.fullmatch(order_id):
            raise PayPalWorkflowError("PayPal sandbox did not return a valid order ID.")
        links = response.get("links", [])
        if not isinstance(links, list):
            raise PayPalWorkflowError("PayPal sandbox returned invalid links data.")
        raw_approval_url = next(
            (
                link.get("href")
                for link in links
                if isinstance(link, dict) and link.get("rel") in {"approve", "payer-action"}
            ),
            None,
        )
        approval_url = _validate_sandbox_approval_url(raw_approval_url)
        order_status = str(response.get("status", "CREATED")).upper()

        evidence_payload: dict[str, Any] = {
            **gate,
            "order_id": order_id,
            "order_status": order_status,
            "buyer_approved": order_status == "APPROVED",
            "amount": clean_amount,
            "currency": clean_currency,
            "description": clean_description,
            "approval_url": approval_url,
            "environment": "sandbox",
        }
        if session_id:
            evidence_payload["session_id"] = session_id

        evidence = {
            "stage": "paypal_order",
            "status": "PASS",
            "summary": "PayPal sandbox order created after O3AI evidence and operator approval.",
            "evidence": evidence_payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_evidence(results_dir, "paypal_order.json", evidence)
        return evidence_payload

    def get_order(self, results_dir: Path, order_id: str) -> dict[str, Any]:
        """Retrieve order from PayPal Sandbox and verify/record buyer approval state."""
        results_dir = results_dir.resolve()
        gate = verify_o3ai_gate(results_dir)
        if not isinstance(order_id, str) or not _ORDER_ID_RE.fullmatch(order_id):
            raise PayPalWorkflowError("Invalid PayPal order ID.")

        order_path = assert_safe_path(results_dir / "paypal_order.json", results_dir)
        order_evidence = _read_json_dict(
            order_path, "No recorded O3AI sandbox order found for verification."
        )
        recorded = order_evidence.get("evidence")
        if (
            order_evidence.get("status") != "PASS"
            or not isinstance(recorded, dict)
            or recorded.get("order_id") != order_id
            or recorded.get("environment") != "sandbox"
        ):
            raise PayPalWorkflowError("Order verification blocked: order does not match recorded O3AI sandbox order.")

        response = self._request("GET", f"/v2/checkout/orders/{order_id}")
        resp_id = response.get("id")
        resp_status = response.get("status")
        if resp_id != order_id or not isinstance(resp_status, str) or not resp_status.strip():
            raise PayPalWorkflowError("PayPal sandbox returned an invalid order verification response.")

        normalized_status = resp_status.strip().upper()
        buyer_approved = normalized_status == "APPROVED"
        now_iso = datetime.now(timezone.utc).isoformat()
        updated_evidence = {
            **recorded,
            **gate,
            "order_id": order_id,
            "order_status": normalized_status,
            "buyer_approved": buyer_approved,
            "buyer_verified_at": now_iso,
            "environment": "sandbox",
        }
        updated_doc = {
            "stage": "paypal_order",
            "status": "PASS",
            "summary": (
                "Buyer approved order on PayPal sandbox; ready for explicit operator capture."
                if buyer_approved
                else f"PayPal sandbox order status is {normalized_status}; buyer approval not completed."
            ),
            "evidence": updated_evidence,
            "timestamp": now_iso,
        }
        _write_evidence(results_dir, "paypal_order.json", updated_doc)
        return updated_evidence

    def verify_buyer_approval(self, results_dir: Path, order_id: str) -> dict[str, Any]:
        """Alias for get_order to retrieve and record buyer approval status from PayPal."""
        return self.get_order(results_dir, order_id)

    def capture_order(
        self,
        results_dir: Path,
        order_id: str,
        *,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        """Capture a buyer-approved sandbox order, rechecking O3AI and explicit confirmation."""
        results_dir = results_dir.resolve()
        gate = verify_o3ai_gate(results_dir)
        if human_approved is not True:
            raise PayPalWorkflowError("Capture blocked: explicitly confirm this sandbox capture.")
        if not isinstance(order_id, str) or not _ORDER_ID_RE.fullmatch(order_id):
            raise PayPalWorkflowError("Invalid PayPal order ID.")

        order_path = assert_safe_path(results_dir / "paypal_order.json", results_dir)
        order_evidence = _read_json_dict(
            order_path, "Create an O3AI-gated sandbox order before capturing it."
        )
        recorded = order_evidence.get("evidence")
        if (
            order_evidence.get("status") != "PASS"
            or not isinstance(recorded, dict)
            or recorded.get("order_id") != order_id
            or recorded.get("environment") != "sandbox"
        ):
            raise PayPalWorkflowError("Capture blocked: order is not a recorded O3AI sandbox order.")

        capture_path = assert_safe_path(results_dir / "paypal_capture.json", results_dir)
        if capture_path.is_file():
            try:
                existing_cap = json.loads(capture_path.read_text(encoding="utf-8"))
                cap_ev = existing_cap.get("evidence", {}) if isinstance(existing_cap, dict) else {}
                if (
                    isinstance(existing_cap, dict)
                    and existing_cap.get("status") == "PASS"
                    and cap_ev.get("order_id") == order_id
                    and cap_ev.get("capture_status") == "COMPLETED"
                    and cap_ev.get("environment") == "sandbox"
                ):
                    return {**cap_ev, "idempotent_reuse": True}
            except (OSError, json.JSONDecodeError):
                pass

        if not (recorded.get("buyer_approved") is True and recorded.get("order_status") == "APPROVED"):
            verified = self.get_order(results_dir, order_id)
            if not (verified.get("buyer_approved") is True and verified.get("order_status") == "APPROVED"):
                raise PayPalWorkflowError(
                    "Capture blocked: buyer has not approved this order on PayPal Sandbox."
                )

        try:
            response = self._request(
                "POST",
                f"/v2/checkout/orders/{order_id}/capture",
                {},
                request_id=f"o3ai-capture-{order_id}",
            )
        except PayPalWorkflowError as exc:
            _write_evidence(
                results_dir,
                "paypal_capture.json",
                {
                    "stage": "paypal_capture",
                    "status": "FAIL",
                    "summary": str(exc),
                    "evidence": {
                        "order_id": order_id,
                        "capture_status": "FAILED",
                        "environment": "sandbox",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise

        purchase_units = response.get("purchase_units", [])
        captures = [
            capture
            for unit in (purchase_units if isinstance(purchase_units, list) else [])
            if isinstance(unit, dict)
            for capture in (
                unit.get("payments", {}).get("captures", [])
                if isinstance(unit.get("payments"), dict)
                else []
            )
            if isinstance(capture, dict)
        ]
        capture = captures[0] if captures else None
        capture_id = capture.get("id") if isinstance(capture, dict) else None
        if (
            response.get("status") != "COMPLETED"
            or capture is None
            or capture.get("status") != "COMPLETED"
            or not isinstance(capture_id, str)
            or not capture_id.strip()
        ):
            _write_evidence(
                results_dir,
                "paypal_capture.json",
                {
                    "stage": "paypal_capture",
                    "status": "FAIL",
                    "summary": "Capture did not complete on PayPal sandbox.",
                    "evidence": {
                        "order_id": order_id,
                        "capture_status": str(response.get("status", "FAILED")),
                        "environment": "sandbox",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise PayPalWorkflowError("Capture is not completed; no payment is reported as successful.")

        capture_evidence: dict[str, Any] = {
            **gate,
            "order_id": order_id,
            "capture_id": capture_id,
            "capture_status": "COMPLETED",
            "amount": recorded.get("amount"),
            "currency": recorded.get("currency"),
            "environment": "sandbox",
        }
        if recorded.get("session_id"):
            capture_evidence["session_id"] = recorded["session_id"]

        evidence = {
            "stage": "paypal_capture",
            "status": "PASS",
            "summary": "Buyer-approved PayPal sandbox order captured; O3AI gate re-verified.",
            "evidence": capture_evidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_evidence(results_dir, "paypal_capture.json", evidence)
        return capture_evidence

