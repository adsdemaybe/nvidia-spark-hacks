"""Anchor a manifest in a public append-only timestamp log.

Uses the OpenTimestamps client (`ots`) when installed. The .ots receipt
proves the manifest — and therefore the signed content hash — existed
before the Bitcoin block that commits it. This is what defeats the
strip-and-resign attack: a forger can delete your signature from their
copy, but cannot produce an earlier timestamp than yours.

If `ots` is missing, we say so plainly. An unanchored manifest is
tamper-evident but proves nothing about priority.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ots_available() -> bool:
    return shutil.which("ots") is not None


def anchor_manifest(manifest_path: Path) -> tuple[bool, str]:
    """Stamp manifest with OpenTimestamps. Returns (ok, message)."""
    if not ots_available():
        return False, (
            "`ots` not found — manifest is NOT anchored. Priority is unproven "
            "until it is. Install with `pip install opentimestamps-client`, "
            f"then run: ots stamp {manifest_path}"
        )
    receipt = manifest_path.with_suffix(manifest_path.suffix + ".ots")
    if receipt.exists():
        return False, f"receipt already exists: {receipt} (upgrade with `ots upgrade`)"
    result = subprocess.run(
        ["ots", "stamp", str(manifest_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return False, f"ots stamp failed: {result.stderr.strip()}"
    return True, (
        f"anchored: {receipt}\n"
        "Receipt is pending until aggregated into a Bitcoin block (~hours). "
        f"Later, run `ots upgrade {receipt}` to make it independently verifiable."
    )


def verify_anchor(manifest_path: Path) -> tuple[bool, str]:
    """Verify an existing .ots receipt. Returns (ok, message)."""
    receipt = manifest_path.with_suffix(manifest_path.suffix + ".ots")
    if not receipt.exists():
        return False, f"no receipt at {receipt} — manifest was never anchored"
    if not ots_available():
        return False, "`ots` not found — cannot verify receipt"
    result = subprocess.run(
        ["ots", "verify", str(receipt), "-f", str(manifest_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output
