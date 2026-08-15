"""Assemble a whole RobotIR into one placed solid, and export it.

The engine builds geometry **per link** (`engine.geometry.registry.build`) and
computes each link's world transform (`engine.kinematics.link_geometry_transform`),
but nothing composes the two — so a designed robot could be evaluated and never
looked at. This module closes that gap.

Placement uses `Location(Plane(...))` built directly from the link's rotation
columns rather than decomposing to Euler angles. Euler decomposition has to match
`engine.kinematics._euler_xyz_to_matrix`'s exact convention (Rz@Ry@Rx, intrinsic
XYZ) and silently produces mirrored or gimbal-flipped parts when it doesn't. The
basis-vector form has no convention to get wrong.

Units: the IR is SI metres, build123d is millimetres. Converted here, once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from build123d import Location, Part, Plane
from engine.geometry.registry import build as build_geometry
from engine.ir import RobotIR
from engine.kinematics import link_frames, link_geometry_transform

_M_TO_MM = 1000.0


@dataclass(frozen=True)
class PlacedLink:
    link_id: str
    part: Part
    mass_kg: float


@dataclass(frozen=True)
class Assembly:
    ir: RobotIR
    links: list[PlacedLink]
    part: Part

    @property
    def total_mass_kg(self) -> float:
        return sum(pl.mass_kg for pl in self.links)


def _place(part: Part, transform: np.ndarray) -> Part:
    """Apply a 4x4 SI-metre world transform to a millimetre part."""
    rot = transform[:3, :3]
    origin = transform[:3, 3] * _M_TO_MM
    plane = Plane(
        origin=tuple(float(v) for v in origin),
        x_dir=tuple(float(v) for v in rot[:, 0]),
        z_dir=tuple(float(v) for v in rot[:, 2]),
    )
    return Location(plane) * part


def assemble(ir: RobotIR) -> Assembly:
    """Build every link and place it at its home-pose world transform."""
    placed: list[PlacedLink] = []
    total: Part | None = None
    frames = link_frames(ir)

    for link in ir.links:
        result = build_geometry(link.geometry)
        world = _place(result.part, link_geometry_transform(ir, link.id, frames))
        placed.append(
            PlacedLink(link_id=link.id, part=world, mass_kg=result.mass_properties.mass)
        )
        total = world if total is None else total + world

    if total is None:
        raise ValueError(f"RobotIR {ir.name!r} has no links to assemble")

    return Assembly(ir=ir, links=placed, part=total)


def export(assembly: Assembly, out_dir: Path, stem: str | None = None) -> dict[str, str]:
    """Write the assembled robot as STEP + GLB + STL, plus per-link STEPs."""
    from cad_api.geometry import export_artifacts

    stem = stem or assembly.ir.name
    out_dir = Path(out_dir)
    written = export_artifacts(assembly.part, out_dir, stem)

    parts_dir = out_dir / f"{stem}-links"
    parts_dir.mkdir(parents=True, exist_ok=True)
    for pl in assembly.links:
        sub = export_artifacts(pl.part, parts_dir, pl.link_id)
        if "step" in sub:
            written[f"link:{pl.link_id}"] = sub["step"]

    return written


def describe(assembly: Assembly) -> str:
    bb = assembly.part.bounding_box()
    lines = [
        f"assembly: {assembly.ir.name}",
        f"  links       {len(assembly.links)}",
        f"  joints      {len(assembly.ir.joints)}",
        f"  bbox        {bb.max.X - bb.min.X:.1f} x {bb.max.Y - bb.min.Y:.1f} x {bb.max.Z - bb.min.Z:.1f} mm",
        f"  total mass  {assembly.total_mass_kg * 1000:.1f} g",
    ]
    for pl in sorted(assembly.links, key=lambda p: -p.mass_kg):
        lines.append(f"    {pl.link_id:<16} {pl.mass_kg * 1000:8.1f} g")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m cad_api.assembly")
    parser.add_argument("ir_path", help="path to a RobotIR JSON file")
    parser.add_argument("--out", default="./out", help="output directory")
    parser.add_argument("--stem", default=None, help="basename for the exported files")
    args = parser.parse_args(argv)

    with open(args.ir_path, encoding="utf-8") as f:
        ir = RobotIR.model_validate(json.load(f))

    asm = assemble(ir)
    print(describe(asm))
    written = export(asm, Path(args.out), args.stem)
    print("\nwrote:")
    for k, v in written.items():
        print(f"  {k:<20} {v}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
