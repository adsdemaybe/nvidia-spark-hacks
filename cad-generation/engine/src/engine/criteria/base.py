"""Criterion shape. §8: "Every criterion must expose a magnitude, not just
pass/fail — a boolean-only criterion is invisible to coverage analysis
because its value never moves."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from engine.ir import RobotIR
from engine.mass_properties import MassProperties


@dataclass(frozen=True)
class CriterionResult:
    name: str
    magnitude: float
    passed: bool
    unit: str
    detail: str = ""


# A criterion may apply zero or more times per design (e.g. mount_fits fires
# once per fixed joint) — hence a list, not a single result.
CriterionFn = Callable[[RobotIR, dict[str, MassProperties]], list[CriterionResult]]


@dataclass(frozen=True)
class Criterion:
    name: str
    tier: int
    fn: CriterionFn
