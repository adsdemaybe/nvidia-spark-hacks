"""Enclosure solid modelling, and the `enclosure_shell` geometry generator.

Two jobs, both containment jobs:

1. **Unit containment.** The contract boundary is millimetres; the engine's IR
   is SI metres. Every mm<->m conversion in the CAD API lives in this module,
   mirroring the rule `engine.geometry.registry` sets for itself.

2. **Registry containment.** `enclosure_shell` registers into the *engine's*
   geometry registry rather than inventing a parallel one, so an enclosure is
   an ordinary `Link` and `engine.evaluate()` scores it with the same criteria
   as any other part — including `mount_fits`. The alternative (a CAD-only
   verdict path) would breach §1.1: agents propose, *the* harness disposes,
   and there is only one harness.

Enclosure local frame: the inner cavity's minimum corner is the origin, so the
cavity occupies [0,L] x [0,W] x [0,H]. Walls and floor are at negative
coordinates. Board coordinates are mapped into this frame by `board_offset()`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from build123d import Align, Box, Cylinder, Part, Pos, export_gltf, export_step, export_stl
from engine.catalogue import MaterialSpec, resolve as resolve_catalogue
from engine.geometry.registry import GeometryResult, _mass_properties_from_shape, register
from engine.ir import (
    CatalogueParam,
    GeometrySpec,
    Link,
    Provenance,
    Quantity,
    RobotIR,
)

from cad_api.contracts import BoardReport, EnclosureIntent, EnclosureReport, PortCutout, Standoff

_M_TO_MM = 1000.0
_MM_TO_M = 1e-3


def mm(q_metres: float) -> float:
    return q_metres * _M_TO_MM


def m(q_mm: float) -> float:
    return q_mm * _MM_TO_M


def _q(value_mm: float, note: str) -> Quantity:
    """A derived dimension, in SI, labelled INFERRED.

    It is inferred rather than measured or confirmed: it follows deterministically
    from the board report plus the intent's clearances, so it is neither a datasheet
    fact nor a measurement (§5 provenance ladder).
    """
    return Quantity(
        value=m(value_mm),
        unit="m",
        provenance=Provenance(status="INFERRED", source="cad_api.enclosure", note=note),
    )


# --- the registered generator -------------------------------------------


@register("enclosure_shell")
def _enclosure_shell(params: dict, material: MaterialSpec) -> GeometryResult:
    """A five-sided box shell (open top) sized by its *inner cavity*.

    Params (all Quantity, SI metres): cavity_length, cavity_width,
    cavity_height, wall_thickness.
    """
    from engine.geometry.registry import _require

    cav_l = mm(_require(params, "cavity_length").value)
    cav_w = mm(_require(params, "cavity_width").value)
    cav_h = mm(_require(params, "cavity_height").value)
    wall = mm(_require(params, "wall_thickness").value)

    if wall <= 0:
        raise ValueError(f"wall_thickness must be positive, got {wall}mm")

    part = _shell_solid(cav_l, cav_w, cav_h, wall)
    return GeometryResult(part=part, mass_properties=_mass_properties_from_shape(part, material.density.value))


def _shell_solid(cav_l: float, cav_w: float, cav_h: float, wall: float) -> Part:
    """Outer box minus cavity, in the enclosure local frame (all mm)."""
    outer = Pos(-wall, -wall, -wall) * Box(
        cav_l + 2 * wall, cav_w + 2 * wall, cav_h + wall, align=(Align.MIN, Align.MIN, Align.MIN)
    )
    cavity = Box(cav_l, cav_w, cav_h, align=(Align.MIN, Align.MIN, Align.MIN))
    return outer - cavity


# --- full enclosure assembly --------------------------------------------


@dataclass(frozen=True)
class BuiltEnclosure:
    part: Part
    cavity_l_mm: float
    cavity_w_mm: float
    cavity_h_mm: float
    offset_x_mm: float  # board-frame -> enclosure-frame
    offset_y_mm: float


_BOTTOM_CLEARANCE_MM = 0.5  # air gap under the tallest bottom-side component


def standoff_height_mm(board: BoardReport, intent: EnclosureIntent) -> float:
    """The standoff height this board actually needs, in mm.

    `intent.standoff_height_mm` is a floor, not a verdict. Bottom-side components
    hang into the standoff gap, so a board with a 12mm electrolytic underneath
    cannot seat on 4mm standoffs no matter what the intent asked for. Deriving the
    height here — once — keeps the solid, the reported standoffs and the cutout
    heights from disagreeing with each other.

    The raised value is not hidden: it lands in every `Standoff.height_mm` in the
    enclosure report, so the report still measures what exists rather than
    restating what was requested.
    """
    needed = board.max_component_height_mm("bottom")
    if needed <= 0:
        return intent.standoff_height_mm
    return max(intent.standoff_height_mm, needed + _BOTTOM_CLEARANCE_MM)


def board_offset(board: BoardReport, intent: EnclosureIntent) -> tuple[float, float]:
    """Translation taking board coordinates into the enclosure's cavity frame.

    The board's bounding-box minimum lands at (clearance, clearance) so there is
    an even gap between the board edge and the inner wall on all sides.
    """
    bb = board.outline_mm.bbox()
    return (intent.clearance_mm - bb.min_x_mm, intent.clearance_mm - bb.min_y_mm)


def build_enclosure(board: BoardReport, intent: EnclosureIntent) -> BuiltEnclosure:
    """Deterministic: same board_report + intent always yields the same solid."""
    bb = board.outline_mm.bbox()
    cav_l = bb.length_mm + 2 * intent.clearance_mm
    cav_w = bb.width_mm + 2 * intent.clearance_mm
    standoff_h = standoff_height_mm(board, intent)
    cav_h = (
        standoff_h
        + board.thickness_mm
        + board.max_component_height_mm("top")
        + intent.headroom_mm
    )

    part = _shell_solid(cav_l, cav_w, cav_h, intent.wall_thickness_mm)
    off_x, off_y = board_offset(board, intent)

    # Standoff bosses, one per mounting hole, each with its pilot hole.
    for hole in board.mounting_holes:
        cx, cy = hole.x_mm + off_x, hole.y_mm + off_y
        boss = Pos(cx, cy, 0) * Cylinder(
            radius=intent.standoff_outer_diameter_mm / 2,
            height=standoff_h,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        pilot = Pos(cx, cy, 0) * Cylinder(
            radius=hole.diameter_mm / 2,
            height=standoff_h,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        part = part + boss - pilot

    # Port cutouts through the wall for connectors that asked for one.
    for cutout in port_cutouts_for(board, intent):
        part = part - _cutout_solid(cutout, cav_l, cav_w, intent.wall_thickness_mm, off_x, off_y, board, intent)

    return BuiltEnclosure(
        part=part,
        cavity_l_mm=cav_l,
        cavity_w_mm=cav_w,
        cavity_h_mm=cav_h,
        offset_x_mm=off_x,
        offset_y_mm=off_y,
    )


def port_cutouts_for(board: BoardReport, intent: EnclosureIntent) -> list[PortCutout]:
    """One cutout per connector that declared `needs_cutout`. Pure data."""
    off_x, off_y = board_offset(board, intent)
    out: list[PortCutout] = []
    for conn in board.connector_edges:
        if not conn.needs_cutout:
            continue
        out.append(
            PortCutout(
                ref=conn.ref,
                edge=conn.edge,
                x_mm=conn.x_mm + off_x,
                y_mm=conn.y_mm + off_y,
                width_mm=conn.width_mm,
                height_mm=conn.height_mm,
            )
        )
    return out


def _cutout_solid(
    cutout: PortCutout,
    cav_l: float,
    cav_w: float,
    wall: float,
    off_x: float,
    off_y: float,
    board: BoardReport,
    intent: EnclosureIntent,
) -> Part:
    """A prism punched through the named wall, at the connector's height."""
    z0 = standoff_height_mm(board, intent) + board.thickness_mm
    depth = wall * 3  # comfortably through the wall from either side

    if cutout.edge in ("north", "south"):
        y = cav_w if cutout.edge == "north" else 0.0
        return Pos(cutout.x_mm, y - depth / 2, z0) * Box(
            cutout.width_mm, depth, cutout.height_mm, align=(Align.CENTER, Align.MIN, Align.MIN)
        )
    x = cav_l if cutout.edge == "east" else 0.0
    return Pos(x - depth / 2, cutout.y_mm, z0) * Box(
        depth, cutout.width_mm, cutout.height_mm, align=(Align.MIN, Align.CENTER, Align.MIN)
    )


