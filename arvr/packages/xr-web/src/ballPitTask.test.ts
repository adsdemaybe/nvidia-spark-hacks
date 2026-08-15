import { describe, expect, it } from "vitest";
import {
  BALLS,
  BALLS_PER_COLOR,
  BALL_RADIUS_M,
  BINS,
  BIN_HEIGHT_M,
  BIN_INTERIOR_M,
  BIN_WALL_THICKNESS_M,
  FLOOR_Z_M,
  binFor,
  type BinSpec,
} from "./ballPitLayout";
import { BallPitTask, containingBin, insideBin } from "./ballPitTask";
import type { Vec3 } from "./contracts";

const RED_BIN = binFor("red");
const BLUE_BIN = binFor("blue");
const REST_Z = FLOOR_Z_M + BALL_RADIUS_M;
const INNER_HALF = BIN_INTERIOR_M / 2;
const RIM_Z = FLOOR_Z_M + BIN_HEIGHT_M;

/** A point just above a bin's floor, inset from its walls. */
function inBin(bin: BinSpec, dx = 0, dy = 0): Vec3 {
  return [bin.center[0] + dx, bin.center[1] + dy, bin.center[2] + BALL_RADIUS_M];
}

/**
 * Park every ball except the named ones far out of the room, in a line spaced
 * wider than a ball diameter.
 *
 * Twenty-four balls all obey the same physics, so a test about *one* ball's
 * bounce would otherwise be a test about one ball's bounce plus twenty-three
 * neighbours it might clip on the way down. Parking them keeps the assertion
 * about the thing it names. They are parked resting on the floor rather than
 * deleted because there is no delete -- the ball set is the layout's.
 */
function isolate(task: BallPitTask, keep: string[]): void {
  let parked = 0;
  for (const ball of BALLS) {
    if (keep.includes(ball.id)) continue;
    task.setBallPosition(ball.id, [-20 - parked * 0.5, 0, REST_Z]);
    parked += 1;
  }
}

/** Put every ball in a bin, optionally sending one ball somewhere it does not
 * belong, and score the result. No stepping, so nothing rolls: this is about
 * the predicate, not the physics. */
function placeAll(task: BallPitTask, misplaced?: { id: string; bin: BinSpec }): void {
  for (const ball of BALLS) {
    const bin = misplaced?.id === ball.id ? misplaced.bin : binFor(ball.color);
    task.setBallPosition(ball.id, inBin(bin));
  }
  task.settle();
}

/** Total mechanical energy per unit mass, summed over the pit: kinetic plus
 * gravitational potential measured from the resting height. Every ball has the
 * same mass, so leaving mass out changes nothing that matters. */
function totalEnergy(task: BallPitTask): number {
  let total = 0;
  for (const ball of BALLS) {
    const v = task.ballVelocity(ball.id);
    const p = task.ballPosition(ball.id);
    total += 0.5 * (v[0] ** 2 + v[1] ** 2 + v[2] ** 2);
    total += 9.81 * Math.max(0, p[2] - REST_Z);
  }
  return total;
}

function speed(task: BallPitTask, id: string): number {
  const v = task.ballVelocity(id);
  return Math.hypot(v[0], v[1], v[2]);
}

/** Closest pair of ball centers anywhere in the pit. */
function closestPair(task: BallPitTask): number {
  let closest = Infinity;
  for (let i = 0; i < BALLS.length; i += 1) {
    for (let j = i + 1; j < BALLS.length; j += 1) {
      const a = task.ballPosition(BALLS[i]!.id);
      const b = task.ballPosition(BALLS[j]!.id);
      closest = Math.min(closest, Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]));
    }
  }
  return closest;
}

