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
    # Every param is either a purchasable-component reference or a
    # provenanced physical quantity — never a bare float (§2).
    params: dict[str, CatalogueParam | Quantity] = Field(default_factory=dict)
    material: CatalogueParam  # catalogue="materials"


class Link(BaseModel):
    id: str
    geometry: GeometrySpec
    pose: Pose = Field(default_factory=Pose)


class JointType(str):
    pass


JointKind = Literal["revolute", "prismatic", "fixed"]


class JointLimits(BaseModel):
    lower: Quantity
    upper: Quantity
    effort: Quantity  # N or N*m depending on joint kind
    velocity: Quantity


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
