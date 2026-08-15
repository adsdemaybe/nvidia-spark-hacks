"""Ed25519 key management.

Private key stays in a local file (0600). Public key is shareable hex.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DEFAULT_KEY_DIR = Path.home() / ".provenance-kit"
PRIVATE_KEY_NAME = "pk_ed25519"
PUBLIC_KEY_NAME = "pk_ed25519.pub"


def generate_keypair(key_dir: Path = DEFAULT_KEY_DIR) -> tuple[Path, Path]:
    """Generate and store a keypair. Refuses to overwrite an existing key."""
    key_dir.mkdir(parents=True, exist_ok=True)
    priv_path = key_dir / PRIVATE_KEY_NAME
    pub_path = key_dir / PUBLIC_KEY_NAME
    if priv_path.exists():
        raise FileExistsError(f"refusing to overwrite existing key: {priv_path}")

    private_key = Ed25519PrivateKey.generate()
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(priv_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(priv_bytes)

    pub_hex = public_key_hex(private_key.public_key())
    pub_path.write_text(pub_hex + "\n")
    return priv_path, pub_path


def load_private_key(key_dir: Path = DEFAULT_KEY_DIR) -> Ed25519PrivateKey:
    priv_path = key_dir / PRIVATE_KEY_NAME
    if not priv_path.exists():
        raise FileNotFoundError(
            f"no private key at {priv_path} — run `pk keygen` first"
        )
    key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"{priv_path} is not an Ed25519 private key")
    return key


def public_key_hex(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def public_key_from_hex(hex_str: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_str))
