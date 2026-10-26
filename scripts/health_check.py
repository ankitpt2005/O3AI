"""Health monitor check hitting the deployed page and recording results/monitor.json."""

from __future__ import annotations
import http.server
import json
import socketserver
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple


class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Silent request handler for local health check."""

    def log_message(self, format: str, *args: Any) -> None:
        pass


class ReusableTCPServer(socketserver.TCPServer):
    """TCPServer that enables socket reuse to prevent port conflicts."""
    allow_reuse_address = True


def start_local_test_server(public_dir: Path) -> Tuple[socketserver.TCPServer, int]:
    """Start an ephemeral HTTP server serving public_dir."""
    handler = lambda *args, **kwargs: QuietSimpleHTTPRequestHandler(*args, directory=str(public_dir), **kwargs)
    httpd = ReusableTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def test_url_health(url: str) -> Tuple[bool, Dict[str, Any]]:
    """Perform HTTP GET request and verify status 200 and title content."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "O3AI-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            has_title = "<title>O3AI Working Tower Dashboard</title>" in body
            passed = status_code == 200 and has_title

            evidence = {
                "url": url,
                "status_code": status_code,
                "has_valid_title": has_title,
                "content_length": len(body),
                "health": "UP" if passed else "DOWN",
            }
            return passed, evidence
    except Exception as err:
        return False, {
            "url": url,
            "status_code": 0,
            "error": str(err),
            "health": "DOWN",
        }


def run_health_check(workspace_root: Path, target_url: str | None = None) -> Tuple[bool, Dict[str, Any]]:
    """Execute health check against target URL or local public directory."""
    public_dir = workspace_root / "public"
    index_file = public_dir / "index.html"

    if not index_file.exists():
        return False, {"error": "Missing public/index.html to monitor", "health": "DOWN"}

    if target_url:
        return test_url_health(target_url)

    # Boot ephemeral local server to test real HTTP request
    httpd, port = start_local_test_server(public_dir)
    test_url = f"http://127.0.0.1:{port}/index.html"
    try:
        return test_url_health(test_url)
    finally:
        httpd.shutdown()
        httpd.server_close()


def main() -> int:
    """Main CLI entrypoint for monitor stage."""
    workspace = Path.cwd()
    passed, evidence = run_health_check(workspace)

    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "monitor.json"

    payload = {
        "stage": "monitor",
        "status": "PASS" if passed else "FAIL",
        "evidence": evidence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    if passed:
        print(f"[SUCCESS] Health monitor passed: Page is UP at {evidence.get('url')}")
        print(f"  - Status code: {evidence.get('status_code')}")
        print(f"  - Content length: {evidence.get('content_length')} bytes")
        return 0
    else:
        print(f"[FAILURE] Health monitor failed: {evidence}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
