"""The PCB <-> CAD contract (master-plan.md §6).

This module is the frozen interface between Feat 1 (pcb-ai, TypeScript) and
Feat 2 (cad-generation, Python). Per §10 it is an H+4 contract file: changing
a shape here requires both track owners at the table, and the TypeScript
mirror in `pcb-ai/src/cad/contracts.ts` must change in the same commit.

Units: **millimetres everywhere on this boundary**. The PCB world is natively
mm (tscircuit, Gerbers, KiCad) and the engine's IR is natively SI metres; the
mm<->m conversion is contained in `cad_api.geometry`, exactly as
`engine.geometry.registry` contains its own, and never leaks into either
domain's vocabulary (§2 "topology is data" / registry containment rule).

Nothing in this module computes or decides anything — it only describes shapes.
Verdicts come from `cad_api.fit`, geometry from `cad_api.enclosure`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Edge = Literal["north", "south", "east", "west"]
Side = Literal["top", "bottom"]
Severity = Literal["blocker", "major", "minor"]


# --- geometry primitives ------------------------------------------------


class Point2(BaseModel):
    x_mm: float
    y_mm: float


class BBox2(BaseModel):
    min_x_mm: float
    min_y_mm: float
    max_x_mm: float
    max_y_mm: float

    @property
    def length_mm(self) -> float:
        """Extent along X."""
        return self.max_x_mm - self.min_x_mm

    @property
    def width_mm(self) -> float:
        """Extent along Y."""
        return self.max_y_mm - self.min_y_mm

    @model_validator(mode="after")
    def _non_degenerate(self) -> "BBox2":
        if self.max_x_mm <= self.min_x_mm or self.max_y_mm <= self.min_y_mm:
            raise ValueError(f"degenerate bbox: {self.model_dump()}")
        return self


class Outline(BaseModel):
    """Board outline as a closed polygon in board coordinates (mm).

    A rectangle is the common case but the polygon form is the contract so a
    cut-corner or L-shaped board never needs a schema change.
    """

    points: list[Point2] = Field(min_length=3)

    def bbox(self) -> BBox2:
        xs = [p.x_mm for p in self.points]
        ys = [p.y_mm for p in self.points]
        return BBox2(min_x_mm=min(xs), min_y_mm=min(ys), max_x_mm=max(xs), max_y_mm=max(ys))

    @classmethod
    def rect(cls, length_mm: float, width_mm: float, origin_x_mm: float = 0.0, origin_y_mm: float = 0.0) -> "Outline":
        return cls(
            points=[
                Point2(x_mm=origin_x_mm, y_mm=origin_y_mm),
                Point2(x_mm=origin_x_mm + length_mm, y_mm=origin_y_mm),
                Point2(x_mm=origin_x_mm + length_mm, y_mm=origin_y_mm + width_mm),
                Point2(x_mm=origin_x_mm, y_mm=origin_y_mm + width_mm),
            ]
        )


# --- board_report (pcb -> cad) ------------------------------------------


class MountingHole(BaseModel):
    x_mm: float
    y_mm: float
    diameter_mm: float = Field(gt=0)
    plated: bool = False


class ComponentHeight(BaseModel):
    """One entry of the component heightmap: a placed part's occupied volume."""

    ref: str  # designator, e.g. "U1"
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)  # along X
    depth_mm: float = Field(gt=0)  # along Y
    height_mm: float = Field(ge=0)  # above (or below) the board surface
    side: Side = "top"


class ConnectorEdge(BaseModel):
    """A connector that must reach the outside world through the enclosure."""

    ref: str
    edge: Edge
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    needs_cutout: bool = True


class Keepout(BaseModel):
    """A region the enclosure must not intrude into (antenna, mezzanine, etc.)."""

    name: str
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)
    depth_mm: float = Field(gt=0)
    height_mm: float | None = None  # None = full cavity height
    side: Side = "top"