describe("insideBin", () => {
  it("accepts a ball resting in the middle of a bin", () => {
    expect(insideBin(inBin(RED_BIN), RED_BIN)).toBe(true);
  });

  it("rejects a ball outside the interior footprint", () => {
    expect(insideBin(inBin(RED_BIN, 0, BIN_INTERIOR_M), RED_BIN)).toBe(false);
    expect(insideBin(inBin(RED_BIN, BIN_INTERIOR_M, 0), RED_BIN)).toBe(false);
  });

  it("rejects a ball carried high above the bin", () => {
    // Walking past a bin with a ball in your hand is not putting it in the
    // bin -- and at room scale you walk past both bins constantly.
    const overhead: Vec3 = [RED_BIN.center[0], RED_BIN.center[1], 1.6];
    expect(insideBin(overhead, RED_BIN)).toBe(false);
  });

  it("rejects a ball below the bin floor", () => {
    const under: Vec3 = [RED_BIN.center[0], RED_BIN.center[1], RED_BIN.center[2] - 0.2];
    expect(insideBin(under, RED_BIN)).toBe(false);
  });

  it("treats the bin floor and the rim as inside, inclusively", () => {
    // A ball can rest exactly on either plane, and a strict inequality on
    // the floor would silently stop counting the balls that matter most.
    expect(insideBin([RED_BIN.center[0], RED_BIN.center[1], RED_BIN.center[2]], RED_BIN)).toBe(true);
    expect(insideBin([RED_BIN.center[0], RED_BIN.center[1], RIM_Z], RED_BIN)).toBe(true);
  });

  it("rejects a ball a hair above the rim", () => {
    expect(insideBin([RED_BIN.center[0], RED_BIN.center[1], RIM_Z + 1e-6], RED_BIN)).toBe(false);
  });

  it("treats the footprint edge as inside and a hair past it as outside", () => {
    expect(insideBin(inBin(RED_BIN, INNER_HALF, 0), RED_BIN)).toBe(true);
    expect(insideBin(inBin(RED_BIN, 0, INNER_HALF), RED_BIN)).toBe(true);
    expect(insideBin(inBin(RED_BIN, INNER_HALF + 1e-6, 0), RED_BIN)).toBe(false);
    expect(insideBin(inBin(RED_BIN, 0, INNER_HALF + 1e-6), RED_BIN)).toBe(false);
  });

  it("does not confuse the two bins", () => {
    expect(insideBin(inBin(RED_BIN), BLUE_BIN)).toBe(false);
    expect(insideBin(inBin(BLUE_BIN), RED_BIN)).toBe(false);
  });

  it("containingBin names the bin a point is in, and nothing on open floor", () => {
    expect(containingBin(inBin(BLUE_BIN))?.id).toBe("blue_bin");
    expect(containingBin([1.0, 0, REST_Z])).toBeNull();
  });
});

