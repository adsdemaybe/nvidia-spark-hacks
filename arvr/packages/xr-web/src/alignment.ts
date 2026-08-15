/**
 * Twin Alignment v0 — spec section 49.
 *
 * "Use deterministic manual anchors first... Do not delay the demo for
 * automatic relocalization." Two anchors (robot base, table corner): the
 * user taps where each one actually is in the room, and this solves the
 * rigid transform T_struct_to_ar that makes every subsequent twin pose
 * line up with the real room instead of floating at wherever the AR
 * session's own tracking origin happens to be.
 *
 * A single Z-rotation + translation, no scale correction — struct_world
 * and the AR session are both already metric (meters), so there's nothing
 * to scale. This is deliberately the simplest thing that can be "computed
 * from two anchors": anchor A fixes the translation exactly; the vector
 * from A to B fixes the rotation. B's own reprojection error is therefore
 * a real, meaningful signal — spec's "anchor reprojection error < 5cm"
 * target — because nothing about how B is used forces it to reproject
 * perfectly (no scale correction to hide a mismatch, unlike a 2-point
 * similarity transform would).
 */

import type { Quat, Vec3 } from "./contracts";

export interface Anchor {
  /** Where this point actually is, in struct_world (the twin's own frame). */
  structPosition: Vec3;
  /** Where the user tapped for it, in the scene's render frame. */
  tappedPosition: Vec3;
}

export interface Alignment {
  yaw: number;
  translation: Vec3;
}

export const IDENTITY_ALIGNMENT: Alignment = { yaw: 0, translation: [0, 0, 0] };

export function solveAlignment(anchorA: Anchor, anchorB: Anchor): Alignment {
  const structDx = anchorB.structPosition[0] - anchorA.structPosition[0];
  const structDy = anchorB.structPosition[1] - anchorA.structPosition[1];
  const tappedDx = anchorB.tappedPosition[0] - anchorA.tappedPosition[0];
  const tappedDy = anchorB.tappedPosition[1] - anchorA.tappedPosition[1];

  const yaw = Math.atan2(tappedDy, tappedDx) - Math.atan2(structDy, structDx);

  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  const rotatedAx = cos * anchorA.structPosition[0] - sin * anchorA.structPosition[1];
  const rotatedAy = sin * anchorA.structPosition[0] + cos * anchorA.structPosition[1];

  const translation: Vec3 = [
    anchorA.tappedPosition[0] - rotatedAx,
    anchorA.tappedPosition[1] - rotatedAy,
    anchorA.tappedPosition[2] - anchorA.structPosition[2],
  ];

  return { yaw, translation };
}

export function applyAlignment(alignment: Alignment, point: Vec3): Vec3 {
  const cos = Math.cos(alignment.yaw);
  const sin = Math.sin(alignment.yaw);
  return [
    cos * point[0] - sin * point[1] + alignment.translation[0],
    sin * point[0] + cos * point[1] + alignment.translation[1],
    point[2] + alignment.translation[2],
  ];
}

/**
 * The transform that undoes {@link applyAlignment}.
 *
 * `applyAlignment` answers "where in the room does this twin pose belong?".
 * Its inverse answers the question the openxr hand path actually asks:
 * "the human's hand is *here* in the room — where is that in struct_world?".
 * Both directions are needed at once (the scene is placed with one, every
 * tracked hand frame is mapped with the other), so the inverse is a named,
 * tested operation rather than an ad-hoc negation at each call site.
 */
export function invertAlignment(alignment: Alignment): Alignment {
  const cos = Math.cos(alignment.yaw);
  const sin = Math.sin(alignment.yaw);
  const [tx, ty, tz] = alignment.translation;
  // R(-yaw) applied to -t: rotating the negated offset back through the
  // inverse rotation is what makes apply(inv, apply(a, p)) === p.
  return {
    yaw: -alignment.yaw,
    translation: [-(cos * tx + sin * ty), -(-sin * tx + cos * ty), -tz],
  };
}

/**
 * The same yaw, as a quaternion about struct +Z.
 *
 * `applyAlignment` rotates positions; a tracked hand joint also carries an
 * orientation, and rotating the position while leaving the orientation in the
 * room's frame would hand the retargeter a pose whose two halves disagree
 * about which way is forward.
 */
export function yawQuaternion(angle: number): Quat {
  const half = angle / 2;
  const s = Math.sin(half);
  return [0, 0, s === 0 ? 0 : s, Math.cos(half)];
}

/** Hamilton product, xyzw order: the rotation `b` followed by `a`. */
export function composeQuaternions(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

/** How far the reprojected anchor lands from where it was actually tapped.
 * Spec's "anchor reprojection error < 5cm" target. */
export function reprojectionErrorM(alignment: Alignment, anchor: Anchor): number {
  const reprojected = applyAlignment(alignment, anchor.structPosition);
  return Math.hypot(
    reprojected[0] - anchor.tappedPosition[0],
    reprojected[1] - anchor.tappedPosition[1],
    reprojected[2] - anchor.tappedPosition[2],
  );
}
