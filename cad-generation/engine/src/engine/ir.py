"""Robot IR — the single source of truth every downstream layer reads/writes.

Two rules from §2 of robot-platform-tech-stack.md that must not be relaxed:

- CatalogueParam, never a free scalar, for any real (purchasable) part.
- Quantity requires Provenance — no bare floats near a physical constant.

Topology is data, not code: a quadruped and a rover differ by IR document,
not by Python. This module defines the schema only — it performs no I/O and
computes nothing (mass properties, CoM, inertia are computed downstream by
the geometry/mass-properties layer, never authored here; see §11 non-negotiable #2).
"""

from __future__ import annotations

import hashlib
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

ProvenanceStatus = Literal["CONFIRMED", "INFERRED", "ASSUMED", "MEASURED"]


class Provenance(BaseModel):
    """Where a Quantity's value came from. See §5 provenance ladder."""

    status: ProvenanceStatus
    source: str
    note: str = ""

    @field_validator("source")
    @classmethod
    def _source_required_for_confirmed(cls, v: str, info) -> str:
        # CI-equivalent check, enforced at construction time rather than in a
        # separate CI step: "CI fails if a CONFIRMED entry lacks a resolvable
        # source" (§5) — cheapest place to catch it is right here.
        status = info.data.get("status")
        if status == "CONFIRMED" and not v.strip():
            raise ValueError("CONFIRMED provenance requires a resolvable source")
        return v


# §5's ladder, as an order rather than a paragraph. MEASURED outranks CONFIRMED
# because an instrumented number beats a manufacturer's: the datasheet describes
# the part family, the measurement describes the one bolted to this robot.
_PROVENANCE_RANK: dict[str, int] = {
    "ASSUMED": 0,
    "INFERRED": 1,
    "CONFIRMED": 2,
    "MEASURED": 3,
}


def worst_provenance(*statuses: str) -> str:
    """The weakest status among the inputs — what a verdict built on them is worth.

    §12 non-negotiable #3: "every verdict states the worst provenance among its
    inputs". A tip-over PASS computed from a CONFIRMED mass and an ASSUMED
    friction coefficient is an ASSUMED result, and reporting it as anything else
    is the specific way a report lies without containing a false number.
    """
    known = [s for s in statuses if s in _PROVENANCE_RANK]
    if not known:
        return "ASSUMED"
    return min(known, key=lambda s: _PROVENANCE_RANK[s])


class Quantity(BaseModel):
    """A physical value with a unit and a provenance. Never a bare float.

    The unit is parsed by pint at construction (§1, v3: "a bare float never
    crosses an interface"). That turns two silent failures into loud ones: a
    typo like `"Nm/s"` used to reach a report unchallenged, and a conversion
    written by hand per module used to be where the factor-of-ten servo error
    came from. `to()` replaces the hand-written factor.
    """

    value: float
    unit: str
    provenance: Provenance

    @field_validator("unit")
    @classmethod
    def _unit_must_parse(cls, v: str) -> str:
        from engine.units import parse

        parse(v)  # raises UnitError, which is a ValueError, so pydantic reports it
        return v

    def to(self, unit: str) -> "Quantity":
        """This quantity expressed in another unit, provenance carried through.

        Provenance is preserved rather than downgraded: converting 30 kgf*cm to
        N*m does not make the number less confirmed, it makes it more usable.
        A conversion across dimensions raises instead.
        """
        from engine.units import convert

        return Quantity(
            value=convert(self.value, self.unit, unit), unit=unit, provenance=self.provenance
        )

    def magnitude_in(self, unit: str) -> float:
        """The bare number in `unit` — for arithmetic, at the point of use."""
        from engine.units import convert

        return convert(self.value, self.unit, unit)

    def __repr__(self) -> str:  # pragma: no cover - convenience only
        return f"Quantity({self.value}{self.unit}, {self.provenance.status})"


class CatalogueParam(BaseModel):
    """A reference to a purchasable, discrete component. Never a free scalar."""

    kind: Literal["catalogue"] = "catalogue"
    value: str  # catalogue key, e.g. "planetary_13.73"
    catalogue: str  # e.g. "stepper_motors"