describe("BallPitTask scoring", () => {
  it("starts with nothing sorted and nothing complete", () => {
    const task = new BallPitTask();
    expect(task.score("red")).toBe(0);
    expect(task.score("blue")).toBe(0);
    expect(task.isComplete).toBe(false);
  });

  it("scores a red ball placed in the red bin", () => {
    const task = new BallPitTask();
    task.setBallPosition("red_ball_0", inBin(RED_BIN));
    task.settle();

    expect(task.score("red")).toBe(1);
    expect(task.containerOf("red_ball_0")).toBe("red_bin");
  });

  it("counts a red ball in the blue bin as wrong, not as a point", () => {
    const task = new BallPitTask();
    task.setBallPosition("red_ball_0", inBin(BLUE_BIN));
    task.settle();

    expect(task.score("red")).toBe(0);
    expect(task.score("blue")).toBe(0);
    expect(task.containerOf("red_ball_0")).toBe("blue_bin");
    expect(task.events.filter((e) => e.type === "wrong_basket")).toHaveLength(1);
  });

  it("does not double-count a ball that stays in its bin", () => {
    const task = new BallPitTask();
    task.setBallPosition("red_ball_0", inBin(RED_BIN));
    task.settle();
    task.settle();
    task.settle();

    expect(task.score("red")).toBe(1);
    expect(task.events.filter((e) => e.type === "ball_enter_basket")).toHaveLength(1);
  });

  it("does not score a ball that is still held inside the bin", () => {
    // Reaching into a bin holding a ball is not letting go of it.
    const task = new BallPitTask();
    task.setHeld("red_ball_0", true);
    task.setBallPosition("red_ball_0", inBin(RED_BIN));
    task.settle();

    expect(task.score("red")).toBe(0);
    expect(task.containerOf("red_ball_0")).toBeNull();

    task.setHeld("red_ball_0", false);
    task.settle();

    expect(task.score("red")).toBe(1);
  });

  it("does not score a ball carried through a bin on the way past", () => {
    // The desk task learned this the hard way: a ball carried through the
    // container volume scored on the way through and again on landing, and
    // sort_complete fired twice. At room scale the sweep is much longer --
    // you walk a ball right across a waist-high bin -- so the same mistake
    // would fire on almost every trip.
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setHeld("red_ball_0", true);

    for (let i = 0; i <= 40; i += 1) {
      const t = i / 40;
      task.setBallPosition("red_ball_0", [
        RED_BIN.center[0],
        RED_BIN.center[1] - 0.8 + t * 1.6,
        RED_BIN.center[2] + 0.2,
      ]);
      task.step(1 / 72);
      expect(task.containerOf("red_ball_0")).toBeNull();
    }

    expect(task.events.filter((e) => e.type === "ball_enter_basket")).toHaveLength(0);
    expect(task.score("red")).toBe(0);
  });

  it("scores nothing when a ball carried through a bin is dropped outside it", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setHeld("red_ball_0", true);
    for (let i = 0; i <= 20; i += 1) {
      const t = i / 20;
      task.setBallPosition("red_ball_0", [
        RED_BIN.center[0],
        RED_BIN.center[1] - 0.6 + t * 1.2,
        RED_BIN.center[2] + 0.25,
      ]);
      task.step(1 / 72);
    }
    task.setBallPosition("red_ball_0", [RED_BIN.center[0], RED_BIN.center[1] - 1.2, 0.9]);
    task.setHeld("red_ball_0", false);
    for (let i = 0; i < 300; i += 1) task.step(1 / 72);

    expect(task.containerOf("red_ball_0")).toBeNull();
    expect(task.events.some((e) => e.type === "ball_enter_basket")).toBe(false);
  });

  it("takes the point back when a ball is lifted out again", () => {
    const task = new BallPitTask();
    task.setBallPosition("red_ball_0", inBin(RED_BIN));
    task.settle();
    expect(task.score("red")).toBe(1);

    task.setBallPosition("red_ball_0", [0.8, 0, 1.2]);
    task.settle();

    expect(task.score("red")).toBe(0);
    expect(task.containerOf("red_ball_0")).toBeNull();
  });

  it("is complete only when every ball is in its matching bin", () => {
    const task = new BallPitTask();
    placeAll(task);

    expect(task.isComplete).toBe(true);
    expect(task.score("red")).toBe(BALLS_PER_COLOR);
    expect(task.score("blue")).toBe(BALLS_PER_COLOR);
    expect(task.events.filter((e) => e.type === "sort_complete")).toHaveLength(1);
  });

  it("is not complete when one ball sits in the wrong bin", () => {
    const task = new BallPitTask();
    placeAll(task, { id: "blue_ball_5", bin: RED_BIN });

    expect(task.isComplete).toBe(false);
    expect(task.score("blue")).toBe(BALLS_PER_COLOR - 1);
    expect(task.events.some((e) => e.type === "sort_complete")).toBe(false);
  });

  it("emits sort_complete once, not on every settle after completion", () => {
    const task = new BallPitTask();
    placeAll(task);
    task.settle();
    task.settle();

    expect(task.events.filter((e) => e.type === "sort_complete")).toHaveLength(1);
  });

  it("re-arms sort_complete when a finished pit is broken and finished again", () => {
    // Pulling a ball back out un-finishes the task, so finishing it a second
    // time is a real second completion, not a duplicate of the first.
    const task = new BallPitTask();
    placeAll(task);

    task.setHeld("red_ball_0", true);
    task.settle();
    expect(task.isComplete).toBe(false);

    task.setHeld("red_ball_0", false);
    task.settle();

    expect(task.isComplete).toBe(true);
    expect(task.events.filter((e) => e.type === "sort_complete")).toHaveLength(2);
  });
});

