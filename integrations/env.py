"""Lightweight standard-library .env loader for local integrations."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(workspace: Path | None = None) -> int:
    """Populate os.environ from a local .env file without overriding set variables.

    Returns the number of environment variables loaded.
    """
    root = workspace if workspace is not None else Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.is_file():
        return 0

    loaded = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ or not os.environ[key].strip():
            os.environ[key] = value
            loaded += 1
    return loaded
