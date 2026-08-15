/**
 * The can-pickup scene: a table, one can, and the robot arm.
 *
 * This is the task the project actually records: a human picks up a can with
 * their own hand, and that demonstration becomes reinforcement-learning data.
 * Everything in this scene is either the thing being manipulated, the surface
 * it sits on, or the robot the data is ultimately for. Nothing else is here,
 * deliberately -- every extra object is something a policy has to learn to
 * ignore, and something a demonstrator can accidentally interact with.
 *
 * Frame: right-handed, Z-up, meters. Robot base at the origin, +X reaching
 * away from it, +Z up.
 *
 * The can sits inside the SO-101's measured reachable envelope
 * (`tools/so101_reach_envelope.py`: everything from x = 0.14..0.38 with
 * |y| <= 0.24 converges at table height). That matters even though the human
 * does the picking: the recorded demonstration is only useful if the robot
 * this data trains could physically perform it.
 */

import type { Vec3 } from "./contracts";

/**
 * Tabletop surface height: zero, i.e. the table top IS the struct origin
 * plane.
 *
 * The whole pipeline treats struct_world's origin as the robot's base, and
 * the SO-101 is a desk arm bolted to the surface it works on. Putting the
 * tabletop anywhere else puts the arm's base below it, and the arm then
 * renders growing up through the table -- which is what the first version of
 * this scene did. Everything on the table therefore sits at small positive Z,
 * and the table body hangs below zero.
 */
export const TABLE_Z_M = 0;

/** How far the table body drops below its surface, to a floor nobody stands
 * on. Visual only. */
export const TABLE_DROP_M = 0.72;

/** Table footprint. Sized to the arm's reachable band rather than to a real
 * piece of furniture, so nothing on it is out of reach. */
export const TABLE_X_M: [number, number] = [-0.12, 0.46];
export const TABLE_Y_M: [number, number] = [-0.30, 0.30];
export const TABLE_THICKNESS_M = 0.02;

/**
 * A standard 355ml drinks can: 66mm across, 123mm tall.
 *
 * Real dimensions rather than a convenient round number, because the grasp
 * aperture a human uses is set by the actual object -- a can 2cm wider would
 * be a different grasp, and the recorded finger poses would teach a different
 * one.
 */
export const CAN_RADIUS_M = 0.033;
export const CAN_HEIGHT_M = 0.123;

export const CAN_ID = "soda_can_01";

/** Where the can starts, centred in front of the arm and well inside reach.
 * The position is the can's centre, so it rests with its base on the table. */
export const CAN_START: Vec3 = [0.26, 0.0, TABLE_Z_M + CAN_HEIGHT_M / 2];

/** Robot base, at the struct origin -- the convention the whole pipeline
 * shares, and what the retargeter solves against. */
export const ROBOT_BASE: Vec3 = [0, 0, 0];

/**
 * How far the can must be lifted clear of the table to count as picked up.
 *
 * 8cm is unambiguous: well past the noise in either hand tracking source, and
 * clearly a deliberate lift rather than the can being nudged or tipped. The
 * predicate is deterministic and the number is stated rather than tuned by
 * eye, because "did the demonstration succeed" decides what enters the
 * training set.
 */
export const LIFT_CLEARANCE_M = 0.08;

/** Table height plus the clearance: the absolute Z the can's base must pass. */
export const LIFT_THRESHOLD_Z_M = TABLE_Z_M + LIFT_CLEARANCE_M;

export const CAN_PICKUP_TASK_ID = "pick_up_can";

/**
 * A resting pose for the arm, in the URDF's joint order:
 * shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper.
 *
 * The all-zero configuration has the arm sticking straight out across the
 * table, which reads as a fault rather than a robot at rest. Nothing drives
 * these joints in this app -- there is no robot to retarget onto yet -- so a
 * static, plausible pose is the honest thing to render: present for scale and
 * context, visibly not doing anything.
 */
export const ARM_REST_POSE: readonly number[] = [0, -1.15, 1.35, 0.6, 0, 0.3];
