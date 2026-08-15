"""ar_datapipe — normalize -> retarget (Pinocchio IK) -> verify (MuJoCo
replay) -> export (LeRobot-compatible). See pipeline.run_episode for the
entry point, and ../../fixtures/robot/README.md for the placeholder robot
this currently targets."""

from .pipeline import run_episode
from .retarget import IkSolver, RetargetResult
from .robot_model import DEFAULT_MODEL, RobotModel
from .verify import MujocoReplay, ReplayResult

__all__ = [
    "DEFAULT_MODEL",
    "IkSolver",
    "MujocoReplay",
    "ReplayResult",
    "RetargetResult",
    "RobotModel",
    "run_episode",
]
