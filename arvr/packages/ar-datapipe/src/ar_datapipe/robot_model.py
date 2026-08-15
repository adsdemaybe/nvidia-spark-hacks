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
    # MuJoCo's URDF compiler welds a body connected only by a fixed joint
    # into its parent rather than keeping it as a separate body (confirmed
    # directly: the real SO-101's dummy `gripper_frame_link` tool frame,
    # attached via a fixed joint, produces no MuJoCo body at all -- only
    # Pinocchio preserves it as a distinct frame). When end_effector_frame
    # isn't resolvable as a MuJoCo body, verify.py falls back to
    # `mujoco_ee_body` (a real body -- the fixed joint's parent link) and
    # composes `mujoco_ee_offset_m` (that fixed joint's local translation)
    # onto its world pose, so MuJoCo tracking targets the *exact* same
    # point Pinocchio's IK does, not the nearest ancestor body. None for
    # both means the old behavior: end_effector_frame must itself be a
    # real MuJoCo body (true for the placeholder robot, whose EE has no
    # fixed-joint dummy frame in between).
    mujoco_ee_body: str | None = None
    mujoco_ee_offset_m: tuple[float, float, float] | None = None


DEFAULT_MODEL = RobotModel(urdf_path=DEFAULT_URDF)


def robot_model_from_bundle(bundle) -> RobotModel:  # bundle: ar_contracts.RobotBundle
    """Derives the MuJoCo fallback (mujoco_ee_body/mujoco_ee_offset_m) from
    the bundle's own robot_ir, generically -- not hardcoded per robot. If
    the end-effector frame is reached by a *fixed* joint (a dummy tool
    frame, like the real SO-101's gripper_frame_link), MuJoCo's URDF
    compiler welds it away; fall back to that joint's parent link and
    remember the fixed local offset so verify.py can compose it back onto
    a real body's world pose. A 6-DOF-with-native-body EE (the old
    placeholder) has no such fixed joint, so both stay None -- unchanged
    behavior.
    """
    end_effector_frame = bundle.manifest.end_effectors[0]
    mujoco_ee_body = None
    mujoco_ee_offset_m = None
    for joint in bundle.robot_ir.joints:
        if joint.child_link == end_effector_frame and joint.type == "fixed":
            mujoco_ee_body = joint.parent_link
            mujoco_ee_offset_m = joint.origin_position_m
            break
    return RobotModel(
        urdf_path=bundle.urdf_path,
        end_effector_frame=end_effector_frame,
        mujoco_ee_body=mujoco_ee_body,
        mujoco_ee_offset_m=mujoco_ee_offset_m,
    )