describe("BallPitTask falling and resting", () => {
  it("drops a released ball onto the floor and stops it there", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, 1.0]);

    for (let i = 0; i < 400; i += 1) task.step(1 / 72);

    expect(task.ballPosition("red_ball_0")[2]).toBeCloseTo(REST_Z, 6);
    expect(speed(task, "red_ball_0")).toBe(0);
  });

  it("never lets a falling ball pass below the floor", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, 2.0]);

    let lowest = Infinity;
    for (let i = 0; i < 400; i += 1) {
      task.step(1 / 72);
      lowest = Math.min(lowest, task.ballPosition("red_ball_0")[2]);
    }

    expect(lowest).toBeGreaterThanOrEqual(REST_Z - 1e-9);
  });

  it("bounces before it settles, rather than landing dead", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, 1.0]);

    const heights: number[] = [];
    for (let i = 0; i < 400; i += 1) {
      task.step(1 / 72);
      heights.push(task.ballPosition("red_ball_0")[2]);
    }

    const firstContact = heights.findIndex((z) => z <= REST_Z + 1e-9);
    expect(firstContact).toBeGreaterThan(0);
    // A hollow plastic ball off a hard floor comes back up a good fraction of
    // a waist-height drop. Anything under a few centimetres reads as a
    // beanbag, which is the failure this pins down.
    expect(Math.max(...heights.slice(firstContact))).toBeGreaterThan(REST_Z + 0.05);
  });

  it("stops bouncing instead of hopping forever", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, 1.0]);

    // Two seconds is generous for a one-metre drop; the point is that the
    // geometric series of bounces terminates at all.
    for (let i = 0; i < 144; i += 1) task.step(1 / 72);

    expect(task.ballVelocity("red_ball_0")).toEqual([0, 0, 0]);
    expect(task.ballPosition("red_ball_0")[2]).toBeCloseTo(REST_Z, 9);
  });

  it("does not move a held ball, however long it is stepped", () => {
    const task = new BallPitTask();
    const carried: Vec3 = [0.6, 0.2, 1.3];
    task.setBallPosition("red_ball_0", carried);
    task.setHeld("red_ball_0", true);

    for (let i = 0; i < 500; i += 1) task.step(1 / 72);

    expect(task.ballPosition("red_ball_0")).toEqual(carried);
  });

  it("lands a released ball on the bin floor and scores it there", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [RED_BIN.center[0], RED_BIN.center[1], 1.5]);

    let lowest = Infinity;
    for (let i = 0; i < 400; i += 1) {
      task.step(1 / 72);
      lowest = Math.min(lowest, task.ballPosition("red_ball_0")[2]);
    }

    const position = task.ballPosition("red_ball_0");
    expect(lowest).toBeGreaterThanOrEqual(RED_BIN.center[2] + BALL_RADIUS_M - 1e-9);
    expect(insideBin(position, RED_BIN)).toBe(true);
    expect(task.containerOf("red_ball_0")).toBe("red_bin");
  });

  it("keeps a ball dropped near an inner wall inside the bin", () => {
    // Dropped right against the wall the ball has to be pushed back in by a
    // radius, and it must not end up straddling or outside the footprint.
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [
      RED_BIN.center[0] + INNER_HALF - 0.005,
      RED_BIN.center[1],
      1.2,
    ]);

    for (let i = 0; i < 400; i += 1) task.step(1 / 72);

    const position = task.ballPosition("red_ball_0");
    expect(Math.abs(position[0] - RED_BIN.center[0])).toBeLessThanOrEqual(
      INNER_HALF - BALL_RADIUS_M + 1e-9,
    );
    expect(insideBin(position, RED_BIN)).toBe(true);
  });

  it("bounces a ball thrown at the outside of a bin back into the room", () => {
    // The walls are solid from outside too. If they were not, a hard throw
    // would pop through the side and land in the bin, which would score a
    // shot the human never made.
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [RED_BIN.center[0], RED_BIN.center[1] - 1.2, REST_Z]);
    task.releaseBall("red_ball_0", [0, 6, 0]);

    for (let i = 0; i < 400; i += 1) {
      task.step(1 / 72);
      expect(task.containerOf("red_ball_0")).toBeNull();
    }

    const position = task.ballPosition("red_ball_0");
    const outerFace = RED_BIN.center[1] - INNER_HALF - BIN_WALL_THICKNESS_M - BALL_RADIUS_M;
    expect(position[1]).toBeLessThanOrEqual(outerFace + 1e-9);
  });

  it("does not let a ball dropped onto the rim fall into the bin", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [
      RED_BIN.center[0],
      RED_BIN.center[1] + INNER_HALF + BIN_WALL_THICKNESS_M / 2,
      1.4,
    ]);

    for (let i = 0; i < 500; i += 1) task.step(1 / 72);

    expect(task.containerOf("red_ball_0")).toBeNull();
    expect(task.ballPosition("red_ball_0")[2]).toBeGreaterThanOrEqual(RIM_Z);
  });
});

