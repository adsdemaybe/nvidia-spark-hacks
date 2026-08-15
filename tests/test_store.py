"""CASStore + input_digest contracts (r2s.core.store.cas)."""
from __future__ import annotations

import os

import pytest

from r2s.core.artifacts import ArtifactRef
from r2s.core.store.cas import CASStore, input_digest


@pytest.fixture
def store(tmp_path):
    return CASStore(f"file://{tmp_path}/store")


def _ref(sha: str) -> ArtifactRef:
    return ArtifactRef(
        uri=f"cas://sha256/{sha}",
        sha256=sha,
        size_bytes=10,
        media_type="application/json",
    )


class TestPutGet:
    def test_bytes_roundtrip(self, store):
        data = b"the payload bytes \x00\xff"
        ref = store.put_bytes(data, "application/octet-stream")
        assert store.get_bytes(ref) == data
        assert ref.size_bytes == len(data)
        assert ref.uri == f"cas://sha256/{ref.sha256}"
        assert store.exists(ref)

    def test_dedup_same_bytes_one_blob(self, store, tmp_path):
        ref1 = store.put_bytes(b"identical", "text/plain")
        ref2 = store.put_bytes(b"identical", "text/plain")
        assert ref1.sha256 == ref2.sha256
        blobs = [
            p
            for p in (tmp_path / "store" / "cas").rglob("*")
            if p.is_file()
        ]
        assert len(blobs) == 1

    def test_different_bytes_different_digests(self, store):
        assert store.put_bytes(b"a", "t").sha256 != store.put_bytes(b"b", "t").sha256

    def test_missing_ref_does_not_exist(self, store):
        assert not store.exists(_ref("0" * 64))


def _write_tree(root, mtime: int):
    (root / "sub").mkdir(parents=True)
    f1 = root / "sub" / "mesh.obj"
    f1.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    f2 = root / "meta.json"
    f2.write_text('{"k": 1}')
    for f in (f1, f2):
        os.utime(f, (mtime, mtime))


class TestPutDir:
    def test_mtime_invariance(self, store, tmp_path):
        a, b = tmp_path / "tree_a", tmp_path / "tree_b"
        _write_tree(a, mtime=1_000_000_000)
        _write_tree(b, mtime=1_700_000_000)  # same content, mtimes 22 years apart
        assert store.put_dir(a).sha256 == store.put_dir(b).sha256

    def test_content_sensitivity(self, store, tmp_path):
        a, b = tmp_path / "tree_c", tmp_path / "tree_d"
        _write_tree(a, mtime=1_000_000_000)
        _write_tree(b, mtime=1_000_000_000)
        (b / "meta.json").write_text('{"k": 2}')
        assert store.put_dir(a).sha256 != store.put_dir(b).sha256

    def test_dir_roundtrip(self, store, tmp_path):
        src = tmp_path / "tree_e"
        _write_tree(src, mtime=1_000_000_000)
        ref = store.put_dir(src)
        out = store.fetch_dir(ref, tmp_path / "out")
        assert (out / "meta.json").read_text() == '{"k": 1}'
        assert (out / "sub" / "mesh.obj").read_text().startswith("v 0 0 0")


class TestResumeIndex:
    def test_record_then_lookup(self, store):
        ref = store.put_bytes(b"stage output", "application/json")
        digest = "e" * 64
        store.record("recon", digest, ref)
        hit = store.lookup("recon", digest)
        assert hit == ref

    def test_lookup_miss_returns_none(self, store):
        assert store.lookup("recon", "f" * 64) is None

    def test_lookup_scoped_by_stage(self, store):
        ref = store.put_bytes(b"x", "application/json")
        store.record("stage_a", "a" * 64, ref)
        assert store.lookup("stage_b", "a" * 64) is None


class TestInputDigest:
    _KW = dict(
        stage="recon",
        stage_version="1.0.0",
        config={"iters": 100},
        packages={"r2s-core": "0.1.0", "trimesh": "4.4.0"},
        seed=1337,
    )

    def test_input_order_independence(self):
        r1, r2, r3 = _ref("1" * 64), _ref("2" * 64), _ref("3" * 64)
        d_fwd = input_digest(inputs=[r1, r2, r3], **self._KW)
        d_rev = input_digest(inputs=[r3, r1, r2], **self._KW)
        assert d_fwd == d_rev

    def test_seed_sensitivity(self):
        kw = {**self._KW}
        kw.pop("seed")
        r = [_ref("1" * 64)]
        assert input_digest(inputs=r, seed=0, **kw) != input_digest(inputs=r, seed=1, **kw)

    def test_config_sensitivity(self):
        kw = {**self._KW}
        kw.pop("config")
        r = [_ref("1" * 64)]
        d1 = input_digest(inputs=r, config={"iters": 100}, **kw)
        d2 = input_digest(inputs=r, config={"iters": 200}, **kw)
        assert d1 != d2

    def test_input_content_sensitivity(self):
        assert input_digest(inputs=[_ref("1" * 64)], **self._KW) != input_digest(
            inputs=[_ref("2" * 64)], **self._KW
        )

    def test_package_order_independence(self):
        kw = {**self._KW}
        kw.pop("packages")
        r = [_ref("1" * 64)]
        d1 = input_digest(inputs=r, packages={"a": "1", "b": "2"}, **kw)
        d2 = input_digest(inputs=r, packages={"b": "2", "a": "1"}, **kw)
        assert d1 == d2

    def test_deterministic(self):
        r = [_ref("1" * 64)]
        assert input_digest(inputs=r, **self._KW) == input_digest(inputs=r, **self._KW)
