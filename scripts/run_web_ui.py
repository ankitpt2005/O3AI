"""Run the O3AI dashboard locally for developer and customer views."""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from o3ai.web_app import main

if __name__ == "__main__":
    raise SystemExit(main())