describe("BallPitTask throwing", () => {
  it("sends a released ball downrange on an arc", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, 1.2]);
    task.releaseBall("red_ball_0", [3, 0, 2]);

    let apex = 0;
    for (let i = 0; i < 600; i += 1) {
      task.step(1 / 72);
      apex = Math.max(apex, task.ballPosition("red_ball_0")[2]);
    }

    const landed = task.ballPosition("red_ball_0");
    expect(apex).toBeGreaterThan(1.3);
    expect(landed[0]).toBeGreaterThan(3);
    expect(landed[2]).toBeCloseTo(REST_Z, 6);
  });

  it("can land a throw in a bin from across the room", () => {
    // The whole reason `releaseBall` takes a velocity: a room-scale pit where
    // every ball has to be walked over and dropped in is a walking simulator.
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, 1.3]);
    task.releaseBall("red_ball_0", [1.437, 1.687, 2.92]);

    for (let i = 0; i < 500; i += 1) task.step(1 / 72);

    expect(task.containerOf("red_ball_0")).toBe("red_bin");
    expect(task.score("red")).toBe(1);
  });

  it("emits exactly one grasp_end when a held ball is thrown", () => {
    const task = new BallPitTask();
    task.setHeld("red_ball_0", true);
    task.releaseBall("red_ball_0", [1, 0, 1]);
    task.releaseBall("red_ball_0", [1, 0, 1]);

    expect(task.events.filter((e) => e.type === "grasp_end")).toHaveLength(1);
    expect(task.isHeld("red_ball_0")).toBe(false);
  });

  it("throws a ball that was never held without inventing a grasp_end", () => {
    const task = new BallPitTask();
    task.releaseBall("red_ball_0", [0, 0, 4]);

    expect(task.events).toHaveLength(0);
    expect(task.ballVelocity("red_ball_0")).toEqual([0, 0, 4]);
  });

  it("cancels a pending throw when the ball is repositioned by a hand", () => {
    // Catching a ball has to kill whatever it was doing, or it resumes its
    // old flight the moment it is let go again.
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.releaseBall("red_ball_0", [5, 0, 3]);
    task.setBallPosition("red_ball_0", [0, 0, 1.0]);

    expect(task.ballVelocity("red_ball_0")).toEqual([0, 0, 0]);

    for (let i = 0; i < 300; i += 1) task.step(1 / 72);

    const landed = task.ballPosition("red_ball_0");
    expect(landed[0]).toBeCloseTo(0, 9);
    expect(landed[1]).toBeCloseTo(0, 9);
  });
});

