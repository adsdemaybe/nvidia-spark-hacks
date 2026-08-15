"""RobotProvider — Shadow Robot Spatial Demonstration Pipeline spec section 8.

The switch-point between "no CAD yet" and "the real generated robot": every
downstream stage (retargeter, shadow robot, simulator) consumes a
`RobotBundle` and must not know or care which provider produced it. No
downstream code may branch on `if source == "fixture"/"generated"` (spec
section 54) — the only place that distinction is allowed to exist is inside
`get_configured_robot_provider()` below, which returns one concrete provider
type and nothing else.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from ar_contracts import RobotBundle


class RobotProvider(ABC):
    @abstractmethod
    def get_robot_bundle(self, robot_id: str | None = None) -> RobotBundle: ...


def get_configured_robot_provider() -> RobotProvider:
    """Reads STRUCT_ROBOT_PROVIDER (default "fixture"). "generated" is a live
    switch-point, not yet a built provider — it raises rather than silently
    falling back, so a misconfiguration is loud instead of quietly serving
    the fixture robot under a "generated" label."""
    kind = os.environ.get("STRUCT_ROBOT_PROVIDER", "fixture")
    if kind == "fixture":
        from .fixture_robot_provider import FixtureRobotProvider

        return FixtureRobotProvider()
    if kind == "generated":
        raise NotImplementedError(
            "STRUCT_ROBOT_PROVIDER=generated has no implementation yet "
            "(Milestone 2+ — see arvr/STATE.md)."
        )
    raise ValueError(f"unknown STRUCT_ROBOT_PROVIDER: {kind!r}")
