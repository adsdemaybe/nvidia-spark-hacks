"""Splat-training backends behind one protocol.

The fallback ladder as code (deployment plan §3.1):

  three_dgrut  -- NVIDIA reference; NuRec USDZ export. Spark only. Blind-authored.
  gsplat       -- JIT-compiles for any arch; PLY out, no NuRec. Spark only.
  stub         -- reads a prepared fixture PLY. Runs anywhere, exercises every
                  downstream stage. NOT a bypass: the fixture cloud is real
                  geometry and the scale/gravity solvers run on it for real.

Select with RECON_FORCE_BACKEND or config. An untested fallback is not a
fallback, so the force knob exists precisely to run lower rungs on the Spark.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .ply import GaussianCloud, load_ply


@dataclass
class SplatResult:
    cloud: GaussianCloud
    ply_path: Path
    method: str
    iterations: int
    val_psnr: float
    val_ssim: float
    val_split: list[str]
    nurec_usdz: Path | None = None
    checkpoint: Path | None = None


class SplatBackend(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...
    def train(self, colmap_dir: Path, images_dir: Path, out_dir: Path, cfg: dict) -> SplatResult: ...


class ThreeDGRUTBackend:
    """Drives an nv-tlabs/3dgrut checkout mounted at $THREEDGRUT_ROOT.

    Blind-authored against the documented interface; every assumption that
    could drift is verified against the filesystem, not the flag: we try both
    export flag spellings and assert on the file that appears.
    """

    name = "three_dgrut"

    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("THREEDGRUT_ROOT", "/opt/3dgrut"))

    def available(self) -> tuple[bool, str]:
        train = self.root / "train.py"
        if not train.exists():
            return False, f"no train.py under {self.root} (clone nv-tlabs/3dgrut --recursive)"
        return True, str(self.root)

    def train(self, colmap_dir: Path, images_dir: Path, out_dir: Path, cfg: dict) -> SplatResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        config_name = cfg.get("config_name", "apps/colmap_3dgut_mcmc.yaml")
        iters = int(cfg.get("iterations", 15000))
        exp = cfg.get("experiment_name", "r2s")

        # Try the current flag first, then the older spelling. Trust the file
        # on disk, never the flag.
        for export_flag in ("export_usd.enabled=true", "export_usdz.enabled=true"):
            cmd = [
                "python", str(self.root / "train.py"),
                "--config-name", config_name,
                f"path={colmap_dir}",
                f"out_dir={out_dir}",
                f"experiment_name={exp}",
                f"n_iterations={iters}",
                export_flag,
                "export_usd.apply_normalizing_transform=true"
                if "usd." in export_flag else
                "export_usdz.apply_normalizing_transform=true",
                *cfg.get("extra_args", []),
            ]
            proc = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
            (out_dir / "train.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
            if proc.returncode == 0:
                break
        else:
            raise RuntimeError(f"3dgrut train failed twice; see {out_dir/'train.log'}")

        usdz = _newest(out_dir, "*.usdz")
        ply = _newest(out_dir, "*.ply")
        ckpt = _newest(out_dir, "*.pt")
        if ply is None and usdz is None:
            raise RuntimeError(f"3dgrut exited 0 but produced neither PLY nor USDZ in {out_dir}")

        metrics = _read_metrics(out_dir)
        cloud = load_ply(ply) if ply else GaussianCloud(*(  # USDZ-only: defer cloud
            __import__("numpy").zeros((0, k), dtype="float32") for k in (3, 3, 1, 3, 4)
        ))
        return SplatResult(
            cloud=cloud,
            ply_path=ply or out_dir / "missing.ply",
            method="3dgut",
            iterations=iters,
            val_psnr=metrics.get("psnr", 0.0),
            val_ssim=metrics.get("ssim", 0.0),
            val_split=metrics.get("val_split", []),
            nurec_usdz=usdz,
            checkpoint=ckpt,
        )


class StubBackend:
    """Fixture path: a prepared gaussian PLY + ground-truth metadata."""

    name = "stub"

    def __init__(self, fixture_dir: Path):
        self.fixture_dir = Path(fixture_dir)

    def available(self) -> tuple[bool, str]:
        p = self.fixture_dir / "gaussians.ply"
        return (p.exists(), str(p))

    def train(self, colmap_dir: Path, images_dir: Path, out_dir: Path, cfg: dict) -> SplatResult:
        ply = self.fixture_dir / "gaussians.ply"
        cloud = load_ply(ply)
        meta = {}
        mp = self.fixture_dir / "fixture.json"
        if mp.exists():
            meta = json.loads(mp.read_text())
        return SplatResult(
            cloud=cloud,
            ply_path=ply,
            method="stub",
            iterations=0,
            val_psnr=float(meta.get("stub_psnr", 30.0)),
            val_ssim=float(meta.get("stub_ssim", 0.92)),
            val_split=["stub_holdout_0", "stub_holdout_1"],
        )


def _newest(d: Path, pat: str) -> Path | None:
    hits = sorted(d.rglob(pat), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def _read_metrics(out_dir: Path) -> dict:
    for name in ("metrics.json", "results.json"):
        p = _newest(out_dir, name)
        if p:
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
    return {}


def select_backend(name: str, fixture_dir: Path | None) -> SplatBackend:
    forced = os.environ.get("R2S_FORCE_BACKEND", "")
    if forced.startswith("splat:"):
        name = forced.split(":", 1)[1]
    if name == "three_dgrut":
        return ThreeDGRUTBackend()
    if name == "stub":
        if fixture_dir is None:
            raise ValueError("stub backend needs fixture_dir")
        return StubBackend(fixture_dir)
    raise ValueError(f"unknown splat backend {name!r} (three_dgrut|stub; gsplat lands with the Spark)")
