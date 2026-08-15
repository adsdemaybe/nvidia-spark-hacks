/**
 * Where Spatial Teach's fixture scene sits in struct_world.
 *
 * Extracted from spatialTeachMain.ts when the openxr path needed the same
 * numbers: the two calibration anchors a human pinches in their room ARE the
 * robot base and the asset, so xrCalibration.ts has to agree with the
 * renderer about where those are, exactly. Two copies of a robot-base
 * position that drift apart would mean the human calibrates against one
 * layout and the robot is retargeted against another -- an error that looks
 * like bad hand tracking rather than like a constant.
 *
 * Milestone 1 has no scene manifest / room reconstruction (spec sections
 * 40-41: fixture scene only), so these are fixed rather than loaded.
 */

import type { Quat, Vec3 } from "./contracts";

export const ROBOT_BASE_STRUCT: Vec3 = [0.0, 0.0, 0.0];

// Recentered for the real SO-101 (Track A) -- it's a small desktop arm,
// maybe 30-40cm of usable reach, nothing like the earlier placeholder's
// ~1m chain. These aren't guessed: verified directly against the real
// IkSolver (uv run python3 against ar_datapipe.retarget.IkSolver), each
// point is the literal FK output of a real, within-joint-limits
// configuration, so IK is guaranteed to converge back to it. A 5-DOF arm
// (this one) also can't reach an arbitrary (position, orientation) pair
// independently the way a 6-DOF wrist can -- the orientation below is the
// one that FK actually produced at these positions, not an arbitrary
// identity quaternion (which was empirically NOT achievable in-limits
// near this workspace).
export const ASSET_WORLD_POSITION: Vec3 = [0.35, -0.05, 0.14];

// The button itself sits normally on the table -- identity orientation.
// (The wrist/gripper's *target* orientation for reaching it is a separate
// concern, handled in tools/make_mock_hand_episode.py's NATURAL_EE_ORIENTATION
// -- not the asset's own world rotation.)
export const ASSET_WORLD_POSE: { position_m: Vec3; orientation_xyzw: Quat } = {
  position_m: ASSET_WORLD_POSITION,
  orientation_xyzw: [0, 0, 0, 1],
};

// tools/make_mock_hand_episode.py's RETRACT_END_M -- where the mock demo's
// wrist genuinely ends up, so the default goal exercises real accept logic.
export const DEFAULT_GOAL_M: Vec3 = [0.37, 0.07, 0.2];
