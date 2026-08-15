/**
 * The red/blue ball-sorting task: state, predicate, scoring, events.
 *
 * Deliberately free of three.js and of WebXR. What counts as sorted is a
 * geometric fact about where a ball is, and the spec is explicit that no
 * LLM judgement decides task success -- so the predicate lives here, in
 * plain arithmetic that a test can pin down, and the renderer and the
 * headset code read it rather than reimplementing it.
 *
 * The physics is intentionally small: a ball falls, lands on the table or a
 * basket floor, and stops. That is everything the interaction needs -- pick
 * up, carry, drop in -- and every bit of it is deterministic, so a recorded
 * episode replays to the same resting places. Authoritative contact and
 * physics remain the simulator's job at verification time (spec section 16);
 * this is the human-side interaction layer, not a second physics engine.
 */

import {
  BALLS,
  BALL_RADIUS_M,
  BASKETS,
  BASKET_INTERIOR_M,
  BASKET_WALL_HEIGHT_M,
  TABLE_Z_M,
  basketFor,
  type BallColor,
  type BasketSpec,
} from "./sortLayout";
import type { Vec3 } from "./contracts";

export type SortEventType =
  | "grasp_start"
  | "grasp_end"
  | "ball_enter_basket"
  | "wrong_basket"
  | "sort_complete"
  | "tracking_lost";

export interface SortEvent {
  type: SortEventType;
  objectId?: string;
  containerId?: string;
}

export interface SortObjectState {
  id: string;
  position_m: Vec3;
}

/** Gravity, m/s^2. Real gravity because the balls are real-sized and a
 * human's sense of a dropped object is calibrated to it. */
const GRAVITY_M_S2 = 9.81;
/** Speed below which a resting ball is pinned, so it does not jitter on the
 * table forever accumulating float noise. */
const REST_SPEED_M_S = 0.02;

/**
 * Is this ball's center inside this basket's interior volume?
 *
 * A ball carried *over* a basket must not count -- otherwise the sort would
 * score on the way past. So the test is a real volume: the interior
 * footprint, from the basket floor up to the wall rim, not a footprint plus
 * "anywhere above".
 */
export function insideBasket(position: Vec3, basket: BasketSpec): boolean {
  const half = BASKET_INTERIOR_M / 2;
  const [x, y, z] = position;
  const [cx, cy, cz] = basket.center;
  if (Math.abs(x - cx) > half || Math.abs(y - cy) > half) return false;
  return z >= cz && z <= cz + BASKET_WALL_HEIGHT_M;
}

/** Which basket, if any, contains this point. */
export function containingBasket(position: Vec3): BasketSpec | null {
  return BASKETS.find((basket) => insideBasket(position, basket)) ?? null;
}

interface BallState {
  position: Vec3;
  velocityZ: number;
  held: boolean;
  container: string | null;
}

export class SortTask {
  private readonly balls = new Map<string, BallState>();
  readonly events: SortEvent[] = [];
  private completeAnnounced = false;

  constructor() {
    this.reset();
  }

  reset(): void {
    this.balls.clear();
    this.events.length = 0;
    this.completeAnnounced = false;
    for (const ball of BALLS) {
      this.balls.set(ball.id, {
        position: [...ball.start] as Vec3,
        velocityZ: 0,
        held: false,
        container: null,
      });
    }
  }

  ballPosition(id: string): Vec3 {
    return [...this.ball(id).position] as Vec3;
  }

  containerOf(id: string): string | null {
    return this.ball(id).container;
  }

  isHeld(id: string): boolean {
    return this.ball(id).held;
  }

  /** Move a ball, e.g. because a hand is carrying it. Clears its velocity:
   * a carried ball is not falling. */
  setBallPosition(id: string, position: Vec3): void {
    const ball = this.ball(id);
    ball.position = [...position] as Vec3;
    ball.velocityZ = 0;
  }

