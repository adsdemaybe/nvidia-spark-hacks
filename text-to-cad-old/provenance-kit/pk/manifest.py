"""Signed manifest creation and verification.

A manifest is a JSON document binding a content root hash (plus per-file
hashes) to a signer's public key at a claimed time. The signature covers
the canonical JSON encoding of the payload, so any change to content,
metadata, or claimed time invalidates it.

The signature makes tampering with THIS copy evident. It does not prove
authorship priority — anyone can strip a manifest and sign their own.
Priority comes from anchoring the manifest in a public timestamp log
(see anchor.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .keys import public_key_from_hex, public_key_hex
from .merkle import hash_target

MANIFEST_VERSION = 1


def _canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def create_manifest(
    target: Path,
    private_key: Ed25519PrivateKey,
    author: str | None = None,
    comment: str | None = None,
) -> dict:
    root_hash, files = hash_target(target)
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "target_name": target.name,
        "target_kind": "directory" if target.is_dir() else "file",
        "hash_algorithm": "sha256",
        "root_hash": root_hash,
        "files": files,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "signer_public_key": public_key_hex(private_key.public_key()),
        "signature_algorithm": "ed25519",
    }
    if author:
        payload["author"] = author
    if comment:
        payload["comment"] = comment

    signature = private_key.sign(_canonical_json(payload))
    return {"payload": payload, "signature": signature.hex()}


def verify_manifest(
    manifest: dict,
    target: Path,
    expected_public_key_hex: str | None = None,
) -> list[str]:
    """Verify manifest against target content. Returns list of problems;
    empty list means everything checked out."""
    problems: list[str] = []
    payload = manifest.get("payload")
    signature_hex = manifest.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature_hex, str):
        return ["manifest is malformed: missing payload or signature"]

    signer_hex = payload.get("signer_public_key", "")
    if expected_public_key_hex and signer_hex != expected_public_key_hex:
        problems.append(
            "signer public key does not match the expected key "
            f"(manifest: {signer_hex[:16]}…, expected: {expected_public_key_hex[:16]}…)"
        )

    # 1. Signature over canonical payload.
    try:
        public_key = public_key_from_hex(signer_hex)
        public_key.verify(bytes.fromhex(signature_hex), _canonical_json(payload))
    except (InvalidSignature, ValueError):
        problems.append("signature is INVALID — manifest was altered or forged")
        # Content checks below still run: they localize what changed.

    # 2. Content hashes.
    if not target.exists():
        problems.append(f"target does not exist: {target}")
        return problems

    root_hash, files = hash_target(target)
    if root_hash != payload.get("root_hash"):
        problems.append("root hash MISMATCH — content differs from signed content")
        recorded: dict = payload.get("files", {})
        for path in sorted(set(recorded) | set(files)):
            if path not in recorded:
                problems.append(f"  added since signing: {path}")
            elif path not in files:
                problems.append(f"  missing since signing: {path}")
            elif recorded[path] != files[path]:
                problems.append(f"  modified since signing: {path}")
    return problems


def manifest_path_for(target: Path) -> Path:
    if target.is_dir():
        return target / ".pk" / "manifest.json"
    return target.with_name(target.name + ".pk.json")


def write_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def read_manifest(path: Path) -> dict:
    return json.loads(path.read_text())
