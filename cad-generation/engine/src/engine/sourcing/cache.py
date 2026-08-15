"""Content-addressed cache for everything the sourcing layer fetches (§6.2).

    "Everything fetched is cached in the artifact store keyed by content hash.
    The network is touched at catalogue-build time only; evaluation and
    optimization remain fully offline."

Keyed by the sha256 of the *bytes*, not by the URL. That distinction is the
whole point: a manufacturer who silently replaces the STEP behind a stable URL
has changed the robot, and a URL-keyed cache would hide it. Content addressing
turns that into a new object, a new hash, and a visible change in the IR that
pins it.

The index is a plain JSON file mapping a request key to the hash it resolved to,
with the fetch time and the license alongside. Deliberately not a database: this
is a build-time artifact store on one machine, `sqlite` would need a schema
migration story, and the whole thing has to be inspectable with `cat`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ENV_VAR = "ROBOT_SOURCING_CACHE"
# sourcing/ -> engine/ -> src/ -> engine/ -> cad-generation/
_DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "sourcing-cache"


def cache_root() -> Path:
    return Path(os.environ.get(_ENV_VAR, _DEFAULT_ROOT)).resolve()


@dataclass
class CacheEntry:
    """One cached fetch: what was asked for, what came back, under what licence."""

    key: str  # the request: "digikey:search:NEMA17", "url:https://..."
    sha256: str
    media_type: str = ""
    source_url: str = ""
    license: str = ""
    fetched_at: str = ""  # ISO 8601; supplied by the caller, never generated here
    note: str = ""
    quarantined: bool = False
    quarantine_reason: str = ""
    extra: dict = field(default_factory=dict)


class SourcingCache:
    """Objects by content hash, plus an index from request key to hash.

    Every method that writes takes the bytes and returns the hash, so a caller
    physically cannot store an object without knowing its identity — which is
    what stops "the cached file" and "the file we evaluated against" drifting
    apart.
    """

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else cache_root()
        self.objects = self.root / "objects"
        self.index_path = self.root / "index.json"

    # --- objects ---------------------------------------------------------

    def path_for(self, sha256: str) -> Path:
        # Two-level fanout: a flat directory with ten thousand STEP files in it
        # is slow to list and unpleasant to look at.
        return self.objects / sha256[:2] / sha256

    def has(self, sha256: str) -> bool:
        return self.path_for(sha256).exists()

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self.path_for(digest)
        if target.exists():
            return digest  # content-addressed: identical bytes, nothing to do
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename, so an interrupted fetch cannot leave a truncated
        # object under a hash that claims to describe complete bytes.
        tmp = target.with_suffix(".partial")
        tmp.write_bytes(data)
        tmp.rename(target)
        return digest

    def get(self, sha256: str) -> bytes:
        path = self.path_for(sha256)
        if not path.exists():
            raise KeyError(f"no cached object {sha256[:16]}... under {self.objects}")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            # Corruption, or someone edited a cache object by hand. Either way
            # the bytes are not what the IR pinned, and continuing would evaluate
            # a different robot than the one recorded.
            raise ValueError(
                f"cached object {sha256[:16]}... hashes to {actual[:16]}...; "
                "the cache has been corrupted or edited in place"
            )
        return data

    # --- index -----------------------------------------------------------

    def _load_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, index: dict[str, dict]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".partial")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
        tmp.rename(self.index_path)

    def record(self, entry: CacheEntry) -> None:
        index = self._load_index()
        index[entry.key] = asdict(entry)
        self._save_index(index)

    def lookup(self, key: str) -> CacheEntry | None:
        raw = self._load_index().get(key)
        return CacheEntry(**raw) if raw else None

    def entries(self) -> list[CacheEntry]:
        return [CacheEntry(**v) for v in self._load_index().values()]

    def quarantine(self, key: str, reason: str) -> None:
        """Mark a cached object unusable without deleting it.

        Deleting would lose the evidence. A model that failed the ingest mass
        cross-check is exactly the thing somebody will want to look at, and the
        next fetch would silently re-download the same bad bytes.
        """
        index = self._load_index()
        if key not in index:
            raise KeyError(f"nothing cached under {key!r} to quarantine")
        index[key]["quarantined"] = True
        index[key]["quarantine_reason"] = reason
        self._save_index(index)

    def quarantined(self) -> list[CacheEntry]:
        return [e for e in self.entries() if e.quarantined]
