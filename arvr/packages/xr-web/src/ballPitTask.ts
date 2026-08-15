/**
 * The room-scale ball pit: state, containment predicate, scoring, events.
 *
 * The desk task's sibling (`sortTask.ts`) and deliberately the same shape --
 * same event vocabulary, same "a held ball is nowhere" rule, same
 * three.js-free, WebXR-free arithmetic that a test can pin down -- because a
 * shared session layer drives both and the recorder that consumes the events
 * must not care which scene produced them.
 *
 * What is different is the physics. On the desk a ball falls, lands, and
 * stops, and that is honestly everything a careful pinch-and-place needs. A
 * ball pit is not careful: you grab a handful, you throw one across the room,
 * balls knock into each other and roll. A pit where a thrown ball flies in a
 * dead straight line and lands like a beanbag reads as broken in a way the
 * desk scene never did. So this one integrates gravity properly, bounces with
 * energy loss, has ground drag, and resolves ball-to-ball contacts.
 *
 * It is still not a physics engine and is not trying to be one. No rotation,
 * no spin, no friction torque, no continuous collision detection -- spheres,
 * impulses along contact normals, and a fixed substep. Authoritative contact
 * remains the simulator's job at verification time (spec section 16); this is
 * the human-side interaction layer, and its one hard requirement is that it
 * be deterministic so a recorded episode replays to the same resting places.
 */

import {
  BALLS,
  BALL_RADIUS_M,
  BINS,
  BIN_HEIGHT_M,
  BIN_INTERIOR_M,
  BIN_WALL_THICKNESS_M,
  FLOOR_Z_M,
  binFor,
  type BallColor,
  type BinSpec,
} from "./ballPitLayout";
import type { SortEvent, SortEventType, SortObjectState } from "./sortTask";
import type { Vec3 } from "./contracts";

/**
 * The pit speaks the desk task's event language, literally: these are type
 * aliases, not parallel definitions.
 *
 * The recorder already consumes `SortEvent`, and a second structurally
 * identical interface would compile fine today and drift the first time
 * somebody adds a field to one of them. Aliasing makes that drift impossible
 * rather than merely unlikely. The names still say "basket" where the pit
 * says "bin"; renaming them would break the recorder for a cosmetic gain,
 * which is exactly the trade the spec's frozen-contracts rule refuses.
 */
export type BallPitEventType = SortEventType;
export type BallPitEvent = SortEvent;
export type BallPitObjectState = SortObjectState;

/** Gravity, m/s^2. Real gravity, because at room scale a human watching a
 * ball arc across the floor will notice immediately if it is not. */
const GRAVITY_M_S2 = 9.81;

/**
 * Fraction of impact speed a ball keeps when it hits the ground.
 *
 * A perfectly elastic floor never settles and a perfectly dead one reads as a
 * beanbag; both are wrong for a hollow plastic ball. 0.4 puts a ball dropped
 * from waist height (~1 m) through a 15 cm bounce, then a 2 cm one, then to
 * rest -- "bounces once or twice and settles", which is what the eye expects.
 */
const GROUND_RESTITUTION = 0.4;

/** Bins are stiff-walled plastic and much deader than the floor: a ball
 * thrown at a bin wall should drop into the bin, not ricochet back out. */
const BIN_RESTITUTION = 0.3;

/** Ball-on-ball is livelier than ball-on-floor -- two light hollow spheres
 * lose less to deformation than one does against a hard floor -- but stays
 * below 1 so that a pit full of contacts is strictly dissipative. */
const BALL_RESTITUTION = 0.5;

/**
 * Bounce speed below which the ball is simply parked on the surface.
 *
 * Without it the geometric series of bounces never terminates: the hops get
 * arbitrarily small but the ball keeps twitching, `objectStates` keeps
 * changing, and nothing ever comes to rest for a determinism check to compare.
 * 0.35 m/s is the speed of a 6 mm fall, so what is being discarded is a hop
 * too small to see.
 */
const BOUNCE_REST_SPEED_M_S = 0.35;

/** Horizontal speed below which a ball in ground contact is stopped dead, for
 * the same reason: a millimetre-per-second creep is not motion, it is float
 * noise that keeps resting positions from ever being equal. */
const ROLL_REST_SPEED_M_S = 0.05;

/** Fraction of horizontal speed shed per second of ground contact. Balls do
 * not roll frictionlessly forever on carpet, and a thrown ball that never
 * stops eventually wanders out of the room. */
const GROUND_DRAG_PER_S = 2.5;

