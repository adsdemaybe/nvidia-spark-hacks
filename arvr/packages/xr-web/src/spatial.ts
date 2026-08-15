/**
 * The spatial math, mirrored from ar_contracts (Python) (STRUCT_2.md 13B, 22).
 *
 * Kept dependency-free and identical in behaviour to the Python side. If these
 * two ever disagree, "any spatial input -> the same robot pipeline" stops being
 * true, so spatial.test.ts checks them against the same fixture.
 */

import type { Pose, Quat, Vec3 } from "./contracts";
import { IDENTITY_QUATERNION } from "./contracts";

/** Matches ar_contracts.common.QUAT_NORM_TOLERANCE. */
export const QUAT_NORM_TOLERANCE = 1e-2;

/** Forward is +X in the canonical Z-up right-handed frame. */
export const FORWARD_AXIS: Vec3 = [1, 0, 0];

export function normalizeQuaternion(q: Quat): Quat {
  if (!q.every(Number.isFinite)) {
    throw new Error(`orientation_xyzw must be finite; got ${JSON.stringify(q)}`);
  }
  const norm = Math.hypot(...q);
  if (Math.abs(norm - 1) > QUAT_NORM_TOLERANCE) {
    throw new Error(
      `orientation_xyzw must be a unit quaternion; norm was ${norm.toFixed(6)}`,
    );
  }
  return [q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm];
}

/** Rotate v by q, using the cross-product form (no matrix, no allocation churn). */
export function rotateVector(q: Quat, v: Vec3): Vec3 {
  const [ux, uy, uz, w] = q;

  const tx = 2 * (uy * v[2] - uz * v[1]);
  const ty = 2 * (uz * v[0] - ux * v[2]);
  const tz = 2 * (ux * v[1] - uy * v[0]);

  return [
    v[0] + w * tx + (uy * tz - uz * ty),
    v[1] + w * ty + (uz * tx - ux * tz),
    v[2] + w * tz + (ux * ty - uy * tx),
  ];
}

/** The direction the human is facing, as a unit vector in struct_world. */
export function heading(pose: Pose): Vec3 {
  return rotateVector(pose.orientation_xyzw ?? IDENTITY_QUATERNION, FORWARD_AXIS);
}

/** Where the robot should stand: behind the human, along their heading. */
export function followTarget(human: Pose, desiredFollowDistanceM: number): Vec3 {
  if (!(desiredFollowDistanceM > 0)) {
    throw new Error(
      `follow distance must be positive; got ${desiredFollowDistanceM}. ` +
        "A non-positive distance puts the robot in front of the human.",
    );
  }
  const fwd = heading(human);
  return [
    human.position_m[0] - fwd[0] * desiredFollowDistanceM,
    human.position_m[1] - fwd[1] * desiredFollowDistanceM,
    human.position_m[2] - fwd[2] * desiredFollowDistanceM,
  ];
}
