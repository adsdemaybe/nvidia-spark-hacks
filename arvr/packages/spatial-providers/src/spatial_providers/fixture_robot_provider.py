"""FixtureRobotProvider — the independent-development path (spec section 11).
Everything downstream of RobotBundle must work against this before any CAD
output exists.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ar_contracts import RobotBundle, RobotCapabilityProfile, RobotIR, RobotManifest

from .robot_provider import RobotProvider

ARVR_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ROBOTS_DIR = ARVR_ROOT / "fixtures" / "spatial-training" / "robots"
DEFAULT_ROBOT_ID = "so101"


class UnknownRobotError(ValueError):
    pass


class FixtureRobotProvider(RobotProvider):
    def __init__(self, robots_dir: Path | None = None) -> None:
        self.robots_dir = robots_dir or DEFAULT_ROBOTS_DIR

    def get_robot_bundle(self, robot_id: str | None = None) -> RobotBundle:
        robot_id = robot_id or DEFAULT_ROBOT_ID
        bundle_dir = self.robots_dir / robot_id
        manifest_path = bundle_dir / "manifest.json"
        if not manifest_path.exists():
            raise UnknownRobotError(f"no fixture robot bundle at {bundle_dir}")

        manifest = RobotManifest.model_validate(json.loads(manifest_path.read_text()))
        robot_ir = RobotIR.model_validate(
            json.loads((bundle_dir / manifest.robot_ir).read_text())
        )
        actuator_path = bundle_dir / "actuator.json"
        actuator = json.loads(actuator_path.read_text()) if actuator_path.exists() else {}
        urdf_path = bundle_dir / manifest.urdf
        visual_glb_path = (bundle_dir / manifest.visual_glb).resolve()
        usd_path = (bundle_dir / manifest.usd).resolve() if manifest.usd else None

        return RobotBundle(
            manifest=manifest,
            robot_ir=robot_ir,
            capability_profile=_derive_capability_profile(robot_ir, actuator),
            urdf_path=urdf_path,
            visual_glb_path=visual_glb_path,
            usd_path=usd_path,
        )


def _derive_capability_profile(robot_ir: RobotIR, actuator: dict) -> RobotCapabilityProfile:
    # arm_dof counts only actuator.json's declared arm joints, not every
    # non-fixed joint in the URDF -- a real gripper joint is a second,
    # separate DOF, not part of the arm's reach (fixed once, so the earlier
    # placeholder-only "count everything non-fixed" shortcut silently broke
    # the moment a robot with a real gripper joint showed up).
    arm_joint_names = actuator.get("joints")
    if arm_joint_names is not None:
        arm_dof = len(arm_joint_names)
    else:
        arm_dof = len([j for j in robot_ir.joints if j.type != "fixed"])
    has_gripper = bool(actuator.get("gripper"))
    workspace_radius_m = sum(
        math.sqrt(sum(c * c for c in j.origin_position_m)) for j in robot_ir.joints
    )
    return RobotCapabilityProfile(
        arm_dof=arm_dof,
        # Honest default when there's no real gripper -- never invented
        # (spec section 10: "do not silently invent the robot's joints").
        end_effector="parallel_gripper" if has_gripper else "none",
        finger_count=2 if has_gripper else 0,
        supports_wrist_pose=True,
        supports_full_hand_retarget=False,
        workspace_radius_m=round(workspace_radius_m, 6),
    )