/**
 * Longest substep the integrator will take.
 *
 * Nothing here does continuous collision detection, so the guard against a
 * fast ball tunnelling through a bin wall is simply that it never travels far
 * in one step. At 1/120 s a 10 m/s throw -- about as hard as a person throws
 * underarm -- advances 8 cm, comfortably less than the 18 cm ball diameter
 * and the wall's collision reach. It also makes the pit behave the same on a
 * 72 Hz headset and on a stuttering 20 fps browser tab, since a long frame is
 * split rather than taken whole.
 */
const MAX_SUBSTEP_S = 1 / 120;

/**
 * Is this ball's center inside this bin's interior volume?
 *
 * Same reasoning as `insideBasket`, and the same reason it must be a volume
 * rather than a footprint: a ball carried *over* a bin, or thrown across the
 * room on an arc that passes above one, must not count. So the test runs from
 * the bin floor up to the wall rim and stops there -- not "footprint plus
 * anywhere above".
 */
export function insideBin(position: Vec3, bin: BinSpec): boolean {
  const half = BIN_INTERIOR_M / 2;
  const [x, y, z] = position;
  const [cx, cy, cz] = bin.center;
  if (Math.abs(x - cx) > half || Math.abs(y - cy) > half) return false;
  return z >= cz && z <= cz + BIN_HEIGHT_M;
}

/** Which bin, if any, contains this point. */
export function containingBin(position: Vec3): BinSpec | null {
  return BINS.find((bin) => insideBin(position, bin)) ?? null;
}

interface BallState {
  position: Vec3;
  /** Full 3D this time, not just the vertical component the desk task got
   * away with: pit balls are thrown, and a throw is mostly horizontal. */
  velocity: Vec3;
  held: boolean;
  container: string | null;
}

