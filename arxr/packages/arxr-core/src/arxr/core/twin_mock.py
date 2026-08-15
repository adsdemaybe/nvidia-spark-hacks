"""A fixture TwinState stream (STRUCT_2.md 58).

Stands in for Isaac Sim so that PLACE, REPLAY, FOLLOW, TWIN and CORRECT can all
be built and demoed before the Spark bridge exists. Deterministic by
construction -- tick in, state out, no clock and no RNG -- so two clients on the
same tick see the same world and a bug found in a recording reproduces.

When the real bridge lands this is what it replaces. Nothing in a client should
need to change; if it does, the mock was lying about the contract.

The UI must never present this as live Isaac state (STRUCT_2.md 82).
"""
from __future__ import annotations

import math

from .schemas.twin import ObjectState, RobotState, TaskState, TwinState

# An arbitrary fixed epoch so timestamps look real without a wall clock.
EPOCH_NS = 1_700_000_000_000_000_000

DEFAULT_HZ = 30.0

# A 6-DOF arm's home pose, and how far each joint swings. Chosen only to be
# visibly non-static in an AR view -- this is not a kinematically meaningful
# trajectory and must not be treated as one.
HOME_JOINTS = (0.1, 0.5, -0.2, 0.4, 0.1, 0.0)
JOINT_AMPLITUDES = (0.4, 0.3, 0.5, 0.3, 0.2, 0.6)
CYCLE_SECONDS = 6.0

TASK_ID = "cube_to_bin"
# Where in the cycle each phase begins, as a fraction of one loop.
TASK_PHASES = (
    (0.00, "approaching"),
    (0.30, "grasping"),
    (0.45, "moving"),
    (0.80, "placing"),
    (0.95, "success"),
)


class MockTwinSource:
    """Generates TwinState for a tick index. Pure function of the tick."""

    def __init__(self, scene_id: str = "demo_room", hz: float = DEFAULT_HZ) -> None:
        if hz <= 0.0:
            raise ValueError(f"hz must be positive; got {hz}")
        self.scene_id = scene_id
        self.hz = hz

    def _phase(self, tick: int) -> float:
        """Position within the loop, in [0, 1)."""
        seconds = tick / self.hz
        return (seconds % CYCLE_SECONDS) / CYCLE_SECONDS

    def at_tick(self, tick: int) -> TwinState:
        phase = self._phase(tick)
        swing = math.sin(phase * 2.0 * math.pi)

        joints = tuple(
            home + amplitude * swing
            for home, amplitude in zip(HOME_JOINTS, JOINT_AMPLITUDES, strict=True)
        )

        # The cube rides along with the arm once it has been grasped; the bin
        # never moves. Enough structure for a client to show something real.
        carried = 0.30 <= phase < 0.95
        cube_z = 0.70 + (0.25 * swing if carried else 0.0)

        status = "idle"
        for start, name in TASK_PHASES:
            if phase >= start:
                status = name

        return TwinState(
            timestamp_ns=EPOCH_NS + round(tick * 1e9 / self.hz),
            scene_id=self.scene_id,
            robot=RobotState(id="robot_01", joint_positions=joints),
            objects=[
                ObjectState(id="cube_01", position_m=(0.3, 0.1, cube_z)),
                ObjectState(id="bin_01", position_m=(0.6, -0.2, 0.0)),
            ],
            task=TaskState(id=TASK_ID, status=status),
        )
