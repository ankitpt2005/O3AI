"""Guard the O3AI core boundary: `o3ai/` stays offline and never imports integrations."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "o3ai"

# Network-facing code belongs in integrations/, never in the core package.
FORBIDDEN_MODULES = (
    "integrations",
    "urllib.request",
    "http.client",
    "http.server",
    "socket",
    "socketserver",
    "ssl",
    "smtplib",
    "ftplib",
    "xmlrpc",
)


def _imported_modules(tree: ast.AST) -> set[str]:
    """Return every absolute module path imported anywhere in the syntax tree."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _is_forbidden(module: str) -> bool:
    return any(module == banned or module.startswith(f"{banned}.") for banned in FORBIDDEN_MODULES)


class CoreBoundaryTests(unittest.TestCase):
    def test_core_has_no_network_or_integration_imports(self) -> None:
        core_files = sorted(CORE_DIR.rglob("*.py"))
        self.assertTrue(core_files, "o3ai/ core package not found")
        violations: list[str] = []
        for path in core_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module in sorted(_imported_modules(tree)):
                if _is_forbidden(module):
                    violations.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
        self.assertEqual(violations, [], "Core must stay offline: " + "; ".join(violations))

    def test_web_app_lives_outside_core(self) -> None:
        self.assertFalse((CORE_DIR / "web_app.py").exists())
        self.assertTrue((REPO_ROOT / "integrations" / "web_app.py").is_file())


if __name__ == "__main__":
    unittest.main()
