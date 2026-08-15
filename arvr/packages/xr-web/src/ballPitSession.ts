/**
 * Two hands, many balls: one frame of the room-scale ball pit interaction.
 *
 * The desk task's `sortSession.ts` drives a single hand, because the robot it
 * feeds has one end-effector. Nothing here has an end-effector: both hands are
 * manipulators, independently, and either can be holding a different ball at
 * the same time. So this runs a `GraspController` per hand rather than
 * generalizing the single-hand one into something that pretends to be both.
 *
 * Kept free of three.js and WebXR types so the whole interaction -- grab with
 * either hand, carry, throw, score -- is testable without a headset.
 */

import { GraspController, type GraspCandidate } from "./grasp";
import { PinchLatch } from "./hands";
import { BALLS, BALL_RADIUS_M } from "./ballPitLayout";
import type { Vec3 } from "./contracts";

/**
 * How close a hand has to be to catch a ball.
 *
 * Much larger than the desk task's 5cm, and deliberately so: these balls are
 * 9cm in radius and are grabbed with a whole hand, not pinched between two
 * fingertips. The catch radius is the ball plus a hand's worth of slack, so
 * closing your fist anywhere around a ball takes it. Requiring fingertip
 * precision on an object this size feels broken.
 */
export const PIT_GRASP_RADIUS_M = BALL_RADIUS_M + 0.07;

/**
 * How many past samples the throw velocity is averaged over.
 *
 * One frame's delta is far too noisy -- hand tracking jitter alone would fling
 * balls across the room. Averaging over roughly the last 80ms of motion is
 * long enough to smooth the jitter and short enough to still capture the flick
 * at the end of a throw, which is the part a human actually aims with.
 */
const VELOCITY_SAMPLES = 6;
/** Nobody throws a ball at 20 m/s, but a tracking glitch can look like it. */
const MAX_THROW_SPEED_M_S = 8;

/** What the task layer must provide. Declared structurally rather than
 * importing the class, so this stays testable against a stub. */
export interface BallPitTaskLike {
  ballPosition(id: string): Vec3;
  isHeld(id: string): boolean;
  setBallPosition(id: string, position: Vec3): void;
  setHeld(id: string, held: boolean): void;
  releaseBall(id: string, velocity: Vec3): void;
  markTrackingLost?(): void;
}

/** One hand's worth of input, already in struct_world. */
export interface HandInput {
  /** Where the grab happens. Null when the hand is untracked. */
  position: Vec3 | null;
  orientation: [number, number, number, number];
  /** 0 = open, 1 = closed. */
  gripper: number;
}

export interface BallPitInput {
  left: HandInput | null;
  right: HandInput | null;
  dtSeconds: number;
}

export interface BallPitUpdate {
  /** Ball ids either hand is holding. */
  held: Set<string>;
  /** Ball ids either hand could grab right now, for highlighting. */
  reachable: Set<string>;
  grasped: string[];
  released: string[];
}

/** Tracks one hand: its grasp, its pinch hysteresis, and its recent motion. */
class HandTrack {
  readonly grasp = new GraspController(PIT_GRASP_RADIUS_M);
  readonly pinch = new PinchLatch();
  private samples: Array<{ position: Vec3; dt: number }> = [];

  /** Record where the hand is, for the throw velocity. */
  sample(position: Vec3 | null, dtSeconds: number): void {
    if (!position) {
      // A hand that vanished has no meaningful velocity, and reusing stale
      // samples across a tracking dropout would launch whatever it held.
      this.samples = [];
      return;
    }
    this.samples.push({ position: [...position] as Vec3, dt: dtSeconds });
    if (this.samples.length > VELOCITY_SAMPLES) this.samples.shift();
  }

  /** Average velocity over the recent samples, clamped to something a human
   * could actually produce. */
  velocity(): Vec3 {
    if (this.samples.length < 2) return [0, 0, 0];
    const first = this.samples[0]!;
    const last = this.samples[this.samples.length - 1]!;
    let elapsed = 0;
    for (let i = 1; i < this.samples.length; i += 1) elapsed += this.samples[i]!.dt;
    if (elapsed <= 0) return [0, 0, 0];

    const raw: Vec3 = [
      (last.position[0] - first.position[0]) / elapsed,
      (last.position[1] - first.position[1]) / elapsed,
      (last.position[2] - first.position[2]) / elapsed,
    ];
    const speed = Math.hypot(...raw);
    if (speed <= MAX_THROW_SPEED_M_S || speed === 0) return raw;
    const scale = MAX_THROW_SPEED_M_S / speed;
    return [raw[0] * scale, raw[1] * scale, raw[2] * scale];
  }

  reset(): void {
    this.grasp.clear();
    this.pinch.reset();
    this.samples = [];
  }
}

export class BallPitSession {
  private readonly hands = { left: new HandTrack(), right: new HandTrack() };
  private bothHandsWereTracked = true;

  constructor(private readonly task: BallPitTaskLike) {}

  reset(): void {
    this.hands.left.reset();
    this.hands.right.reset();
    this.bothHandsWereTracked = true;
  }

  update(input: BallPitInput): BallPitUpdate {
    const held = new Set<string>();
    const reachable = new Set<string>();
    const grasped: string[] = [];
    const released: string[] = [];

    const anyTracked = input.left?.position != null || input.right?.position != null;
    if (!anyTracked && this.bothHandsWereTracked) this.task.markTrackingLost?.();
    this.bothHandsWereTracked = anyTracked;

    for (const side of ["left", "right"] as const) {
      const hand = input[side];
      const track = this.hands[side];
      const position = hand?.position ?? null;

      track.sample(position, input.dtSeconds);
      track.pinch.update(hand ? { gripper: hand.gripper } : null);

      // A ball already held by the *other* hand must not be a candidate, or
      // both hands end up owning one ball and fighting over its position.
      const candidates = this.candidates(held);

      const result = track.grasp.update({
        pinchActive: track.pinch.isEngaged,
        pinchCenter: position,
        handOrientation: hand?.orientation ?? [0, 0, 0, 1],
        balls: candidates,
      });

      if (result.grasped) {
        this.task.setHeld(result.grasped, true);
        grasped.push(result.grasped);
      }
      if (result.released) {
        // Release with the hand's own velocity: that is what turns "let go"
        // into "throw", and it is the whole reason the samples exist.
        this.task.setHeld(result.released, false);
        this.task.releaseBall(result.released, track.velocity());
        released.push(result.released);
      }
      if (result.heldId && result.ballPosition) {
        this.task.setBallPosition(result.heldId, result.ballPosition);
        held.add(result.heldId);
      }

      if (position) {
        for (const candidate of candidates) {
          if (within(candidate.position, position, PIT_GRASP_RADIUS_M)) {
            reachable.add(candidate.id);
          }
        }
      }
    }

    for (const id of held) reachable.add(id);
    return { held, reachable, grasped, released };
  }

  /** Balls available to grab: everything not currently held by anyone. */
  private candidates(claimedThisFrame: ReadonlySet<string>): GraspCandidate[] {
    const out: GraspCandidate[] = [];
    for (const ball of BALLS) {
      if (claimedThisFrame.has(ball.id)) continue;
      if (this.task.isHeld(ball.id)) continue;
      out.push({ id: ball.id, position: this.task.ballPosition(ball.id) });
    }
    return out;
  }
}

function within(a: Vec3, b: Vec3, radius: number): boolean {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) <= radius;
}
