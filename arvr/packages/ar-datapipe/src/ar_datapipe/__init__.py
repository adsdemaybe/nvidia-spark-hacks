"""ar_datapipe — normalize -> retarget (Pinocchio IK) -> verify (MuJoCo
replay) -> export (LeRobot-compatible). See pipeline.run_episode for the
entry point, and ../../fixtures/robot/README.md for the placeholder robot
this currently targets."""

from .arm_retargeter import ArmRetargeter, ArmRetargetResult
from .export import export_robot_episode
from .interaction_ir import (
    derive_interaction_ir,
    derive_sort_interaction_ir,
    object_relative_pose,
)
from .pipeline import run_episode
from .retarget import IkSolver, RetargetResult
from .robot_model import DEFAULT_MODEL, RobotModel
from .sort_task import (
    SORT_TASK_ID,
    SORT_TASK_SPEC,
    SortTaskResult,
    SortTaskSpec,
    containing_basket,
    evaluate_sort_episode,
    evaluate_sort_task,
    inside_basket,
)
from .spatial_pipeline import run_spatial_episode
from .verify import MujocoReplay, ReplayResult

__all__ = [
    "DEFAULT_MODEL",
    "SORT_TASK_ID",
    "SORT_TASK_SPEC",
    "ArmRetargetResult",
    "ArmRetargeter",
    "IkSolver",
    "MujocoReplay",
    "ReplayResult",
    "RetargetResult",
    "RobotModel",
    "SortTaskResult",
    "SortTaskSpec",
    "containing_basket",
    "derive_interaction_ir",
    "derive_sort_interaction_ir",
    "evaluate_sort_episode",
    "evaluate_sort_task",
    "export_robot_episode",
    "inside_basket",
    "object_relative_pose",
    "run_episode",
    "run_spatial_episode",
]