class ThermalHotspot(BaseModel):
    ref: str
    x_mm: float
    y_mm: float
    power_w: float = Field(ge=0)
    max_temp_c: float | None = None


class BoardMass(BaseModel):
    """Mass and centre of mass of the populated board.

    §7.3 of the robot tech stack (v3): the board's mass and CoM go into the
    robot's mass model as MEASURED-class facts, replacing the ASSUMED "50 g per
    board" placeholder Phase 1 starts with. It has to be computed on the PCB
    side and not the robot side — the BOM and the stackup live there, and a
    robot-side estimate would be a second opinion nobody measured.

    Split into substrate and components because they have different provenance:
    the substrate is area x thickness x a material density, all of which the
    routed artifact knows, while the component figure is only as good as the
    per-footprint mass table behind it. A caller comparing 12 g of substrate
    against 40 g of "components" knows which half to distrust.
    """

    total_g: float = Field(ge=0)
    substrate_g: float = Field(ge=0)
    components_g: float = Field(ge=0)
    com_mm: Point2


class BoardReport(BaseModel):
    """`board_report` in §6 — everything CAD needs to know about a board.

    Produced by pcb-ai from its circuit JSON; consumed by `cad.design_enclosure`
    and `cad.check_fit`.
    """

    design_id: str = ""
    outline_mm: Outline
    thickness_mm: float = Field(default=1.6, gt=0)
    mounting_holes: list[MountingHole] = Field(default_factory=list)
    component_heightmap: list[ComponentHeight] = Field(default_factory=list)
    connector_edges: list[ConnectorEdge] = Field(default_factory=list)
    keepouts: list[Keepout] = Field(default_factory=list)
    thermal_hotspots: list[ThermalHotspot] = Field(default_factory=list)
    # None when the producing side did not compute it. Nullable rather than
    # defaulted to zero: a board of unknown mass and a massless board are very
    # different inputs to a centre-of-mass calculation, and only one of them is
    # possible.
    mass: BoardMass | None = None

    def max_component_height_mm(self, side: Side = "top") -> float:
        heights = [c.height_mm for c in self.component_heightmap if c.side == side]
        return max(heights) if heights else 0.0

    def total_power_w(self) -> float:
        return sum(h.power_w for h in self.thermal_hotspots)


# --- enclosure_report (cad -> pcb) --------------------------------------


class Box3(BaseModel):
    length_mm: float = Field(gt=0)  # X
    width_mm: float = Field(gt=0)  # Y
    height_mm: float = Field(gt=0)  # Z


class Standoff(BaseModel):
    x_mm: float
    y_mm: float
    height_mm: float = Field(gt=0)
    hole_diameter_mm: float = Field(gt=0)
    outer_diameter_mm: float = Field(gt=0)

    @model_validator(mode="after")
    def _boss_wider_than_hole(self) -> "Standoff":
        if self.outer_diameter_mm <= self.hole_diameter_mm:
            raise ValueError(
                f"standoff outer_diameter ({self.outer_diameter_mm}mm) must exceed "
                f"hole_diameter ({self.hole_diameter_mm}mm)"
            )
        return self


class PortCutout(BaseModel):
    ref: str
    edge: Edge
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)


class Artifacts(BaseModel):
    """Paths (or URIs) of generated geometry. Hashes make them citable (§1.1)."""

    step_path: str = ""
    glb_path: str = ""
    stl_path: str = ""
    content_hash: str = ""


