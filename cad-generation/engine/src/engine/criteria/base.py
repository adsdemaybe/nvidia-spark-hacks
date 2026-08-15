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
    # §12 non-negotiable #3: "every verdict states the worst provenance among
    # its inputs". A PASS is only as good as its weakest number, and a report
    # that does not say so is the one way it can mislead without containing a
    # false value.
    #
    # The default is INFERRED rather than ASSUMED because it is the truth for
    # the criteria that predate this field: they are computed from B-rep
    # geometry and a CONFIRMED material density, and §3's table puts a tensor
    # derived that way at exactly INFERRED. A criterion that knows better —
    # anything reading a MEASURED board fact, or an ASSUMED friction — states
    # its own.
    provenance: str = "INFERRED"


# A criterion may apply zero or more times per design (e.g. mount_fits fires
# once per fixed joint) — hence a list, not a single result.
CriterionFn = Callable[[RobotIR, dict[str, MassProperties]], list[CriterionResult]]


@dataclass(frozen=True)
class Criterion:
    name: str
    tier: int
    fn: CriterionFn
