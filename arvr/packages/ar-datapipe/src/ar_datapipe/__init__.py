"""ar_datapipe — normalize -> retarget (Pinocchio IK) -> verify (MuJoCo
replay) -> export (LeRobot-compatible). See pipeline.run_episode for the
entry point, and ../../fixtures/robot/README.md for the placeholder robot
this currently targets."""

from .arm_retargeter import ArmRetargeter, ArmRetargetResult
from .export import export_robot_episode
from .interaction_ir import derive_interaction_ir, object_relative_pose
from .pipeline import run_episode
from .retarget import IkSolver, RetargetResult
from .robot_model import DEFAULT_MODEL, RobotModel
from .spatial_pipeline import run_spatial_episode
from .verify import MujocoReplay, ReplayResult

__all__ = [
    "DEFAULT_MODEL",
    "ArmRetargetResult",
    "ArmRetargeter",
    "IkSolver",
    "MujocoReplay",
    "ReplayResult",
    "RetargetResult",
    "RobotModel",
    "derive_interaction_ir",
    "export_robot_episode",
    "object_relative_pose",
    "run_episode",
    "run_spatial_episode",
]