class Vec3(BaseModel):
    x: float
    y: float
    z: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


class Pose(BaseModel):
    """Position + orientation of a link/joint origin, in the parent frame."""

    position: Vec3 = Field(default_factory=lambda: Vec3(x=0.0, y=0.0, z=0.0))
    # Intrinsic XYZ Euler angles, radians.
    rotation: Vec3 = Field(default_factory=lambda: Vec3(x=0.0, y=0.0, z=0.0))


class GeometrySpec(BaseModel):
    """What to build for a link: a registry key + typed, provenanced params.

    `generator` must match a key registered in engine.geometry.registry — the
    registry is the only place topology-specific geometry logic lives (§2:
    "a registry, never a hierarchy").
    """

    generator: str
    # Every *measurement* is either a purchasable-component reference or a
    # provenanced physical quantity — never a bare float (§2). Plain strings are
    # allowed alongside them for identifiers that are not measurements at all:
    # a vendored asset path, or the sha256 pinning it. Attaching a Provenance to
    # a filename would be provenance theatre, and the §2 rule exists to stop an
    # optimizer exploiting an untethered *number* — a filename is not one.
    params: dict[str, CatalogueParam | Quantity | str] = Field(default_factory=dict)
    material: CatalogueParam  # catalogue="materials"


class Link(BaseModel):
    id: str
    geometry: GeometrySpec
    pose: Pose = Field(default_factory=Pose)


JointKind = Literal["revolute", "prismatic", "fixed"]


class JointLimits(BaseModel):
    """Effort and velocity always; positional bounds only if the joint has them.

    `lower`/`upper` of `None` means the joint turns without end — a wheel. That
    needs to be sayable, because the alternative is authors encoding "no limit"
    as a wide-looking range, and a wheel written as +/-pi is a wheel that stops
    dead after half a turn. It happened here: the rover's drive joints carried
    +/-pi with the provenance note "continuous rotation", passed every static
    criterion, and drove 34 mm before welding solid against the limit.

    Dropping the whole `JointLimits` object would express it too, but it would
    also discard the effort and velocity ratings, which are real catalogue facts
    about the actuator and are what URDF's `continuous` type still wants.
    """

    lower: Quantity | None = None
    upper: Quantity | None = None
    effort: Quantity  # N or N*m depending on joint kind
    velocity: Quantity

    @property
    def bounded(self) -> bool:
        return self.lower is not None and self.upper is not None

    @model_validator(mode="after")
    def _bounds_come_as_a_pair(self) -> "JointLimits":
        if (self.lower is None) != (self.upper is None):
            raise ValueError(
                "joint limits must declare both lower and upper or neither; "
                "one-sided bounds are not representable in URDF or MJCF"
            )
        if self.bounded and self.lower.value > self.upper.value:
            raise ValueError(
                f"joint limit lower ({self.lower.value}) exceeds upper ({self.upper.value})"
            )
        return self


class Joint(BaseModel):
    id: str
    kind: JointKind
    parent: str  # Link.id
    child: str  # Link.id
    origin: Pose = Field(default_factory=Pose)
    axis: Vec3 = Field(default_factory=lambda: Vec3(x=0.0, y=0.0, z=1.0))
    limits: JointLimits | None = None
    actuator: CatalogueParam | None = None  # catalogue="stepper_motors" etc.

    @field_validator("limits")
    @classmethod
    def _fixed_has_no_limits(cls, v, info):
        if info.data.get("kind") == "fixed" and v is not None:
            raise ValueError("fixed joints must not declare limits")
        return v


# --- electronics subsystem (§2 "new in v3", §7) -------------------------
#
# The robot side owns *requirements*; `pcb-ai` fills in *facts*. Keeping both in
# one set of nodes rather than two is deliberate: the whole point of the §7
# contract is that a board fact lands where the robot's criteria already look,
# so `rail_margin` reads the same object whether the board has been designed yet
# or not. A field that only the PCB side can fill is `None` until it has run,
# and every criterion that reads one says so when it is missing.


