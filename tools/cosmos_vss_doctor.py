#!/usr/bin/env python3
"""Standalone `cosmos-vss doctor` entry point, usable without installing the
package (e.g. `python tools/cosmos_vss_doctor.py`).

Prefer `uv run cosmos-vss doctor` from `packages/cosmos-vss/` when the
package is installed; this script exists for a quick check from a repo
checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_SRC = Path(__file__).resolve().parent.parent / "packages" / "cosmos-vss" / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

from cosmos_vss.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["doctor"]))
