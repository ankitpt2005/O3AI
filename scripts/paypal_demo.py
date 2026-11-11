"""Explicit CLI for O3AI-gated PayPal sandbox order and capture."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from o3ai.paypal import PayPalSandboxClient, PayPalWorkflowError


def confirm(prompt: str) -> bool:
    """Require typed confirmation; automation cannot silently approve payments."""
    try:
        return getpass.getpass(prompt).strip() == "APPROVE"
    except (EOFError, KeyboardInterrupt):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="O3AI verified PayPal sandbox workflow")
    sub = parser.add_subparsers(dest="action", required=True)
    order = sub.add_parser("create-order", help="Create a sandbox order after O3AI gates pass")
    order.add_argument("--amount", required=True, help="Positive amount, e.g. 25.00")
    order.add_argument("--currency", default="USD", help="Three-letter currency code (default USD)")
    order.add_argument("--description", required=True)
    order.add_argument("--results-dir", type=Path, default=Path("results"))
    capture = sub.add_parser("capture-order", help="Capture buyer-approved sandbox order")
    capture.add_argument("--order-id", required=True)
    capture.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    client = PayPalSandboxClient()
    try:
        if args.action == "create-order":
            if not confirm("O3AI gates passed. Type APPROVE to create a PayPal SANDBOX order: "):
                raise PayPalWorkflowError("Sandbox order cancelled; explicit approval was not entered.")
            result = client.create_order(args.results_dir, args.amount, args.currency, args.description,
                                         human_approved=True)
            print("Sandbox order created. Approve it with a sandbox Personal (buyer) account.")
            print(f"Order ID: {result['order_id']}")
            if result.get("approval_url"):
                print(f"Approval URL: {result['approval_url']}")
            print(f"Evidence: {args.results_dir / 'paypal_order.json'}")
        else:
            if not confirm("Confirm this buyer-approved PayPal SANDBOX capture; type APPROVE: "):
                raise PayPalWorkflowError("Capture cancelled; explicit approval was not entered.")
            result = client.capture_order(args.results_dir, args.order_id, human_approved=True)
            print("Sandbox capture completed.")
            print(f"Capture ID: {result['capture_id']}")
            print(f"Evidence: {args.results_dir / 'paypal_capture.json'}")
        return 0
    except PayPalWorkflowError as exc:
        print(f"Blocked/failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