export class BallPitTask {
  private readonly balls = new Map<string, BallState>();
  readonly events: BallPitEvent[] = [];
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
        velocity: [0, 0, 0],
        held: false,
        container: null,
      });
    }
  }

  ballPosition(id: string): Vec3 {
    return [...this.ball(id).position] as Vec3;
  }

  /** Exposed so a HUD can show a throw and a test can assert that the pit
   * actually came to rest rather than merely looking like it did. */
  ballVelocity(id: string): Vec3 {
    return [...this.ball(id).velocity] as Vec3;
  }

  containerOf(id: string): string | null {
    return this.ball(id).container;
  }

  isHeld(id: string): boolean {
    return this.ball(id).held;
  }

  /** Move a ball, e.g. because a hand is carrying it. Clears its velocity: a
   * carried ball is not falling, and whatever it was doing before the hand
   * caught it is not what it should resume on release. */
  setBallPosition(id: string, position: Vec3): void {
    const ball = this.ball(id);
    ball.position = [...position] as Vec3;
    ball.velocity = [0, 0, 0];
  }

  /** Take or release ownership of a ball. Edge-triggered event-wise, so a
   * grasp held across frames does not emit a grasp_start per frame. */
  setHeld(id: string, held: boolean): void {
    const ball = this.ball(id);
    if (ball.held === held) return;
    ball.held = held;
    this.events.push({ type: held ? "grasp_start" : "grasp_end", objectId: id });
    if (held) {
      // Picking a ball out of a bin takes the point back with it, which
      // `settle` records on the next tick.
      ball.velocity = [0, 0, 0];
    }
  }

  /**
   * Let go of a ball with a velocity, i.e. throw it.
   *
   * The whole point of a room-scale pit is that you can lob a ball into a bin
   * across the room; a release that always drops straight down makes the
   * scene a slow walking simulator. The caller supplies the velocity because
   * only the caller knows it -- differentiating the hand pose is the session
   * layer's job, and doing it in here would mean the task quietly depending
   * on how often it is stepped.
   *
   * Releasing a ball that was not held is not an error: it still gets the
   * velocity, which is how a test (or a reset button) launches a ball without
   * miming a grasp first. No grasp_end is emitted in that case, because none
   * started.
   */
  releaseBall(id: string, velocity: Vec3 = [0, 0, 0]): void {
    this.setHeld(id, false);
    this.ball(id).velocity = [...velocity] as Vec3;
  }

  /** Note that hand tracking dropped, so the episode carries the reason a
   * demonstration has a gap in it. */
  markTrackingLost(): void {
    this.events.push({ type: "tracking_lost" });
  }

  /** Advance the free balls by dt seconds, then re-evaluate scoring. */
  step(dtSeconds: number): void {
    if (dtSeconds > 0) {
      // Splitting the frame rather than clamping it: a 250 ms hitch must
      // still advance 250 ms of simulation, or the pit visibly lurches
      // backwards in time relative to the hand that is carrying a ball.
      const substeps = Math.max(1, Math.ceil(dtSeconds / MAX_SUBSTEP_S));
      const h = dtSeconds / substeps;
      for (let i = 0; i < substeps; i += 1) this.substep(h);
    }
    this.settle();
  }

  /** Re-evaluate containment and emit whatever changed. Separated from `step`
   * so a test (and the recorder) can score a placed ball without simulating
   * a fall. */
  settle(): void {
    for (const [id, ball] of this.balls) {
      // A ball in your hand has not been placed anywhere, even when your hand
      // is inside a bin. Without this, carrying a ball *through* the bin
      // volume on the way to somewhere else scores it -- and then scores it
      // again when it finally lands, which is exactly how `sort_complete`
      // came to fire twice on the desk task. The bug is not scene-specific
      // and neither is the fix.
      const bin = ball.held ? null : containingBin(ball.position);
      const containerId = bin?.id ?? null;
      if (containerId === ball.container) continue;

      ball.container = containerId;
      if (!bin) continue;

      const spec = BALLS.find((b) => b.id === id)!;
      this.events.push({
        type: bin.color === spec.color ? "ball_enter_basket" : "wrong_basket",
        objectId: id,
        containerId: bin.id,
      });
    }

    if (this.isComplete && !this.completeAnnounced) {
      this.completeAnnounced = true;
      this.events.push({ type: "sort_complete" });
    } else if (!this.isComplete) {
      this.completeAnnounced = false;
    }
  }

  /** How many balls of this color are in their matching bin. */
  score(color: BallColor): number {
    const binId = binFor(color).id;
    let count = 0;
    for (const ball of BALLS) {
      if (ball.color !== color) continue;
      if (this.ball(ball.id).container === binId) count += 1;
    }
    return count;
  }

  /** Every ball in its matching bin. */
  get isComplete(): boolean {
    return BALLS.every((ball) => this.ball(ball.id).container === binFor(ball.color).id);
  }

  /** Ball and bin poses for the episode record. Bins are included for the
   * same reason baskets are on the desk: a demonstration is only reusable if
   * the thing the ball went *into* is part of the record too. */
  objectStates(): BallPitObjectState[] {
    const states: BallPitObjectState[] = [];
    for (const ball of BALLS) states.push({ id: ball.id, position_m: this.ballPosition(ball.id) });
    for (const bin of BINS) states.push({ id: bin.id, position_m: [...bin.center] as Vec3 });
    return states;
  }

  /**
   * One fixed-size physics tick.
   *
   * The order is deliberate. Integrate, then resolve ball-on-ball, then bin
   * walls, then the ground -- constraints run from softest to hardest so the
   * hardest gets the last word. Resolving the ground first would let a ball
   * landing under a falling neighbour be shoved back through the floor and
   * left there; resolving it last means a ball can end a tick overlapping
   * another ball by a millimetre, which nobody will ever see, but it can
   * never end one below the floor, which everybody would.
   */
  private substep(dtSeconds: number): void {
    for (const ball of this.balls.values()) {
      if (ball.held) continue;
      ball.velocity[2] -= GRAVITY_M_S2 * dtSeconds;
      ball.position[0] += ball.velocity[0] * dtSeconds;
      ball.position[1] += ball.velocity[1] * dtSeconds;
      ball.position[2] += ball.velocity[2] * dtSeconds;
    }

    this.resolveBallContacts();

    for (const ball of this.balls.values()) {
      // Held balls are the hand's to place. Constraining them would mean the
      // sim fighting hand tracking for control of the same position, and the
      // sim always loses that fight in a way that looks like jitter.
      if (ball.held) continue;
      this.resolveBinWalls(ball);
      this.resolveGround(ball, dtSeconds);
    }
  }

  /**
   * Equal-mass sphere contacts, every pair, every substep.
   *
   * This is a naive O(n^2) sweep: 24 balls is 276 pairs, and at two substeps
   * per 72 Hz frame that is about 40k distance checks per second, which is
   * noise next to a single frame of rendering. A broadphase -- uniform grid,
   * sweep-and-prune -- would cost more in code and in bucket bookkeeping than
   * it could possibly save at this size, and would add a data structure whose
   * iteration order is one more thing to keep deterministic. The naive loop
   * is the right call for a pit this size; it stops being the right call
   * somewhere in the low hundreds of balls.
   */
  private resolveBallContacts(): void {
    // Map iteration is insertion order, so the pair order is fixed across
    // runs. Contact resolution is order-dependent, so this is load-bearing
    // for determinism, not incidental.
    const balls = [...this.balls.values()];
    const contactDistance = BALL_RADIUS_M * 2;

    for (let i = 0; i < balls.length; i += 1) {
      const a = balls[i]!;
      for (let j = i + 1; j < balls.length; j += 1) {
        const b = balls[j]!;
        // Two balls in two hands are both pinned by hands; there is no
        // contact to solve, only two positions the human already chose.
        if (a.held && b.held) continue;

        let nx = b.position[0] - a.position[0];
        let ny = b.position[1] - a.position[1];
        let nz = b.position[2] - a.position[2];
        const distance = Math.hypot(nx, ny, nz);
        if (distance >= contactDistance) continue;

        if (distance < 1e-9) {
          // Exactly coincident centers have no contact normal to speak of.
          // Separating along +X is arbitrary, but it has to be the *same*
          // arbitrary direction every run or replay stops matching.
          nx = 1;
          ny = 0;
          nz = 0;
        } else {
          nx /= distance;
          ny /= distance;
          nz /= distance;
        }

        // A held ball is moved by the hand, not by the sim, so it behaves as
        // infinite mass: the free ball takes the entire separation and the
        // entire impulse. That is also what makes sweeping a hand through the
        // pit shove balls aside instead of dragging them along.
        const aShare = a.held ? 0 : b.held ? 1 : 0.5;
        const bShare = b.held ? 0 : a.held ? 1 : 0.5;
        const overlap = contactDistance - distance;

        a.position[0] -= nx * overlap * aShare;
        a.position[1] -= ny * overlap * aShare;
        a.position[2] -= nz * overlap * aShare;
        b.position[0] += nx * overlap * bShare;
        b.position[1] += ny * overlap * bShare;
        b.position[2] += nz * overlap * bShare;

        const approach =
          (b.velocity[0] - a.velocity[0]) * nx +
          (b.velocity[1] - a.velocity[1]) * ny +
          (b.velocity[2] - a.velocity[2]) * nz;
        // Already separating: the overlap is a leftover from the positional
        // pass, not a collision. Applying an impulse here is the classic way
        // a resting stack starts pumping itself into the air.
        if (approach >= 0) continue;

        // Equal masses, so exchanging the normal component of the relative
        // velocity is the whole of the response, and the restitution scales
        // how much of it survives. Keeping it strictly below 1 is what stops
        // a pit full of simultaneous contacts from gaining energy.
        const impulse = -(1 + BALL_RESTITUTION) * approach;
        a.velocity[0] -= nx * impulse * aShare;
        a.velocity[1] -= ny * impulse * aShare;
        a.velocity[2] -= nz * impulse * aShare;
        b.velocity[0] += nx * impulse * bShare;
        b.velocity[1] += ny * impulse * bShare;
        b.velocity[2] += nz * impulse * bShare;
      }
    }
  }

  /**
   * Keep balls on the correct side of every bin wall.
   *
   * A bin is a square tube: an open cavity of `BIN_INTERIOR_M`, walls
   * `BIN_WALL_THICKNESS_M` thick rising `BIN_HEIGHT_M` off the bin floor. The
   * only route in or out is over the rim, which is the property the scoring
   * predicate leans on -- if a free ball could pass sideways through a wall
   * it would flick in and out of `containingBin` and spray events.
   */
  private resolveBinWalls(ball: BallState): void {
    const innerHalf = BIN_INTERIOR_M / 2;
    const outerHalf = innerHalf + BIN_WALL_THICKNESS_M;
    // Distance from the bin's outer surfaces at which a ball *center* is
    // already touching, since the ball is a sphere and not a point.
    const reachXY = outerHalf + BALL_RADIUS_M;
    const insideLimit = Math.max(0, innerHalf - BALL_RADIUS_M);

    for (const bin of BINS) {
      const rimZ = bin.center[2] + BIN_HEIGHT_M;
      const dx = ball.position[0] - bin.center[0];
      const dy = ball.position[1] - bin.center[1];

      if (Math.abs(dx) <= innerHalf && Math.abs(dy) <= innerHalf) {
        // Over the cavity. Above the rim the ball is free -- that is the
        // mouth of the bin and it has to stay open in both directions.
        if (ball.position[2] >= rimZ) continue;
        clampInsideWall(ball, 0, bin.center[0], insideLimit);
        clampInsideWall(ball, 1, bin.center[1], insideLimit);
        continue;
      }

      // Outside the cavity in plan view, so the ball is either clear of the
      // bin, hitting a wall from outside, or perched on the rim.
      if (Math.abs(dx) >= reachXY || Math.abs(dy) >= reachXY) continue;
      if (ball.position[2] >= rimZ + BALL_RADIUS_M) continue;
      if (ball.position[2] <= bin.center[2] - BALL_RADIUS_M) continue;

      // Push out along whichever axis it has sunk into least. That is the
      // standard cheap answer for a sphere against a box, and it does the
      // right thing at both extremes: a ball rolling into the side of the bin
      // gets shoved back out sideways, and a ball dropped onto the 3 cm rim
      // gets lifted to sit on it rather than teleported across the room.
      const penetrationUp = rimZ + BALL_RADIUS_M - ball.position[2];
      const penetrationX = reachXY - Math.abs(dx);
      const penetrationY = reachXY - Math.abs(dy);

      if (penetrationUp <= penetrationX && penetrationUp <= penetrationY) {
        ball.position[2] = rimZ + BALL_RADIUS_M;
        if (ball.velocity[2] < 0) ball.velocity[2] = -ball.velocity[2] * BIN_RESTITUTION;
        if (Math.abs(ball.velocity[2]) < BOUNCE_REST_SPEED_M_S) ball.velocity[2] = 0;
      } else if (penetrationX <= penetrationY) {
        pushOutsideWall(ball, 0, bin.center[0], reachXY);
      } else {
        pushOutsideWall(ball, 1, bin.center[1], reachXY);
      }
    }
  }

  /** Land a ball on whatever surface is under it, bounce what is left of its
   * fall, and bleed off the horizontal slide. */
  private resolveGround(ball: BallState, dtSeconds: number): void {
    const restZ = this.groundUnder(ball.position) + BALL_RADIUS_M;
    if (ball.position[2] > restZ) return;

    ball.position[2] = restZ;

    const impact = -ball.velocity[2];
    let vz = impact > 0 ? impact * GROUND_RESTITUTION : 0;
    if (vz < BOUNCE_REST_SPEED_M_S) vz = 0;

    // Drag is applied per unit time rather than per contact, so a ball that
    // is resting (and therefore in contact every single substep) does not
    // stop faster on a 120 Hz machine than on a 60 Hz one.
    const keep = Math.max(0, 1 - GROUND_DRAG_PER_S * dtSeconds);
    let vx = ball.velocity[0] * keep;
    let vy = ball.velocity[1] * keep;
    if (Math.hypot(vx, vy) < ROLL_REST_SPEED_M_S) {
      vx = 0;
      vy = 0;
    }
    ball.velocity = [vx, vy, vz];
  }

  /**
   * Height of the surface under this point.
   *
   * In the shipped layout the bin floors sit on the room floor, so this
   * always returns the same number -- but the bins own their own floor height
   * in `BinSpec`, and a bin raised onto a platform later should not silently
   * drop its balls to the ground. Written the same way `sortTask.floorUnder`
   * is, for the same reason.
   */
  private groundUnder(position: Vec3): number {
    const half = BIN_INTERIOR_M / 2;
    const bin = BINS.find(
      (b) =>
        Math.abs(position[0] - b.center[0]) <= half && Math.abs(position[1] - b.center[1]) <= half,
    );
    return bin ? bin.center[2] : FLOOR_Z_M;
  }

  private ball(id: string): BallState {
    const ball = this.balls.get(id);
    if (!ball) throw new Error(`unknown ball ${id}`);
    return ball;
  }
}

/** Hold a ball inside a wall along one horizontal axis. Only reflects the
 * velocity when the ball was actually moving into the wall, so a ball resting
 * against it is not repeatedly re-launched. */
function clampInsideWall(ball: BallState, axis: 0 | 1, center: number, limit: number): void {
  const offset = ball.position[axis] - center;
  if (Math.abs(offset) <= limit) return;
  const sign = offset < 0 ? -1 : 1;
  ball.position[axis] = center + sign * limit;
  if (ball.velocity[axis] * sign > 0) {
    ball.velocity[axis] = -ball.velocity[axis] * BIN_RESTITUTION;
  }
}

/** Push a ball back out of a wall along one horizontal axis, the mirror of
 * `clampInsideWall` for contacts from the outside. */
function pushOutsideWall(ball: BallState, axis: 0 | 1, center: number, reach: number): void {
  const offset = ball.position[axis] - center;
  const sign = offset < 0 ? -1 : 1;
  ball.position[axis] = center + sign * reach;
  if (ball.velocity[axis] * sign < 0) {
    ball.velocity[axis] = -ball.velocity[axis] * BIN_RESTITUTION;
  }
}
