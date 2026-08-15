#!/usr/bin/env python3
"""Generate the fixture GLBs that scene.json references — closes ASSETS_TODO.md.

    uv run python tools/make_assets.py

`make_fixtures.py` writes scene.json naming table.glb, cube.glb, bin.glb and
robot.glb, but the binaries were never produced, so any client loading the
manifest 404s on every asset. This writes them.

Crude on purpose: correct units and plausible extents, not a reconstruction.
The real assets arrive from F3 as a scan-derived scene (master plan §7);
these exist so the XR client is developable before that lands.

Deterministic — no clock, no RNG, and trimesh's metadata is stripped on export
— so regenerating produces byte-identical files and a fixture diff always means
somebody changed something on purpose.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import trimesh

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ar-xr"

# Meters, Z-up, matching the STRUCT convention the contracts mandate.
TABLE_SIZE = (1.20, 0.80, 0.75)
CUBE_SIZE = 0.06
BIN_SIZE = (0.30, 0.30, 0.25)
BIN_WALL = 0.02
ROBOT_BASE_RADIUS = 0.15
ROBOT_HEIGHT = 0.90


def _write_glb(mesh: trimesh.Trimesh, path: Path) -> None:
    """Export so the bytes depend only on geometry.

    trimesh stamps a generator string into the GLB asset block; leaving it in
    would make regeneration non-deterministic.
    """
    glb = trimesh.exchange.gltf.export_glb(trimesh.Scene(mesh), include_normals=True)
    path.write_bytes(glb)


def table() -> trimesh.Trimesh:
    """Sits on the floor: origin centred in x/y, z=0 underneath."""
    mesh = trimesh.creation.box(extents=TABLE_SIZE)
    mesh.apply_translation((0.0, 0.0, TABLE_SIZE[2] / 2.0))
    return mesh


def cube() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE))


def bin_() -> trimesh.Trimesh:
    """Open-topped box from a floor and four walls.

    Assembled rather than CSG-differenced: a boolean would pull in
    manifold3d/blender as a build dependency and the result would vary with
    whichever engine happened to be installed, which breaks determinism.
    """
    w, d, h = BIN_SIZE
    t = BIN_WALL

    floor = trimesh.creation.box(extents=(w, d, t))
    floor.apply_translation((0.0, 0.0, t / 2.0))
    parts = [floor]

    for dx, dy, extents in (
        ((w - t) / 2.0, 0.0, (t, d, h)),
        (-(w - t) / 2.0, 0.0, (t, d, h)),
        (0.0, (d - t) / 2.0, (w, t, h)),
        (0.0, -(d - t) / 2.0, (w, t, h)),
    ):
        wall = trimesh.creation.box(extents=extents)
        wall.apply_translation((dx, dy, h / 2.0))
        parts.append(wall)

    return trimesh.util.concatenate(parts)


def robot() -> trimesh.Trimesh:
    """Footprint stand-in only.

    The XR client renders an articulated arm driven by TwinState joint values;
    this GLB exists so a manifest consumer that only does static placement has
    something with the right extents. Kinematics live in
    fixtures/robot/test_arm.urdf, which is the single source of truth.
    """
    base = trimesh.creation.cylinder(radius=ROBOT_BASE_RADIUS, height=0.10, sections=24)
    base.apply_translation((0.0, 0.0, 0.05))
    column = trimesh.creation.cylinder(radius=0.05, height=ROBOT_HEIGHT, sections=16)
    column.apply_translation((0.0, 0.0, 0.10 + ROBOT_HEIGHT / 2.0))
    return trimesh.util.concatenate([base, column])


ASSETS = {"table": table, "cube": cube, "bin": bin_, "robot": robot}


def build_assets(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, build in ASSETS.items():
        path = dest / f"{name}.glb"
        _write_glb(build(), path)
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=FIXTURES_DIR)
    args = parser.parse_args()

    for path in build_assets(args.dest):
        print(f"  {path.name}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