  /** Take or release ownership of a ball. Edge-triggered event-wise, so a
   * grasp held across frames does not emit a grasp_start per frame. */
  setHeld(id: string, held: boolean): void {
    const ball = this.ball(id);
    if (ball.held === held) return;
    ball.held = held;
    this.events.push({ type: held ? "grasp_start" : "grasp_end", objectId: id });
    if (held) {
      // Picking a ball out of a basket takes the point back with it, which
      // `settle` records on the next tick.
      ball.velocityZ = 0;
    }
  }

  /** Note that hand tracking dropped, so the episode carries the reason a
   * demonstration has a gap in it. */
  markTrackingLost(): void {
    this.events.push({ type: "tracking_lost" });
  }

  /** Advance the free-falling balls by dt seconds, then re-evaluate scoring. */
  step(dtSeconds: number): void {
    for (const [, ball] of this.balls) {
      if (ball.held) continue;
      ball.velocityZ -= GRAVITY_M_S2 * dtSeconds;
      const nextZ = ball.position[2] + ball.velocityZ * dtSeconds;
      const floorZ = this.floorUnder(ball.position) + BALL_RADIUS_M;
      if (nextZ <= floorZ) {
        ball.position = [ball.position[0], ball.position[1], floorZ];
        ball.velocityZ = 0;
      } else {
        ball.position = [ball.position[0], ball.position[1], nextZ];
        if (Math.abs(ball.velocityZ) < REST_SPEED_M_S && nextZ - floorZ < 1e-4) {
          ball.velocityZ = 0;
        }
      }
    }
    this.settle();
  }

  /** Re-evaluate containment and emit whatever changed. Separated from
   * `step` so a test (and the recorder) can score a placed ball without
   * simulating a fall. */
  settle(): void {
    for (const [id, ball] of this.balls) {
      // A ball in your hand has not been placed anywhere, even when your
      // hand is inside a basket. Without this, carrying a ball *through*
      // the basket volume on the way to somewhere else scores it -- and
      // then scores it again when it finally lands, which is exactly how
      // `sort_complete` came to fire twice.
      const basket = ball.held ? null : containingBasket(ball.position);
      const containerId = basket?.id ?? null;
      if (containerId === ball.container) continue;

      ball.container = containerId;
      if (!basket) continue;

      const spec = BALLS.find((b) => b.id === id)!;
      this.events.push({
        type: basket.color === spec.color ? "ball_enter_basket" : "wrong_basket",
        objectId: id,
        containerId: basket.id,
      });
    }

    if (this.isComplete && !this.completeAnnounced) {
      this.completeAnnounced = true;
      this.events.push({ type: "sort_complete" });
    } else if (!this.isComplete) {
      this.completeAnnounced = false;
    }
  }

  /** How many balls of this color are in their matching basket. */
  score(color: BallColor): number {
    const basketId = basketFor(color).id;
    let count = 0;
    for (const ball of BALLS) {
      if (ball.color !== color) continue;
      if (this.ball(ball.id).container === basketId) count += 1;
    }
    return count;
  }

  /** Spec section 3's success predicate: every ball in its matching basket. */
  get isComplete(): boolean {
    return BALLS.every((ball) => this.ball(ball.id).container === basketFor(ball.color).id);
  }

  /** Ball and basket poses for the episode record. Baskets are included
   * because a demonstration is only reusable if the thing the ball was put
   * *into* is part of the record too (spec section 19). */
  objectStates(): SortObjectState[] {
    const states: SortObjectState[] = [];
    for (const ball of BALLS) states.push({ id: ball.id, position_m: this.ballPosition(ball.id) });
    for (const basket of BASKETS) {
      states.push({ id: basket.id, position_m: [...basket.center] as Vec3 });
    }
    return states;
  }

  private floorUnder(position: Vec3): number {
    const basket = BASKETS.find((b) => {
      const half = BASKET_INTERIOR_M / 2;
      return (
        Math.abs(position[0] - b.center[0]) <= half && Math.abs(position[1] - b.center[1]) <= half
      );
    });
    return basket ? basket.center[2] : TABLE_Z_M;
  }

  private ball(id: string): BallState {
    const ball = this.balls.get(id);
    if (!ball) throw new Error(`unknown ball ${id}`);
    return ball;
  }
}
