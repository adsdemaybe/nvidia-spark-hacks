"""Canonical content hashing for files and directory trees.

The tree hash is deterministic: same content in same relative layout
always yields the same root, regardless of platform or walk order.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 1 << 20

# Directories whose contents are never part of the signed tree.
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pk"}


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        yield rel, path


def hash_tree(root: Path) -> tuple[str, dict[str, str]]:
    """Return (root_hash, {relative_path: file_hash}).

    Root hash covers both file contents and their relative paths, so
    renaming a file changes the root even if bytes are identical.
    """
    files: dict[str, str] = {}
    for rel, path in _iter_files(root):
        files[rel.as_posix()] = hash_file(path)

    h = hashlib.sha256()
    for rel_str in sorted(files):
        h.update(rel_str.encode())
        h.update(b"\0")
        h.update(bytes.fromhex(files[rel_str]))
        h.update(b"\0")
    return h.hexdigest(), files


def hash_target(target: Path) -> tuple[str, dict[str, str]]:
    """Hash a file or a directory tree uniformly."""
    if target.is_dir():
        return hash_tree(target)
    file_hash = hash_file(target)
    return file_hash, {target.name: file_hash}
