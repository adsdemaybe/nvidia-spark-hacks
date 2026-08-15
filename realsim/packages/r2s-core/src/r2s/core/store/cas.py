"""Content-addressed artifact store, fsspec-backed.

One implementation serves both the laptop and the Spark: `file:///…/.r2s` and
`s3://bucket/prefix` differ only in the root URL. Because blobs are immutable
and content-addressed, syncing a run between machines is
`rsync --ignore-existing` and the two caches are the same cache.

The cache key is over INPUTS only -- `input_digest(stage, version, refs, config,
packages, seed)`. There is deliberately no "does the output still hash the same"
check: 3DGS training, CoACD, and PhysX settling are all nondeterministic to
varying degrees, and asserting output stability would produce permanent false
alarms. Determinism is enforced where it is achievable and matters (placement
sampling, variation planning, task generation), by seeding, and tested directly.
"""
from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from typing import TypeVar

import fsspec
from pydantic import BaseModel

from ..artifacts import ArtifactRef, BaseArtifact, canonical_json, sha256_of

A = TypeVar("A", bound=BaseArtifact)

_TAR = "application/x-tar"


def input_digest(
    *,
    stage: str,
    stage_version: str,
    inputs: list[ArtifactRef],
    config: BaseModel | dict,
    packages: dict[str, str],
    seed: int,
) -> str:
    """The resume key.

    `packages` must contain only the packages the stage actually uses -- declared
    per stage. Hashing the whole environment means bumping `rich` re-runs COLMAP.
    """
    return sha256_of(
        {
            "stage": stage,
            "stage_version": stage_version,
            "inputs": sorted(r.sha256 for r in inputs),
            "config": config.model_dump(mode="json") if isinstance(config, BaseModel) else config,
            "packages": dict(sorted(packages.items())),
            "seed": seed,
        }
    )


class CASStore:
    """Immutable blobs under <root>/cas/sha256/ab/cd/<hex>, index under <root>/index."""

    def __init__(self, root: str):
        self.root = root.rstrip("/")
        self.fs, self._base = fsspec.core.url_to_fs(self.root)
        self.fs.makedirs(f"{self._base}/cas", exist_ok=True)
        self.fs.makedirs(f"{self._base}/index", exist_ok=True)
        self.fs.makedirs(f"{self._base}/runs", exist_ok=True)

    # ---- paths ----------------------------------------------------------
    def _blob_path(self, digest: str) -> str:
        return f"{self._base}/cas/sha256/{digest[:2]}/{digest[2:4]}/{digest}"

    def _index_path(self, stage: str, digest: str) -> str:
        return f"{self._base}/index/{stage}/{digest}.json"

    # ---- put ------------------------------------------------------------
    def put_bytes(self, data: bytes, media_type: str) -> ArtifactRef:
        digest = __import__("hashlib").sha256(data).hexdigest()
        path = self._blob_path(digest)
        if not self.fs.exists(path):  # immutable: never rewrite an existing blob
            self.fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
            with self.fs.open(path, "wb") as f:
                f.write(data)
        return ArtifactRef(
            uri=f"cas://sha256/{digest}",
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
        )

    def put_file(self, path: Path, media_type: str) -> ArtifactRef:
        return self.put_bytes(Path(path).read_bytes(), media_type)

    def put_dir(self, path: Path, media_type: str = _TAR) -> ArtifactRef:
        """Directories are stored as deterministic tars (sorted, no mtimes)."""
        with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
            with tarfile.open(tmp.name, "w") as tar:
                for p in sorted(Path(path).rglob("*")):
                    if p.is_file():
                        info = tar.gettarinfo(str(p), arcname=str(p.relative_to(path)))
                        info.mtime, info.uid, info.gid = 0, 0, 0
                        info.uname = info.gname = ""
                        with p.open("rb") as fh:
                            tar.addfile(info, fh)
            ref = self.put_file(Path(tmp.name), media_type)
        Path(tmp.name).unlink(missing_ok=True)
        return ref

    def put_artifact(self, art: BaseArtifact) -> ArtifactRef:
        return self.put_bytes(canonical_json(art), "application/json")

    # ---- get ------------------------------------------------------------
    def get_bytes(self, ref: ArtifactRef) -> bytes:
        with self.fs.open(self._blob_path(ref.sha256), "rb") as f:
            return f.read()

    def get_artifact(self, ref: ArtifactRef, model: type[A]) -> A:
        return model.model_validate_json(self.get_bytes(ref))

    def fetch(self, ref: ArtifactRef, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(ref))
        return dest

    def fetch_dir(self, ref: ArtifactRef, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
            tmp.write(self.get_bytes(ref))
        with tarfile.open(tmp.name) as tar:
            tar.extractall(dest, filter="data")
        Path(tmp.name).unlink(missing_ok=True)
        return dest

    def exists(self, ref: ArtifactRef) -> bool:
        return self.fs.exists(self._blob_path(ref.sha256))

    # ---- resume index ---------------------------------------------------
    def lookup(self, stage: str, digest: str) -> ArtifactRef | None:
        p = self._index_path(stage, digest)
        if not self.fs.exists(p):
            return None
        with self.fs.open(p, "rb") as f:
            return ArtifactRef.model_validate_json(f.read())

    def record(self, stage: str, digest: str, ref: ArtifactRef) -> None:
        p = self._index_path(stage, digest)
        self.fs.makedirs(p.rsplit("/", 1)[0], exist_ok=True)
        with self.fs.open(p, "wb") as f:
            f.write(canonical_json(ref))