class EnclosureReport(BaseModel):
    """`enclosure_report` in §6 — what the enclosure is, measured."""

    cavity_mm: Box3
    standoff_positions: list[Standoff] = Field(default_factory=list)
    port_cutouts: list[PortCutout] = Field(default_factory=list)
    wall_thickness_mm: float = Field(gt=0)
    max_component_height_mm: float = Field(ge=0)
    # --- extensions beyond the §6 minimum ---
    # `board_origin_mm` is load-bearing, not decoration: standoffs and cutouts are
    # in the enclosure's cavity frame while the board report is in board coordinates,
    # so without the origin neither `check_fit` nor `constrain_board` can compare
    # the two. §6 left the frame implicit; making it explicit is the smallest
    # honest fix.
    board_origin_mm: Point2 = Field(default_factory=lambda: Point2(x_mm=0.0, y_mm=0.0))
    outer_mm: Box3 | None = None
    material: str = "pla"
    mass_kg: float | None = None
    artifacts: Artifacts = Field(default_factory=Artifacts)


# --- envelope (cad -> pcb constraint) -----------------------------------


class Envelope(BaseModel):
    """`cad.constrain_board(reason) -> envelope` — what the enclosure can accept.

    The PCB side re-places/re-routes inside this via `pcb.replace_within`.
    """

    reason: str
    max_outline_mm: BBox2
    max_component_height_mm: float = Field(ge=0)
    max_bottom_component_height_mm: float = Field(default=0.0, ge=0)
    mounting_hole_pattern: list[MountingHole] = Field(default_factory=list)
    keepouts: list[Keepout] = Field(default_factory=list)


# --- violations ---------------------------------------------------------


class Violation(BaseModel):
    """A deterministic geometry failure.

    Carries `measured`/`limit` rather than a bare boolean so the negotiator (and
    coverage analysis) can see *how far* off a design is — §8: "prefer ratios
    over booleans... a boolean-only criterion is invisible because its value
    never moves."
    """

    code: str
    severity: Severity
    detail: str
    measured: float
    limit: float
    unit: str
    ref: str = ""

    @property
    def overshoot(self) -> float:
        """How far past the limit, in `unit`. Negative means inside the limit."""
        return self.measured - self.limit


class FitResult(BaseModel):
    ok: bool
    violations: list[Violation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ok_matches_violations(self) -> "FitResult":
        blocking = [v for v in self.violations if v.severity in ("blocker", "major")]
        if self.ok and blocking:
            raise ValueError("FitResult.ok cannot be True while blocking violations exist")
        return self


# --- intent (design input) ----------------------------------------------


class EnclosureIntent(BaseModel):
    """The free-form-ish half of `cad.design_enclosure(board_report, intent)`.

    Deliberately all-defaults: an agent may set these, but every field is a
    number the harness can check, never prose that only an LLM can interpret.
    """

    material: str = "pla"  # must be a key in engine.catalogue MATERIALS
    wall_thickness_mm: float = Field(default=2.0, gt=0)
    clearance_mm: float = Field(default=1.5, ge=0)  # board edge -> inner wall
    lid_thickness_mm: float = Field(default=2.0, gt=0)
    standoff_height_mm: float = Field(default=4.0, gt=0)
    standoff_outer_diameter_mm: float = Field(default=6.0, gt=0)
    headroom_mm: float = Field(default=2.0, ge=0)  # above tallest top component
    vented: bool = False
    name: str = "enclosure"


class DesignEnclosureRequest(BaseModel):
    board_report: BoardReport
    intent: EnclosureIntent = Field(default_factory=EnclosureIntent)
    emit_artifacts: bool = True


class DesignEnclosureResponse(BaseModel):
    enclosure_report: EnclosureReport
    fit: FitResult
    ir_json: dict | None = None  # the enclosure as a RobotIR link, for engine.evaluate
    evaluation: dict | None = None  # engine.evaluate() report, when it was run


class CheckFitRequest(BaseModel):
    board_report: BoardReport
    enclosure_report: EnclosureReport


class ConstrainBoardRequest(BaseModel):
    reason: str
    enclosure_report: EnclosureReport
    intent: EnclosureIntent = Field(default_factory=EnclosureIntent)
    # Optional and defaulted, so this stays an additive change to a frozen H+4
    # contract file: existing callers keep the previous behaviour exactly.
    board_thickness_mm: float | None = Field(default=None, gt=0)