class Rail(BaseModel):
    """A power rail: what voltage, and how much current the robot budgets on it.

    `budget_current` is the robot's claim, computed by the tier-0 energy pass
    from worst-case actuator draw — not a number an agent picks. It becomes a
    hard input to `pcb-ai`'s trace sizing, which is why getting it wrong shows
    up as a burnt trace rather than a failed assertion.
    """

    id: str
    voltage: Quantity  # V, nominal
    budget_current: Quantity  # A, worst case the robot side promises not to exceed
    # Series resistance from the source to the board's input: battery internal
    # resistance plus whatever the power harness adds. Drives the sag term in
    # the §3 actuator model. ASSUMED until a harness exists.
    source_resistance: Quantity | None = None  # ohm


class MountPattern(BaseModel):
    """Where a board bolts down, in the bay's frame. Millimetres, board plane."""

    hole_diameter: Quantity
    positions: list[Vec3] = Field(default_factory=list)


class BoardSpec(BaseModel):
    """One PCB the robot needs: a requirement, plus facts once `pcb-ai` has run.

    `mounted_on` names the link the board bolts to, so the board's measured mass
    lands on the right link in the mass model instead of being smeared over the
    chassis — the difference between a CoM that matches the built robot and one
    that is 15 mm off in the direction of the heaviest board.
    """

    id: str
    purpose: str  # "motor driver carrier", "power distribution", ...
    mounted_on: str  # Link.id — the bay this board lives in
    rails: list[str] = Field(default_factory=list)  # Rail.id it consumes/provides

    # --- requirement: what the bay can accept (flows down to pcb-ai) ---
    max_outline: Vec3  # mm, x/y extents; z unused (board plane)
    max_component_height: Quantity  # mm, top side
    mount: MountPattern | None = None
    keepouts: list[str] = Field(default_factory=list)  # free-text keepout reasons
    connector_rules: list[str] = Field(default_factory=list)  # "at_edge:J1:south"

    # --- fact: measured by pcb-ai from the routed board (flows back up) ---
    # MEASURED-class per §5 when present: computed by an independent gated
    # pipeline from the routed artifact, not asserted by any agent.
    measured_mass: Quantity | None = None  # kg
    measured_com: Vec3 | None = None  # mm, board frame
    measured_dissipation: Quantity | None = None  # W
    measured_outline: Vec3 | None = None  # mm
    measured_max_component_height: Quantity | None = None  # mm
    gate_status: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"
    run_dir: str = ""  # resolvable pointer to the pcb-ai run that produced the facts

    @property
    def designed(self) -> bool:
        return self.gate_status != "NOT_RUN"


class Harness(BaseModel):
    """A cable run from a board connector to an actuator or another board.

    Exists so `harness_drop` has something to compute against. Before §7 the
    length was a guess; once Circuit JSON gives connector positions it is
    derived, and the provenance on `length` says which of the two it is.
    """

    id: str
    from_board: str  # BoardSpec.id
    to: str  # Joint.id (actuator) or BoardSpec.id
    rail: str  # Rail.id
    length: Quantity  # m, one-way
    conductor_area: Quantity  # m^2 — cross-section of one conductor

    # Copper at 20 C. Not a design variable; a physical constant that lives here
    # so the drop calculation has no magic numbers in it.
    resistivity: Quantity | None = None  # ohm*m