describe("BallPitTask ball-to-ball contact", () => {
  it("separates two balls that start on top of each other", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0", "blue_ball_0"]);
    task.setBallPosition("red_ball_0", [0, 0, REST_Z]);
    task.setBallPosition("blue_ball_0", [0.02, 0, REST_Z]);

    for (let i = 0; i < 200; i += 1) task.step(1 / 72);

    const a = task.ballPosition("red_ball_0");
    const b = task.ballPosition("blue_ball_0");
    expect(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])).toBeGreaterThanOrEqual(
      2 * BALL_RADIUS_M - 1e-6,
    );
  });

  it("bounces two balls thrown head-on apart instead of through each other", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0", "blue_ball_0"]);
    task.setBallPosition("red_ball_0", [0, -0.6, REST_Z]);
    task.setBallPosition("blue_ball_0", [0, 0.6, REST_Z]);
    task.releaseBall("red_ball_0", [0, 2, 0]);
    task.releaseBall("blue_ball_0", [0, -2, 0]);

    for (let i = 0; i < 300; i += 1) task.step(1 / 72);

    const a = task.ballPosition("red_ball_0");
    const b = task.ballPosition("blue_ball_0");
    // Each stayed on its own side: they met and reversed rather than swapping
    // places, which is what tunnelling would look like.
    expect(a[1]).toBeLessThan(0);
    expect(b[1]).toBeGreaterThan(0);
    expect(b[1] - a[1]).toBeGreaterThanOrEqual(2 * BALL_RADIUS_M);
  });

  it("passes motion from a moving ball to a resting one", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0", "blue_ball_0"]);
    task.setBallPosition("red_ball_0", [0, -0.5, REST_Z]);
    task.setBallPosition("blue_ball_0", [0, 0, REST_Z]);
    task.releaseBall("red_ball_0", [0, 3, 0]);

    for (let i = 0; i < 300; i += 1) task.step(1 / 72);

    // The struck ball has been pushed along +Y, and the striker did not sail
    // straight through it.
    expect(task.ballPosition("blue_ball_0")[1]).toBeGreaterThan(0.1);
    expect(task.ballPosition("red_ball_0")[1]).toBeLessThan(
      task.ballPosition("blue_ball_0")[1] - BALL_RADIUS_M,
    );
  });

  it("leaves no two balls overlapping after a busy run settles", () => {
    const task = new BallPitTask();
    task.releaseBall(BALLS[0]!.id, [1.5, 0.4, 3.0]);
    task.releaseBall(BALLS[3]!.id, [-1.2, 1.1, 2.5]);
    task.releaseBall(BALLS[7]!.id, [0.8, -1.6, 3.5]);
    task.releaseBall(BALLS[12]!.id, [2.0, 0.9, 1.5]);

    for (let i = 0; i < 1500; i += 1) task.step(1 / 72);

    expect(closestPair(task)).toBeGreaterThanOrEqual(2 * BALL_RADIUS_M - 1e-6);
  });

  it("lets a held ball shove a free one aside without being shoved itself", () => {
    // A hand sweeping through the pit should part it. The hand owns the held
    // ball's position, so contacts must be one-way.
    const task = new BallPitTask();
    isolate(task, ["red_ball_0", "blue_ball_0"]);
    task.setBallPosition("blue_ball_0", [0, 0, REST_Z]);
    const handPosition: Vec3 = [0.05, 0, REST_Z];
    task.setHeld("red_ball_0", true);
    task.setBallPosition("red_ball_0", handPosition);

    for (let i = 0; i < 60; i += 1) task.step(1 / 72);

    expect(task.ballPosition("red_ball_0")).toEqual(handPosition);
    const free = task.ballPosition("blue_ball_0");
    expect(free[0]).toBeLessThan(0);
    expect(Math.hypot(free[0] - handPosition[0], free[1] - handPosition[1])).toBeGreaterThanOrEqual(
      2 * BALL_RADIUS_M - 1e-6,
    );
  });
});

