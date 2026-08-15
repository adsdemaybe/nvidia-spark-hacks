/**
 * Human-side grasp: which ball a pinching hand has hold of, and where that
 * ball should be while it is held.
 *
 * This is the *human's* grasp, kept strictly separate from the robot's
 * (spec section 16). A human pinch picks a ball up immediately, because the
 * demonstration is about what the person meant to do. Whether the robot's
 * gripper could actually have closed on that ball at that pose is a physics
 * question, and only simulation verification answers it -- nothing here
 * claims the robot grasp succeeded.
 *
 * Pure geometry, no three.js: the grasp rule is a distance test and the
 * carry rule is a rigid transform, both of which a test can pin down
 * exactly. The renderer reads the result.
 */

import { rotateVector } from "./spatial";
import type { Quat, Vec3 } from "./contracts";

/**
 * How close the pinch point has to be to a ball's center to catch it.
 *
 * Slightly larger than the ball's own radius (0.03): hand tracking is
 * accurate to roughly a centimeter, and requiring the pinch point to land
 * inside the sphere makes a ball that is visually between your fingers
 * refuse to be picked up.
 */
export const GRASP_RADIUS_M = 0.05;

export interface GraspCandidate {
  id: string;
  position: Vec3;
}

export interface GraspInput {
  /** Hysteretic pinch state -- see hands.ts's PinchLatch. */
  pinchActive: boolean;
  /** Midpoint of thumb and index tips, or null when the hand is untracked. */
  pinchCenter: Vec3 | null;
  handOrientation: Quat;
  balls: GraspCandidate[];
}

export interface GraspUpdate {
  heldId: string | null;
  /** Where the held ball should now be. Null when nothing is held. */
  ballPosition: Vec3 | null;
  /** Set only on the frame the grasp starts. */
  grasped?: string;
  /** Set only on the frame the grasp ends. */
  released?: string;
}

export class GraspController {
  private heldId: string | null = null;
  /** The grab offset, expressed in the hand's frame so that turning the
   * wrist swings the ball around the pinch instead of leaving it behind. */
  private offsetInHand: Vec3 = [0, 0, 0];

  /**
   * How far the catch reaches.
   *
   * Defaults to the desk task's fingertip-pinch radius. The room-scale ball
   * pit passes its own much larger value, because a 9cm ball is caught with a
   * whole hand rather than pinched between two fingertips -- and a caller
   * that highlights reachable balls using one radius while the controller
   * catches with another produces objects that light up and then refuse to be
   * picked up.
   */
  constructor(private readonly graspRadiusM: number = GRASP_RADIUS_M) {}

  get held(): string | null {
    return this.heldId;
  }

  update(input: GraspInput): GraspUpdate {
    const { pinchActive, pinchCenter, handOrientation, balls } = input;

    // A lost hand releases. Carrying a ball on the strength of a pinch that
    // is no longer being observed would leave it stuck to a hand that is
    // not there.
    if (!pinchCenter || !pinchActive) {
      if (this.heldId) {
        const released = this.heldId;
        this.heldId = null;
        return { heldId: null, ballPosition: null, released };
      }
      return { heldId: null, ballPosition: null };
    }

    if (this.heldId) {
      return { heldId: this.heldId, ballPosition: this.carriedPosition(pinchCenter, handOrientation) };
    }

    const caught = nearestWithin(pinchCenter, balls, this.graspRadiusM);
    if (!caught) return { heldId: null, ballPosition: null };

    this.heldId = caught.id;
    // Store the offset in the hand's frame: inverse-rotate the world offset.
    const worldOffset: Vec3 = [
      caught.position[0] - pinchCenter[0],
      caught.position[1] - pinchCenter[1],
      caught.position[2] - pinchCenter[2],
    ];
    this.offsetInHand = rotateVector(conjugate(handOrientation), worldOffset);

    return {
      heldId: caught.id,
      ballPosition: [...caught.position] as Vec3,
      grasped: caught.id,
    };
  }

  /** Drop whatever is held without reporting a release event -- for a reset,
   * where there is no demonstration moment to record. */
  clear(): void {
    this.heldId = null;
    this.offsetInHand = [0, 0, 0];
  }

  private carriedPosition(pinchCenter: Vec3, handOrientation: Quat): Vec3 {
    const offset = rotateVector(handOrientation, this.offsetInHand);
    return [
      pinchCenter[0] + offset[0],
      pinchCenter[1] + offset[1],
      pinchCenter[2] + offset[2],
    ];
  }
}

function nearestWithin(
  point: Vec3,
  balls: GraspCandidate[],
  radiusM: number,
): GraspCandidate | null {
  let best: GraspCandidate | null = null;
  let bestDistance = radiusM;
  for (const ball of balls) {
    const distance = Math.hypot(
      ball.position[0] - point[0],
      ball.position[1] - point[1],
      ball.position[2] - point[2],
    );
    if (distance <= bestDistance) {
      best = ball;
      bestDistance = distance;
    }
  }
  return best;
}

/** Inverse of a unit quaternion. */
function conjugate([x, y, z, w]: Quat): Quat {
  return [-x, -y, -z, w];
}
