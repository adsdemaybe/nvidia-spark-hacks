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


class Quantity(BaseModel):
    """A physical value with a unit and a provenance. Never a bare float."""

    value: float
    unit: str
    provenance: Provenance

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