describe("BallPitTask stability", () => {
  it("never gains energy over a long, contact-heavy run", () => {
    // The one failure mode that ruins a pit: overlapping contacts pumping
    // each other until balls launch off the floor. Every restitution here is
    // below 1 and every positional fix is a correction rather than a push,
    // so total energy must only ever fall.
    const task = new BallPitTask();
    task.releaseBall(BALLS[0]!.id, [1.5, 0.4, 3.0]);
    task.releaseBall(BALLS[3]!.id, [-1.2, 1.1, 2.5]);
    task.releaseBall(BALLS[7]!.id, [0.8, -1.6, 3.5]);
    task.releaseBall(BALLS[12]!.id, [2.0, 0.9, 1.5]);

    const initial = totalEnergy(task);
    expect(initial).toBeGreaterThan(1);

    let worst = 0;
    for (let i = 0; i < 2000; i += 1) {
      task.step(1 / 72);
      worst = Math.max(worst, totalEnergy(task));
    }

    expect(worst).toBeLessThanOrEqual(initial + 1e-9);
    expect(totalEnergy(task)).toBeLessThan(initial * 0.01);
  });

  it("brings the whole pit to a hard stop with no NaN anywhere", () => {
    const task = new BallPitTask();
    task.releaseBall(BALLS[0]!.id, [4, 2, 5]);
    task.releaseBall(BALLS[6]!.id, [-3, -2, 4]);

    for (let i = 0; i < 2000; i += 1) task.step(1 / 72);

    for (const ball of BALLS) {
      const p = task.ballPosition(ball.id);
      const v = task.ballVelocity(ball.id);
      for (const value of [...p, ...v]) expect(Number.isFinite(value)).toBe(true);
      expect(v).toEqual([0, 0, 0]);
      expect(p[2]).toBeGreaterThanOrEqual(REST_Z - 1e-9);
    }
  });

  it("splits a stalled frame rather than swallowing or lurching through it", () => {
    // A browser tab that hitches for a quarter second must simulate a quarter
    // second, and it must do it in bounded pieces so nothing tunnels.
    const hitched = new BallPitTask();
    hitched.releaseBall(BALLS[0]!.id, [3, 1, 4]);
    hitched.step(0.25);

    const smooth = new BallPitTask();
    smooth.releaseBall(BALLS[0]!.id, [3, 1, 4]);
    for (let i = 0; i < 30; i += 1) smooth.step(1 / 120);

    expect(hitched.objectStates()).toEqual(smooth.objectStates());
  });

  it("survives nine balls dumped into one bin at once", () => {
    // The densest thing that can happen in this scene: a bin holding enough
    // balls that they stack, so contacts are simultaneous and every one of
    // them is also resolving against the bin floor and walls. A pile is where
    // a naive impulse solver launches balls out of the container, so this
    // asserts the pile is still a pile afterwards.
    const task = new BallPitTask();
    isolate(task, []);
    const piled = BALLS.slice(0, 9).map((ball) => ball.id);
    piled.forEach((id, i) => {
      task.setBallPosition(id, [
        RED_BIN.center[0] + ((i % 3) - 1) * 0.14,
        RED_BIN.center[1] + (Math.floor(i / 3) - 1) * 0.14,
        1.0 + (i % 3) * 0.12,
      ]);
    });

    const initial = totalEnergy(task);
    let worst = 0;
    for (let i = 0; i < 2000; i += 1) {
      task.step(1 / 72);
      worst = Math.max(worst, totalEnergy(task));
    }

    expect(worst).toBeLessThanOrEqual(initial + 1e-9);
    for (const id of piled) {
      expect(speed(task, id)).toBe(0);
      expect(insideBin(task.ballPosition(id), RED_BIN)).toBe(true);
    }
    expect(closestPair(task)).toBeGreaterThanOrEqual(2 * BALL_RADIUS_M - 1e-6);
  });

  it("treats a zero-length step as scoring only, with no motion", () => {
    const task = new BallPitTask();
    task.setBallPosition("red_ball_0", [0, 0, 2.0]);
    task.setBallPosition("blue_ball_0", inBin(BLUE_BIN));
    task.step(0);

    expect(task.ballPosition("red_ball_0")).toEqual([0, 0, 2.0]);
    expect(task.score("blue")).toBe(1);
  });
});

describe("BallPitTask determinism", () => {
  it("gives the same resting place for the same throw", () => {
    const throwOnce = (): Vec3 => {
      const task = new BallPitTask();
      isolate(task, ["blue_ball_1"]);
      task.setBallPosition("blue_ball_1", [0.3, -0.2, 1.4]);
      task.releaseBall("blue_ball_1", [2.1, 0.7, 1.9]);
      for (let i = 0; i < 400; i += 1) task.step(1 / 90);
      return task.ballPosition("blue_ball_1");
    };
    expect(throwOnce()).toEqual(throwOnce());
  });

  it("replays a whole contact-heavy run to identical object states", () => {
    // Contact resolution is order-dependent, so this is really a test that
    // the pair iteration order is fixed. Without it a recorded episode
    // replays against a subtly different scene.
    const run = (): unknown => {
      const task = new BallPitTask();
      task.releaseBall(BALLS[1]!.id, [1.1, -0.9, 3.2]);
      task.releaseBall(BALLS[9]!.id, [-1.4, 1.3, 2.1]);
      task.releaseBall(BALLS[17]!.id, [0.5, 0.5, 4.0]);
      for (let i = 0; i < 900; i += 1) task.step(1 / 72);
      return task.objectStates();
    };
    expect(run()).toEqual(run());
  });
});

