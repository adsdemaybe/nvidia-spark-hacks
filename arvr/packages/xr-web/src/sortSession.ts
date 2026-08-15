/**
 * One frame of the ball-sorting interaction, from tracked hand to task state.
 *
 * This is the piece the entry point would otherwise inline and nobody could
 * test: pinch -> grasp -> carry -> drop -> score. Keeping it here means the
 * spec's own acceptance scenario ("grab red_ball_0, move it, release over
 * red_basket, RED reads 1/3") runs headlessly in vitest, with no headset, no
 * GPU and no backend -- and the entry point becomes a renderer plus a
 * recorder rather than a place where the rules live.
 *
 * Takes an already-struct_world hand frame. The headset's raw coordinates
 * are relative to wherever it booted, so everything downstream of
 * xrCalibration works in struct_world and nothing here re-derives a frame.
 */

import { GraspController, GRASP_RADIUS_M, type GraspCandidate } from "./grasp";
import { PinchLatch } from "./hands";
import { BALLS } from "./sortLayout";
import { SortTask, type SortEvent } from "./sortTask";
import { teleopTargetFromWireFrame, type RobotTeleopTarget } from "./teleopTarget";
import type { WireHandFrame } from "./liveRetargetSession";
import type { Vec3 } from "./contracts";

export interface SortSessionInput {
  /** struct_world hand frame, or null when tracking is lost. */
  frame: WireHandFrame | null;
  /** 0 = open, 1 = closed (hands.ts's `gripperFromAperture`). */
  gripper: number;
  dtSeconds: number;
}

export interface SortSessionUpdate {
  heldId: string | null;
  /** What to highlight: the held ball, or the one a pinch would catch. */
  highlightId: string | null;
  pinchActive: boolean;
  teleopTarget: RobotTeleopTarget | null;
  /** Events produced by *this* frame only, ready to append to an episode. */
  newEvents: SortEvent[];
}

export class SortSession {
  readonly task = new SortTask();
  private readonly grasp = new GraspController();
  private readonly pinch = new PinchLatch();
  private eventsSeen = 0;
  private handWasTracked = true;

  reset(): void {
    this.task.reset();
    this.grasp.clear();
    this.pinch.reset();
    this.eventsSeen = 0;
    this.handWasTracked = true;
  }

  update(input: SortSessionInput): SortSessionUpdate {
    const { frame, gripper, dtSeconds } = input;

    // Losing tracking is recorded once, on the transition. A demonstration
    // with a gap in it should say why it has one.
    if (!frame && this.handWasTracked) this.task.markTrackingLost();
    this.handWasTracked = frame !== null;

    this.pinch.update(frame ? { gripper } : null);
    const pinchCenter = frame ? structPinchPoint(frame) : null;

    const result = this.grasp.update({
      pinchActive: this.pinch.isEngaged,
      pinchCenter,
      handOrientation: frame?.joints["wrist"]?.orientation_xyzw ?? [0, 0, 0, 1],
      balls: this.candidates(),
    });

    if (result.grasped) this.task.setHeld(result.grasped, true);
    if (result.released) this.task.setHeld(result.released, false);
    if (result.heldId && result.ballPosition) {
      this.task.setBallPosition(result.heldId, result.ballPosition);
    }

    this.task.step(dtSeconds);

    const newEvents = this.task.events.slice(this.eventsSeen);
    this.eventsSeen = this.task.events.length;

    return {
      heldId: result.heldId,
      highlightId: result.heldId ?? this.reachable(pinchCenter),
      pinchActive: this.pinch.isEngaged,
      teleopTarget: frame ? teleopTargetFromWireFrame(frame, gripper) : null,
      newEvents,
    };
  }

  /** Balls that can be picked up: everything not currently held. */
  private candidates(): GraspCandidate[] {
    return BALLS.filter((ball) => !this.task.isHeld(ball.id)).map((ball) => ({
      id: ball.id,
      position: this.task.ballPosition(ball.id),
    }));
  }

  /** Nearest catchable ball, for the pre-grasp highlight. */
  private reachable(pinchCenter: Vec3 | null): string | null {
    if (!pinchCenter) return null;
    let best: string | null = null;
    let bestDistance = GRASP_RADIUS_M;
    for (const candidate of this.candidates()) {
      const distance = Math.hypot(
        candidate.position[0] - pinchCenter[0],
        candidate.position[1] - pinchCenter[1],
        candidate.position[2] - pinchCenter[2],
      );
      if (distance <= bestDistance) {
        best = candidate.id;
        bestDistance = distance;
      }
    }
    return best;
  }
}

/** Pinch point from an already-converted struct_world frame: the midpoint of
 * the thumb and index tips, same definition hands.ts uses in WebXR space. */
export function structPinchPoint(frame: WireHandFrame): Vec3 | null {
  const thumb = frame.joints["thumb-tip"];
  const index = frame.joints["index-finger-tip"];
  if (!thumb || !index) return null;
  return [
    (thumb.position_m[0] + index.position_m[0]) / 2,
    (thumb.position_m[1] + index.position_m[1]) / 2,
    (thumb.position_m[2] + index.position_m[2]) / 2,
  ];
}
