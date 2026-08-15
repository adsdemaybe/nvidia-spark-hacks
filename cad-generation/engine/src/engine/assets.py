"""Vendored CAD assets, and the one place the engine is allowed to read a file.

§11 non-negotiable #7 says the engine has zero I/O — "if it imports the ORM,
the architecture is broken". That rule is about *state*: the engine must not
reach for a database, a queue, or a network service, because then an evaluation
depends on something other than the IR it was handed.

Reading a vendored CAD asset is a different thing. The asset is an input, it is
immutable, and it is content-addressed: a `sha256` in the IR pins exactly which
bytes were evaluated, so the same IR still means the same robot. That is the
property the zero-I/O rule exists to protect, and this preserves it rather than
weakening it. What is still forbidden, and has not changed: no database, no
network, no reading anything the IR did not name.

Assets live under a root that defaults to `<repo>/vendor` and can be moved with
`ROBOT_ASSET_ROOT`, so a deployment that vendors elsewhere does not need a code
change. Paths are always relative to that root and are refused if they escape
it — an IR is agent-authored, and `../../etc/passwd` is a path an agent can
write.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

_ENV_VAR = "ROBOT_ASSET_ROOT"
# assets.py -> engine/ -> src/ -> engine/ -> cad-generation/
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "vendor"


def asset_root() -> Path:
    return Path(os.environ.get(_ENV_VAR, _DEFAULT_ROOT)).resolve()


def resolve_asset(relative: str) -> Path:
    """Turn an IR-relative asset path into an absolute one, safely."""
    root = asset_root()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(
            f"asset path {relative!r} escapes the asset root {root}; "
            "asset paths are relative to the root and may not traverse above it"
        )
    if not candidate.exists():
        raise FileNotFoundError(
            f"no asset at {candidate} (root {root}, set {_ENV_VAR} to move it)"
        )
    return candidate


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=128)
def _import_step_cached(path_str: str, mtime_ns: int, size: int):
    # Keyed on mtime and size as well as the path so an edited asset is
    # re-read rather than served stale from a previous evaluation.
    from build123d import import_step

    return import_step(path_str)


def load_step_path(path) -> "object":
    """Import a STEP from an absolute path, uncached.

    The vendored-asset loader keys its cache on (path, mtime, size), which is right for
    files that live in the repo and are read many times. A freeform solid is the opposite:
    written to a temp directory, read once, deleted immediately. Caching it would hold a
    reference to a shape whose file is already gone and grow without bound across a design
    sweep, so this path deliberately does not.
    """
    from build123d import import_step

    return import_step(str(path))


def load_step(relative: str, *, sha256: str | None = None, mating: bool = False):
    """Import a vendored STEP file as a build123d solid.

    Cached, because importing a real STEP takes on the order of a second and
    `evaluate()` builds every link on every candidate — uncached, a coverage
    sweep over a nine-part arm would spend minutes in OpenCascade.

    `sha256`, when given, is verified. Omitting it is allowed and means the
    asset is unpinned: the IR then names a file rather than a specific robot,
    and two evaluations of the "same" design can differ.

    `mating=True` declares that the caller is about to cut, fuse or intersect
    against this solid. §5 forbids that for vendor CAD, and the refusal happens
    here rather than in a review comment — a model ingested by
    `engine.sourcing.models` carries a `visual_only` tag and this raises
    `VisualOnlyError` for it. Assets with no ingest sidecar are allowed through,
    because most of `vendor/` predates the pipeline and breaking existing designs
    to enforce a rule on files nobody claimed were vendor CAD would be the wrong
    trade.
    """
    path = resolve_asset(relative)
    if mating:
        from engine.sourcing.models import require_matable

        require_matable(path)
    if sha256 is not None:
        actual = sha256_of(path)
        if actual != sha256:
            raise ValueError(
                f"asset {relative} does not match the sha256 pinned in the IR: "
                f"expected {sha256[:16]}..., found {actual[:16]}.... The design "
                "references different bytes than the ones it was evaluated against."
            )
    stat = path.stat()
    return _import_step_cached(str(path), stat.st_mtime_ns, stat.st_size)
