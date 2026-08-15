"""Robot model loading — shared by retarget.py (Pinocchio) and verify.py
(MuJoCo). Both load the SAME urdf file so the kinematic chain can never
silently drift between the IK solver and the replay/verification engine.

Default points at the placeholder test arm (fixtures/robot/test_arm.urdf,
NOT the real deployment robot — see the README next to it). Swap
DEFAULT_URDF once a real robot URDF exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_URDF = (
    Path(__file__).resolve().parents[4] / "fixtures" / "robot" / "test_arm.urdf"
)

END_EFFECTOR_FRAME = "end_effector"


@dataclass(frozen=True)
class RobotModel:
    urdf_path: Path
    end_effector_frame: str = END_EFFECTOR_FRAME


DEFAULT_MODEL = RobotModel(urdf_path=DEFAULT_URDF)
