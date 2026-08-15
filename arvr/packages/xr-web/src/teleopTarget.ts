/**
 * RobotTeleopTarget — the task-space intent a tracked hand expresses.
 *
 * Spec section 15: wrist/palm translation becomes the end-effector target
 * position, palm orientation becomes an orientation *preference*, and pinch
 * becomes the gripper command.
 *
 * This is deliberately NOT a retargeter. There is no IK here and there never
 * should be: the shared `ArmRetargeter` on the backend owns turning a target
 * into q(t), and the whole point of the provider architecture is that the
 * Quest contributes input and view, not robot kinematics. What this module
 * does is name the intent, so it can be recorded alongside the raw hand data
 * and shown on the HUD -- the same intent the backend independently derives
 * from the HandFrame it is already sent.
 *
 * `orientation_weight` exists because the SO-101 has five actuated joints.
 * Position and an arbitrary orientation are only jointly reachable on a
 * lower-dimensional manifold, so the solver runs position-only for this arm
 * (see IkSolver's `position_only`). Recording the weight keeps the human's
 * wrist orientation in the episode as a real preference that a future 6-DOF
 * embodiment can honor, without pretending this one does.
 */

import type { Quat, Vec3 } from "./contracts";
import type { WireHandFrame } from "./liveRetargetSession";

export interface RobotTeleopTarget {
  timestamp_ns: number;
  ee_position_m: Vec3;
  ee_orientation_xyzw: Quat;
  /** 0 = orientation ignored by the solver, 1 = orientation is a hard
   * constraint. Set from the robot's arm DOF, not from taste. */
  orientation_weight: number;
  /** 0 = open, 1 = closed. */
  gripper: number;
}

/**
 * Orientation weight for an arm with this many actuated joints.
 *
 * Below six, orientation cannot be commanded independently of position, so
 * the honest weight is zero -- a nonzero weight would describe a constraint
 * the solver does not apply.
 */
export function orientationWeightForDof(armDof: number): number {
  return armDof >= 6 ? 1 : 0;
}

/** The SO-101, the robot this demo actually runs on. */
export const SO101_ARM_DOF = 5;

/**
 * Build the target from an already-struct_world hand frame.
 *
 * Takes the wire frame rather than a raw WebXR `HandFrame` so the
 * coordinates are the calibrated, canonical ones -- the same bytes the
 * backend retargets from. Deriving from raw device coordinates here would
 * quietly reintroduce the uncalibrated-origin bug the headset path already
 * fixed.
 */
export function teleopTargetFromWireFrame(
  frame: WireHandFrame,
  gripper: number,
  armDof: number = SO101_ARM_DOF,
): RobotTeleopTarget | null {
  const wrist = frame.joints["wrist"];
  if (!wrist) return null;
  return {
    timestamp_ns: frame.timestamp_ns,
    ee_position_m: [...wrist.position_m] as Vec3,
    ee_orientation_xyzw: [...wrist.orientation_xyzw] as Quat,
    orientation_weight: orientationWeightForDof(armDof),
    gripper: clamp01(gripper),
  };
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}