describe("BallPitTask events", () => {
  it("records a grasp start and end around a pickup", () => {
    const task = new BallPitTask();
    task.setHeld("red_ball_0", true);
    task.setHeld("red_ball_0", false);

    expect(task.events.map((e) => e.type)).toEqual(["grasp_start", "grasp_end"]);
  });

  it("does not emit a second grasp_start for a ball already held", () => {
    const task = new BallPitTask();
    task.setHeld("red_ball_0", true);
    task.setHeld("red_ball_0", true);

    expect(task.events.filter((e) => e.type === "grasp_start")).toHaveLength(1);
  });

  it("stamps every event with the object and container it concerns", () => {
    const task = new BallPitTask();
    task.setBallPosition("blue_ball_0", inBin(BLUE_BIN));
    task.settle();

    const entered = task.events.find((e) => e.type === "ball_enter_basket");
    expect(entered?.objectId).toBe("blue_ball_0");
    expect(entered?.containerId).toBe("blue_bin");
  });

  it("emits ball_enter_basket once, not once per frame the ball rests there", () => {
    const task = new BallPitTask();
    isolate(task, ["red_ball_0"]);
    task.setBallPosition("red_ball_0", [RED_BIN.center[0], RED_BIN.center[1], 1.2]);

    for (let i = 0; i < 600; i += 1) task.step(1 / 72);

    expect(task.events.filter((e) => e.type === "ball_enter_basket")).toHaveLength(1);
    expect(task.events.filter((e) => e.type === "wrong_basket")).toHaveLength(0);
  });

  it("records a tracking gap without touching the balls", () => {
    const task = new BallPitTask();
    const before = task.ballPosition("red_ball_0");
    task.markTrackingLost();

    expect(task.events).toEqual([{ type: "tracking_lost" }]);
    expect(task.ballPosition("red_ball_0")).toEqual(before);
  });
});

describe("BallPitTask object states and reset", () => {
  it("reports every ball and every bin for the episode record", () => {
    const ids = new BallPitTask().objectStates().map((state) => state.id);
    for (const ball of BALLS) expect(ids).toContain(ball.id);
    for (const bin of BINS) expect(ids).toContain(bin.id);
    expect(ids).toHaveLength(BALLS.length + BINS.length);
  });

  it("reports live ball positions, not their starting ones", () => {
    const task = new BallPitTask();
    const moved: Vec3 = [0.9, 0.4, 1.1];
    task.setBallPosition("red_ball_1", moved);

    const state = task.objectStates().find((s) => s.id === "red_ball_1");
    expect(state?.position_m).toEqual(moved);
  });

  it("hands out copies, so a caller cannot reach in and move a ball", () => {
    const task = new BallPitTask();
    const position = task.ballPosition("red_ball_0");
    position[2] = 99;

    expect(task.ballPosition("red_ball_0")[2]).not.toBe(99);
  });

  it("resets every ball to its start, at rest, with the score and events clear", () => {
    const task = new BallPitTask();
    task.setHeld("red_ball_0", true);
    task.setBallPosition("red_ball_0", inBin(RED_BIN));
    task.releaseBall("red_ball_0", [2, 2, 2]);
    task.step(1 / 72);
    task.reset();

    expect(task.score("red")).toBe(0);
    expect(task.events).toHaveLength(0);
    expect(task.isComplete).toBe(false);
    expect(task.ballPosition("red_ball_0")).toEqual(BALLS[0]!.start);
    expect(task.ballVelocity("red_ball_0")).toEqual([0, 0, 0]);
    expect(task.isHeld("red_ball_0")).toBe(false);
  });
});
