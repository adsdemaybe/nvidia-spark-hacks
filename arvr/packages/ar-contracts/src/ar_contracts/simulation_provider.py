"""SimulationProvider — Shadow Robot Spatial Demonstration Pipeline spec
section 31.

Lives in ar_contracts, not spatial_providers, despite spatial_providers
being where MuJoCoSimulationProvider (the concrete implementation) lives.
Reason, found while building the Phase 11 orchestrator (ar_datapipe): that
orchestrator needs this interface to type its `simulation_provider`
parameter, but spatial_providers already depends on ar_datapipe (for the
MuJoCo-backed provider) -- ar_datapipe depending back on spatial_providers
for just this interface would be a circular package dependency.
ar_contracts is the one thing both already depend on, so the interface
(data shapes + the ABC signature) belongs here; only the concrete
Fixture/MuJoCo/Mock *implementations* belong in spatial_providers.
`spatial_providers.simulation_provider` re-exports these two names for
whatever already imports them from there.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .interactable_asset import InteractableAsset
from .robot_bundle import RobotBundle
from .robot_trajectory import RobotTrajectory
from .verification_result import VerificationResult


@dataclass(frozen=True)
class TaskSpec:
    """A named generalization of ar_datapipe.pipeline's existing
    goal_position_m/goal_tolerance_m parameters -- the task predicate this
    milestone checks is "did the tool tip end up near a goal point", same
    idea as the old TEACH pipeline's cube_to_bin check, just parameterized
    instead of hardcoded.

    `replay_and_verify()` receives only the robot's own RobotTrajectory --
    no genuinely simulated object physics (SimulationProvider's signature
    has no live cube/drawer body to query). object_position_m/pull_axis
    below extend the *same* EE-trajectory-shaped check the base
    goal_position_m predicate already is, into grasp-and-place and pull
    shapes, rather than pretending to check real object motion this
    milestone doesn't simulate:

    - grasp-and-place (object_position_m set): success requires the
      gripper to read closed while the EE trajectory passed within
      object_capture_radius_m of object_position_m at some point, *and*
      the trajectory ends near goal_position_m with the gripper open
      again -- "picked something up near here, put it down over there",
      approximated from the recorded EE+gripper trajectory alone.
    - pull (pull_axis set): success requires the EE's net displacement
      between the trajectory's first and last frame, projected onto
      pull_axis, to be at least pull_distance_m (minus tolerance_m) --
      matches an asset's own declared drawer axis/limit_m directly.

    Exactly one of {only goal_position_m, object_position_m, pull_axis}
    should be set per task; MuJoCoSimulationProvider checks them in that
    priority order.
    """

    goal_position_m: tuple[float, float, float]
    tolerance_m: float = 0.05
    object_position_m: tuple[float, float, float] | None = None
    object_capture_radius_m: float = 0.05
    pull_axis: tuple[float, float, float] | None = None
    pull_distance_m: float | None = None


class SimulationProvider(ABC):
    @abstractmethod
    def replay_and_verify(
        self,
        robot_bundle: RobotBundle,
        asset_bundle: InteractableAsset,
        trajectory: RobotTrajectory,
        task: TaskSpec,
    ) -> VerificationResult: ...
