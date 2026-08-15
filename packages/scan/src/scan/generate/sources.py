"""Substitute-asset producers behind one protocol.

The user's chosen path is generative 3D per swap (Hunyuan3D/TRELLIS). That is
the top rung and it is GPU + network + model-weights territory -- Spark. The
ladder below it keeps the cousin axis alive everywhere else:

  hunyuan3d   -- text -> mesh, Spark, blind-authored subprocess adapter
  fixture     -- pre-generated GLBs committed under fixtures/assets/<category>/
  procedural  -- parametric primitives, real geometry, always available

Every generation is disk-cached by sha256(descriptor+source+seed): diffusion is
not reproducible even at fixed seed, so "same seed -> same 8 scenes" is only
true because the cache makes the first draw permanent.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Protocol

import numpy as np
import trimesh


class AssetSource(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...
    def produce(self, category: str, descriptor: str, seed: int, cache_dir: Path) -> trimesh.Trimesh: ...


def cache_key(source: str, category: str, descriptor: str, seed: int) -> str:
    return hashlib.sha256(f"{source}|{category}|{descriptor}|{seed}".encode()).hexdigest()


class ProceduralSource:
    """Parametric primitives with real variation: the honest bottom rung.

    Not photoreal and not pretending to be -- provenance says PROCEDURAL and the
    scene records it. What it preserves is the thing that matters for the cousin
    hypothesis: affordance-compatible geometry at plausible scale, different
    every draw.
    """

    name = "procedural"

    _RECIPES = {
        "mug": "_mug", "cup": "_mug", "bottle": "_bottle", "can": "_can",
        "box": "_box", "book": "_book", "keyboard": "_slab", "laptop": "_slab",
        "mouse": "_blob", "monitor": "_monitor", "lamp": "_lamp",
        "plant": "_blob", "table": "_table", "chair": "_chair",
    }

    def available(self) -> tuple[bool, str]:
        return True, "always"

    def produce(self, category: str, descriptor: str, seed: int, cache_dir: Path) -> trimesh.Trimesh:
        rng = np.random.Generator(np.random.PCG64(seed))
        fn = getattr(self, self._RECIPES.get(category, "_box"))
        mesh = fn(rng)
        mesh.metadata["descriptor"] = descriptor or f"procedural {category}"
        return mesh

    # -- recipes (dimensions in metres, plausible household ranges) --------
    def _mug(self, rng) -> trimesh.Trimesh:
        r = rng.uniform(0.035, 0.05)
        h = rng.uniform(0.08, 0.12)
        body = trimesh.creation.cylinder(radius=r, height=h, sections=24)
        body.apply_translation([0, 0, h / 2])
        handle = trimesh.creation.torus(major_radius=r * 0.7, minor_radius=r * 0.16,
                                        major_sections=16, minor_sections=8)
        handle.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        handle.apply_translation([r + r * 0.35, 0, h * 0.55])
        return trimesh.util.concatenate([body, handle])

    def _bottle(self, rng) -> trimesh.Trimesh:
        r = rng.uniform(0.028, 0.04)
        h = rng.uniform(0.18, 0.28)
        body = trimesh.creation.cylinder(radius=r, height=h * 0.75, sections=20)
        body.apply_translation([0, 0, h * 0.375])
        neck = trimesh.creation.cylinder(radius=r * 0.45, height=h * 0.25, sections=16)
        neck.apply_translation([0, 0, h * 0.875])
        return trimesh.util.concatenate([body, neck])

    def _can(self, rng) -> trimesh.Trimesh:
        r = rng.uniform(0.03, 0.035)
        h = rng.uniform(0.10, 0.13)
        m = trimesh.creation.cylinder(radius=r, height=h, sections=24)
        m.apply_translation([0, 0, h / 2])
        return m

    def _box(self, rng) -> trimesh.Trimesh:
        e = rng.uniform([0.08, 0.06, 0.04], [0.25, 0.2, 0.15])
        m = trimesh.creation.box(extents=e)
        m.apply_translation([0, 0, e[2] / 2])
        return m

    def _book(self, rng) -> trimesh.Trimesh:
        e = [rng.uniform(0.15, 0.25), rng.uniform(0.11, 0.18), rng.uniform(0.015, 0.05)]
        m = trimesh.creation.box(extents=e)
        m.apply_translation([0, 0, e[2] / 2])
        return m

    def _slab(self, rng) -> trimesh.Trimesh:
        e = [rng.uniform(0.28, 0.45), rng.uniform(0.12, 0.32), rng.uniform(0.015, 0.03)]
        m = trimesh.creation.box(extents=e)
        m.apply_translation([0, 0, e[2] / 2])
        return m

    def _blob(self, rng) -> trimesh.Trimesh:
        r = rng.uniform(0.03, 0.06)
        m = trimesh.creation.icosphere(subdivisions=2, radius=r)
        m.apply_scale([1.0, rng.uniform(0.6, 0.9), rng.uniform(0.5, 0.8)])
        m.apply_translation([0, 0, float(-m.bounds[0][2])])
        return m

    def _monitor(self, rng) -> trimesh.Trimesh:
        w = rng.uniform(0.5, 0.65)
        panel = trimesh.creation.box(extents=[w, 0.04, w * 9 / 16])
        panel.apply_translation([0, 0, 0.12 + w * 9 / 32])
        base = trimesh.creation.cylinder(radius=0.09, height=0.02, sections=16)
        base.apply_translation([0, 0, 0.01])
        stem = trimesh.creation.box(extents=[0.04, 0.04, 0.12])
        stem.apply_translation([0, 0, 0.06])
        return trimesh.util.concatenate([panel, base, stem])

    def _lamp(self, rng) -> trimesh.Trimesh:
        h = rng.uniform(0.3, 0.45)
        base = trimesh.creation.cylinder(radius=0.08, height=0.02, sections=16)
        base.apply_translation([0, 0, 0.01])
        pole = trimesh.creation.cylinder(radius=0.012, height=h, sections=10)
        pole.apply_translation([0, 0, h / 2])
        shade = trimesh.creation.cone(radius=0.09, height=0.1, sections=16)
        shade.apply_translation([0, 0, h])
        return trimesh.util.concatenate([base, pole, shade])

    def _table(self, rng) -> trimesh.Trimesh:
        w, d = rng.uniform(0.9, 1.4), rng.uniform(0.5, 0.8)
        h = rng.uniform(0.68, 0.76)
        top = trimesh.creation.box(extents=[w, d, 0.03])
        top.apply_translation([0, 0, h - 0.015])
        legs = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                leg = trimesh.creation.box(extents=[0.05, 0.05, h - 0.03])
                leg.apply_translation([sx * (w / 2 - 0.05), sy * (d / 2 - 0.05), (h - 0.03) / 2])
                legs.append(leg)
        return trimesh.util.concatenate([top, *legs])

    def _chair(self, rng) -> trimesh.Trimesh:
        s = rng.uniform(0.38, 0.45)
        h = rng.uniform(0.42, 0.48)
        seat = trimesh.creation.box(extents=[s, s, 0.04])
        seat.apply_translation([0, 0, h])
        back = trimesh.creation.box(extents=[s, 0.03, s])
        back.apply_translation([0, -s / 2 + 0.015, h + s / 2])
        legs = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                leg = trimesh.creation.box(extents=[0.04, 0.04, h])
                leg.apply_translation([sx * (s / 2 - 0.04), sy * (s / 2 - 0.04), h / 2])
                legs.append(leg)
        return trimesh.util.concatenate([seat, back, *legs])


class FixtureSource:
    """Pre-generated meshes committed under fixtures/assets/<category>/*.glb --
    what a Hunyuan3D run on the Spark deposits, replayed deterministically."""

    name = "fixture"

    def __init__(self, root: Path):
        self.root = Path(root)

    def available(self) -> tuple[bool, str]:
        return self.root.exists(), str(self.root)

    def produce(self, category: str, descriptor: str, seed: int, cache_dir: Path) -> trimesh.Trimesh:
        cands = sorted((self.root / category).glob("*.glb")) + \
            sorted((self.root / category).glob("*.obj"))
        if not cands:
            raise FileNotFoundError(f"no fixture meshes for category {category!r} under {self.root}")
        pick = cands[seed % len(cands)]
        return trimesh.load(pick, force="mesh")


class Hunyuan3DSource:
    """text -> 3D via a local Hunyuan3D checkout. Spark-only, blind-authored:
    the adapter shells out and trusts only the file that appears on disk."""

    name = "hunyuan3d"

    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("HUNYUAN3D_ROOT", "/opt/hunyuan3d"))

    def available(self) -> tuple[bool, str]:
        ok = (self.root / "generate.py").exists() or (self.root / "demo.py").exists()
        return ok, str(self.root)

    def produce(self, category: str, descriptor: str, seed: int, cache_dir: Path) -> trimesh.Trimesh:
        key = cache_key(self.name, category, descriptor, seed)
        cached = cache_dir / f"{key}.glb"
        if cached.exists():
            return trimesh.load(cached, force="mesh")

        out = cache_dir / f"{key}_raw.glb"
        script = self.root / ("generate.py" if (self.root / "generate.py").exists() else "demo.py")
        proc = subprocess.run(
            ["python", str(script), "--text", descriptor or category,
             "--seed", str(seed), "--output", str(out)],
            cwd=self.root, capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"hunyuan3d generation failed (rc={proc.returncode}); "
                f"stderr tail: {proc.stderr[-400:]}"
            )
        mesh = trimesh.load(out, force="mesh")
        mesh.export(cached)  # cache the CANONICAL result; the raw file may be huge
        return mesh


def select_source(name: str, fixture_root: Path | None) -> AssetSource:
    forced = os.environ.get("R2S_FORCE_BACKEND", "")
    if forced.startswith("assets:"):
        name = forced.split(":", 1)[1]
    if name == "procedural":
        return ProceduralSource()
    if name == "fixture":
        if fixture_root is None:
            raise ValueError("fixture source needs a root dir")
        return FixtureSource(fixture_root)
    if name == "hunyuan3d":
        return Hunyuan3DSource()
    if name == "trellis":
        from .trellis_usd import TrellisSource

        return TrellisSource()
    raise ValueError(f"unknown asset source {name!r} (trellis|hunyuan3d|fixture|procedural)")
