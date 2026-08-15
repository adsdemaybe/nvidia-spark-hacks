"""task.json -- one machine-checkable manipulation task, bound to one scene.

The predicate library is CLOSED: on, near, inside, grasped, pose_within, plus
AND/OR/NOT composition at max depth 2 with max 3 leaves. An LLM may compose
from the library; it may never extend it. Entities bind by prim path -- two
mugs in a scene and label-binding is silently ambiguous.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..artifacts import ArtifactRef
from ..geometry import Pose
from ..provenance import Measured, Proposer

SCHEMA_VERSION = "1.0.0"

PREDICATE_OPS = ("on", "near", "inside", "grasped", "pose_within")
LOGIC_OPS = ("and", "or", "not")
MAX_DEPTH = 2
MAX_LEAVES = 3


class Predicate(BaseModel):
    op: str
    args: list[str] = Field(default_factory=list)  # instance_ids (or "FLOOR")
    kwargs: dict[str, Any] = Field(default_factory=dict)  # tolerances; d_max REQUIRED for near
    children: list[Predicate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> Predicate:
        if self.op in LOGIC_OPS:
            if not self.children:
                raise ValueError(f"logic op {self.op!r} needs children")
            if self.op == "not" and len(self.children) != 1:
                raise ValueError("'not' takes exactly one child")
        elif self.op in PREDICATE_OPS:
            if self.children:
                raise ValueError(f"leaf predicate {self.op!r} cannot have children")
            if self.op == "near" and "d_max" not in self.kwargs:
                # A hidden default here is how you ship a trivially-true task.
                raise ValueError("'near' requires an explicit d_max")
        else:
            raise ValueError(f"unknown op {self.op!r} -- the library is closed")
        if self.depth() > MAX_DEPTH:
            raise ValueError(f"predicate deeper than {MAX_DEPTH}")
        if self.n_leaves() > MAX_LEAVES:
            raise ValueError(f"predicate has more than {MAX_LEAVES} leaves")
        return self

    def depth(self) -> int:
        return 1 + max((c.depth() for c in self.children), default=0) if self.children else 0

    def n_leaves(self) -> int:
        return sum(c.n_leaves() for c in self.children) if self.children else 1

    def leaves(self) -> list[Predicate]:
        if not self.children:
            return [self]
        out: list[Predicate] = []
        for c in self.children:
            out.extend(c.leaves())
        return out

    def entity_ids(self) -> set[str]:
        out: set[str] = set()
        for leaf in self.leaves():
            out.update(a for a in leaf.args if a != "FLOOR")
        return out


class Witness(BaseModel):
    """Recorded proof that the success predicate is satisfiable: a goal state we
    actually constructed and verified. Doubles as a scripted-demo seed later."""

    goal_poses: dict[str, Pose] = Field(default_factory=dict)  # instance_id -> pose
    waypoints: list[Pose] = Field(default_factory=list)  # ee path that was checked clear
    checked_collision_free: bool = False
    checked_reachable: bool = False


class FeasibilityReport(BaseModel):
    target_reachable: bool = False
    goal_reachable: bool = False
    ik_method: Literal["reach_envelope", "pinocchio_dls", "none"] = "none"
    min_clearance_m: float | None = None


class TaskPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    task_id: str
    scene_ref: ArtifactRef
    scene_id: str
    family: Literal["pick_place", "place_near", "push_to", "stack"]
    instruction: str
    instruction_proposer: Proposer
    target_instances: list[str] = Field(default_factory=list)
    receptacle_instances: list[str] = Field(default_factory=list)
    success: Predicate
    failure: list[Predicate] = Field(default_factory=list)  # early-termination, e.g. on(x, FLOOR)
    # recorded gate-3 artifact: runtime re-asserts these on reset, so a predicate
    # regression surfaces immediately instead of as a mysterious 100% success rate
    initial_predicate_values: dict[str, bool] = Field(default_factory=dict)
    witness: Witness | None = None
    feasibility: FeasibilityReport = Field(default_factory=FeasibilityReport)
    horizon_steps: int = 600
    sim_dt: float = 1.0 / 120.0
    difficulty: Measured[float] | None = None