def standoffs_for(board: BoardReport, intent: EnclosureIntent) -> list[Standoff]:
    off_x, off_y = board_offset(board, intent)
    return [
        Standoff(
            x_mm=h.x_mm + off_x,
            y_mm=h.y_mm + off_y,
            height_mm=standoff_height_mm(board, intent),
            hole_diameter_mm=h.diameter_mm,
            outer_diameter_mm=intent.standoff_outer_diameter_mm,
        )
        for h in board.mounting_holes
    ]


# --- IR bridge ----------------------------------------------------------


def enclosure_ir(built: BuiltEnclosure, intent: EnclosureIntent, name: str = "enclosure") -> RobotIR:
    """Wrap the enclosure as a one-link RobotIR so `engine.evaluate()` can score it."""
    spec = GeometrySpec(
        generator="enclosure_shell",
        params={
            "cavity_length": _q(built.cavity_l_mm, "board bbox length + 2*clearance"),
            "cavity_width": _q(built.cavity_w_mm, "board bbox width + 2*clearance"),
            "cavity_height": _q(built.cavity_h_mm, "standoff + board + tallest top component + headroom"),
            "wall_thickness": _q(intent.wall_thickness_mm, "EnclosureIntent.wall_thickness_mm"),
        },
        material=CatalogueParam(value=intent.material, catalogue="materials"),
    )
    return RobotIR(name=name, root_link="shell", links=[Link(id="shell", geometry=spec)])


# --- artifacts ----------------------------------------------------------


def export_artifacts(part: Part, out_dir: Path, stem: str) -> dict[str, str]:
    """Write STEP + GLB + STL. Returns {format: path}; missing formats are omitted
    rather than faked, so a converter failure is visible, not silent."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    step = out_dir / f"{stem}.step"
    export_step(part, str(step))
    written["step"] = str(step)

    try:
        glb = out_dir / f"{stem}.glb"
        export_gltf(part, str(glb), binary=True)
        written["glb"] = str(glb)
    except Exception:  # noqa: BLE001 - a viewer format is not worth failing the design over
        pass

    try:
        stl = out_dir / f"{stem}.stl"
        export_stl(part, str(stl))
        written["stl"] = str(stl)
    except Exception:  # noqa: BLE001
        pass

    return written


def content_hash(board: BoardReport, intent: EnclosureIntent) -> str:
    """Stable identity for a (board, intent) pair — the artifact cache key (§1.1
    "every artifact is hashed and immutable")."""
    blob = board.model_dump_json() + "|" + intent.model_dump_json()
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