class Electronics(BaseModel):
    """The robot's electrical subsystem: rails, boards, harnesses, and a pack.

    Optional on `RobotIR`. A Phase-1 robot that has not thought about power at
    all is a legitimate thing to evaluate — the electronics criteria simply do
    not fire, and `evaluate()` reports that they did not rather than passing
    them. What is *not* legitimate is a half-filled subsystem, so the validators
    below refuse dangling references.
    """

    battery: CatalogueParam | None = None  # catalogue="batteries"
    rails: list[Rail] = Field(default_factory=list)
    boards: list[BoardSpec] = Field(default_factory=list)
    harnesses: list[Harness] = Field(default_factory=list)
    # Which rail drives each actuated joint. `Joint.id -> Rail.id`. This is the
    # mapping that makes "a motor with no driver rail" checkable at the robot
    # level (§2) instead of at PCB import time.
    joint_rail: dict[str, str] = Field(default_factory=dict)
    # Duty cycle of the mission the energy tier sizes against: fraction of time
    # actuators draw their worst-case current. ASSUMED until a mission profile
    # exists, and the runtime criterion reports it as such.
    mission_duty: float = Field(default=0.3, ge=0.0, le=1.0)
    mission_duration: Quantity | None = None  # s — the runtime target, if stated

    @model_validator(mode="after")
    def _references_resolve(self) -> "Electronics":
        rail_ids = {r.id for r in self.rails}
        board_ids = {b.id for b in self.boards}
        for name, items in (("rail", self.rails), ("board", self.boards), ("harness", self.harnesses)):
            ids = [i.id for i in items]
            if len(ids) != len(set(ids)):
                dupes = sorted({i for i in ids if ids.count(i) > 1})
                raise ValueError(f"duplicate {name} ids: {dupes}")
        for board in self.boards:
            for rail in board.rails:
                if rail not in rail_ids:
                    raise ValueError(f"board {board.id!r} names unknown rail {rail!r}")
        for harness in self.harnesses:
            if harness.from_board not in board_ids:
                raise ValueError(f"harness {harness.id!r} leaves unknown board {harness.from_board!r}")
            if harness.rail not in rail_ids:
                raise ValueError(f"harness {harness.id!r} names unknown rail {harness.rail!r}")
        for joint_id, rail in self.joint_rail.items():
            if rail not in rail_ids:
                raise ValueError(f"joint {joint_id!r} is assigned unknown rail {rail!r}")
        return self

    def rail(self, rail_id: str) -> Rail:
        for r in self.rails:
            if r.id == rail_id:
                return r
        raise KeyError(f"no rail {rail_id!r}")

    def board(self, board_id: str) -> BoardSpec:
        for b in self.boards:
            if b.id == board_id:
                return b
        raise KeyError(f"no board {board_id!r}")

    def harness_for(self, joint_id: str) -> "Harness | None":
        for h in self.harnesses:
            if h.to == joint_id:
                return h
        return None


class RobotIR(BaseModel):
    """The complete design intent for one robot. Immutable once created —
    a revision is a new RobotIR, never a mutation of an existing one (§2, §11.8).
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    root_link: str
    links: list[Link]
    joints: list[Joint] = Field(default_factory=list)
    # How the root link meets the world. "floating" is a robot that carries
    # itself — a rover, a quadruped, a drone. "fixed" is one bolted down: an arm
    # on a bench, a delta on a frame.
    #
    # This is not cosmetic, and it is not inferable. A bench arm reaching out
    # over its own base plate has its centre of mass well outside its footprint,
    # so `static_margin` fails it — correctly, if it were free-standing, and
    # meaninglessly, since it is bolted to a table. SO-101 fails by 1.56 support
    # half-widths. Simulating it is worse: a fixed-base robot given a free joint
    # falls over on the first step, and `settles` reports a design fault that is
    # entirely an artefact of how it was mounted.
    base: Literal["floating", "fixed"] = "floating"
    # The electrical half of the robot (§2 "new in v3"). `None` means the design
    # has not been powered yet — a real Phase-1 state, distinct from "powered
    # and empty". Every electronics criterion returns no results for `None` and
    # `evaluate()` reports the absence, which is §5's "a tier that didn't run is
    # not a pass" applied to a subsystem.
    electronics: Electronics | None = None

    @field_validator("links")
    @classmethod
    def _unique_link_ids(cls, v: list[Link]) -> list[Link]:
        ids = [link.id for link in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate link ids: {ids}")
        return v

    @field_validator("joints")
    @classmethod
    def _joints_reference_known_links(cls, v: list[Joint], info) -> list[Joint]:
        links = info.data.get("links") or []
        link_ids = {link.id for link in links}
        for joint in v:
            if joint.parent not in link_ids:
                raise ValueError(f"joint {joint.id!r} parent {joint.parent!r} is not a known link")
            if joint.child not in link_ids:
                raise ValueError(f"joint {joint.id!r} child {joint.child!r} is not a known link")
            if joint.parent == joint.child:
                raise ValueError(f"joint {joint.id!r} connects link {joint.child!r} to itself")

        joint_ids = [joint.id for joint in v]
        if len(joint_ids) != len(set(joint_ids)):
            dupes = sorted({j for j in joint_ids if joint_ids.count(j) > 1})
            raise ValueError(f"duplicate joint ids: {dupes}")

        # One parent per link, or the tree isn't a tree. The kinematics walk
        # assigns `frames[joint.child]` per joint, so a second joint claiming the
        # same child silently wins and the robot evaluated is not the robot
        # authored — a wrong answer rather than an error.
        seen_children: dict[str, str] = {}
        for joint in v:
            if joint.child in seen_children:
                raise ValueError(
                    f"link {joint.child!r} is the child of both {seen_children[joint.child]!r} "
                    f"and {joint.id!r}; each link may have at most one parent joint"
                )
            seen_children[joint.child] = joint.id

        # The root is the one link with no parent. Without this, `a -> b` plus
        # `b -> a` satisfies the one-parent rule above (each link has exactly one)
        # while still being a cycle. Together the two rules make every subgraph
        # reachable from the root a tree; an unreachable cycle is caught separately
        # by `kinematics.link_frames`, which reports it as unreachable.
        root = info.data.get("root_link")
        if root is not None and root in seen_children:
            raise ValueError(
                f"root link {root!r} is the child of joint {seen_children[root]!r}: "
                "the root has no parent by definition, and this closes a cycle"
            )
        return v

    @model_validator(mode="after")
    def _electronics_references_resolve(self) -> "RobotIR":
        """Electronics may only name links and joints that exist.

        `Electronics` validates itself in isolation; it cannot see the topology.
        A board `mounted_on` a link that was renamed in a revision is the failure
        this catches — otherwise the board's measured mass silently lands nowhere
        and the CoM is quietly wrong rather than loudly missing.
        """
        if self.electronics is None:
            return self
        link_ids = {link.id for link in self.links}
        joint_ids = {joint.id for joint in self.joints}
        board_ids = {b.id for b in self.electronics.boards}
        for board in self.electronics.boards:
            if board.mounted_on not in link_ids:
                raise ValueError(
                    f"board {board.id!r} is mounted on unknown link {board.mounted_on!r}"
                )
        for joint_id in self.electronics.joint_rail:
            if joint_id not in joint_ids:
                raise ValueError(f"joint_rail names unknown joint {joint_id!r}")
        for harness in self.electronics.harnesses:
            if harness.to not in joint_ids | board_ids:
                raise ValueError(
                    f"harness {harness.id!r} runs to {harness.to!r}, which is neither "
                    "a joint nor a board"
                )
        return self

    def link(self, link_id: str) -> Link:
        for link in self.links:
            if link.id == link_id:
                return link
        raise KeyError(f"no link {link_id!r} in RobotIR {self.name!r}")

    def content_hash(self) -> str:
        """Stable-within-this-process dedupe key for identical proposals (§6:
        "dedupe identical proposals, enables caching"). Excludes `id`, which
        is randomly generated per instance and carries no design content.
        """
        canonical = self.model_dump_json(exclude={"id"})
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Revision(BaseModel):
    """An immutable, append-only record of one RobotIR (§2, §6, §11.8).

    Mirrors the `revisions` table shape from §6 — this is the pure-data
    shape; persisting it to SQLite/Postgres is a storage-layer concern the
    engine itself never touches (§11.7: the engine has zero I/O).
    """

    id: UUID = Field(default_factory=uuid4)
    design_id: UUID
    parent_id: UUID | None = None
    revision_no: int
    ir: RobotIR
    ir_hash: str = ""
    author: str  # 'agent:claude' | 'user:<id>'
    rationale: str = ""

    @field_validator("author")
    @classmethod
    def _author_shape(cls, v: str) -> str:
        if not (v.startswith("agent:") or v.startswith("user:")):
            raise ValueError("author must be 'agent:<name>' or 'user:<id>'")
        return v

    @model_validator(mode="after")
    def _fill_ir_hash(self) -> "Revision":
        if not self.ir_hash:
            self.ir_hash = self.ir.content_hash()
        return self
