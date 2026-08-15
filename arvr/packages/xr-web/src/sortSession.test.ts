import { describe, expect, it } from "vitest";
import { PINCH_ENGAGE } from "./hands";
import { SortSession, structPinchPoint } from "./sortSession";
import {
  BALLS,
  BALL_RADIUS_M,
  BASKETS,
  BASKET_INTERIOR_M,
  BASKET_WALL_THICKNESS_M,
  WORKSPACE_X_M,
  WORKSPACE_Y_M,
  basketFor,
} from "./sortLayout";
import type { WireHandFrame } from "./liveRetargetSession";
import type { Vec3 } from "./contracts";

const OPEN = 0;
const CLOSED = PINCH_ENGAGE;
const DT = 1 / 72;

/** A struct_world hand frame whose pinch point is exactly `at`. */
function handAt(at: Vec3, timestampNs = 0): WireHandFrame {
  const half = 0.01;
  return {
    schema_version: "1.0",
    timestamp_ns: timestampNs,
    source_device: "openxr",
    hand: "right",
    frame: "struct_world",
    joints: {
      wrist: { position_m: [at[0], at[1], at[2] + 0.06], orientation_xyzw: [0, 0, 0, 1] },
      "thumb-tip": { position_m: [at[0], at[1] - half, at[2]], orientation_xyzw: [0, 0, 0, 1] },
      "index-finger-tip": {
        position_m: [at[0], at[1] + half, at[2]],
        orientation_xyzw: [0, 0, 0, 1],
      },
    },
  };
}

describe("structPinchPoint", () => {
  it("is the midpoint of the thumb and index tips", () => {
    const point = structPinchPoint(handAt([0.2, 0.05, 0.17]))!;
    expect(point[0]).toBeCloseTo(0.2, 9);
    expect(point[1]).toBeCloseTo(0.05, 9);
    expect(point[2]).toBeCloseTo(0.17, 9);
  });

  it("returns null when a tip is missing rather than guessing a point", () => {
    const frame = handAt([0.2, 0, 0.17]);
    delete frame.joints["thumb-tip"];
    expect(structPinchPoint(frame)).toBeNull();
  });
});

describe("SortSession — the spec's acceptance scenario", () => {
  it("grabs red_ball_0, carries it to the red basket, and scores 1/3", () => {
    const session = new SortSession();
    const redBall = BALLS.find((b) => b.id === "red_ball_0")!;
    const redBasket = basketFor("red");

    // Approach with an open hand: nothing is grabbed yet.
    let update = session.update({ frame: handAt(redBall.start), gripper: OPEN, dtSeconds: DT });
    expect(update.heldId).toBeNull();
    expect(update.highlightId).toBe("red_ball_0");

    // Pinch on the ball.
    update = session.update({ frame: handAt(redBall.start), gripper: CLOSED, dtSeconds: DT });
    expect(update.heldId).toBe("red_ball_0");
    expect(update.newEvents.some((e) => e.type === "grasp_start")).toBe(true);

    // Carry it over the basket, still pinching.
    const overBasket: Vec3 = [
      redBasket.center[0],
      redBasket.center[1],
      redBasket.center[2] + 0.12,
    ];
    for (const point of pathBetween(redBall.start, overBasket, 20)) {
      update = session.update({ frame: handAt(point), gripper: CLOSED, dtSeconds: DT });
    }
    expect(update.heldId).toBe("red_ball_0");
    expect(session.task.score("red")).toBe(0); // carrying over is not scoring

    // Open the hand: the ball falls into the basket.
    session.update({ frame: handAt(overBasket), gripper: OPEN, dtSeconds: DT });
    for (let i = 0; i < 200; i += 1) {
      session.update({ frame: handAt(overBasket), gripper: OPEN, dtSeconds: DT });
    }

    expect(session.task.score("red")).toBe(1);
    expect(session.task.score("blue")).toBe(0);
    expect(session.task.containerOf("red_ball_0")).toBe("red_basket");
    expect(session.task.isComplete).toBe(false);
  });

  it("scores nothing when a red ball is dropped in the blue basket", () => {
    const session = new SortSession();
    const redBall = BALLS.find((b) => b.id === "red_ball_0")!;
    const blueBasket = basketFor("blue");
    const overWrong: Vec3 = [blueBasket.center[0], blueBasket.center[1], blueBasket.center[2] + 0.12];

    session.update({ frame: handAt(redBall.start), gripper: CLOSED, dtSeconds: DT });
    for (const point of pathBetween(redBall.start, overWrong, 20)) {
      session.update({ frame: handAt(point), gripper: CLOSED, dtSeconds: DT });
    }
    for (let i = 0; i < 200; i += 1) {
      session.update({ frame: handAt(overWrong), gripper: OPEN, dtSeconds: DT });
    }

    expect(session.task.score("red")).toBe(0);
    expect(session.task.score("blue")).toBe(0);
    expect(session.task.containerOf("red_ball_0")).toBe("blue_basket");
    expect(session.task.events.some((e) => e.type === "wrong_basket")).toBe(true);
  });

  it("sorts all six balls to completion", () => {
    const session = new SortSession();
    for (const ball of BALLS) {
      const basket = basketFor(ball.color);
      const over: Vec3 = [basket.center[0], basket.center[1], basket.center[2] + 0.12];
      const start = session.task.ballPosition(ball.id);

      session.update({ frame: handAt(start), gripper: CLOSED, dtSeconds: DT });
      for (const point of pathBetween(start, over, 15)) {
        session.update({ frame: handAt(point), gripper: CLOSED, dtSeconds: DT });
      }
      for (let i = 0; i < 150; i += 1) {
        session.update({ frame: handAt(over), gripper: OPEN, dtSeconds: DT });
      }
    }

    expect(session.task.isComplete).toBe(true);
    expect(session.task.score("red")).toBe(3);
    expect(session.task.score("blue")).toBe(3);
    expect(session.task.events.filter((e) => e.type === "sort_complete")).toHaveLength(1);
  });
});

describe("SortSession tracking loss", () => {
  it("drops the ball and records why when the hand disappears", () => {
    const session = new SortSession();
    const ball = BALLS[0]!;
    session.update({ frame: handAt(ball.start), gripper: CLOSED, dtSeconds: DT });

    const lost = session.update({ frame: null, gripper: CLOSED, dtSeconds: DT });

    expect(lost.heldId).toBeNull();
    expect(lost.newEvents.some((e) => e.type === "tracking_lost")).toBe(true);
    expect(lost.newEvents.some((e) => e.type === "grasp_end")).toBe(true);
  });

  it("records tracking loss once, not once per lost frame", () => {
    const session = new SortSession();
    session.update({ frame: handAt(BALLS[0]!.start), gripper: OPEN, dtSeconds: DT });
    for (let i = 0; i < 5; i += 1) {
      session.update({ frame: null, gripper: OPEN, dtSeconds: DT });
    }

    expect(session.task.events.filter((e) => e.type === "tracking_lost")).toHaveLength(1);
  });

  it("a ball dropped by tracking loss still falls to the table", () => {
    const session = new SortSession();
    const ball = BALLS[0]!;
    const high: Vec3 = [ball.start[0], ball.start[1], ball.start[2] + 0.2];
    session.update({ frame: handAt(ball.start), gripper: CLOSED, dtSeconds: DT });
    session.update({ frame: handAt(high), gripper: CLOSED, dtSeconds: DT });
    for (let i = 0; i < 300; i += 1) {
      session.update({ frame: null, gripper: OPEN, dtSeconds: DT });
    }

    expect(session.task.ballPosition(ball.id)[2]).toBeCloseTo(ball.start[2], 3);
  });
});

describe("SortSession teleop target", () => {
  it("produces a target from the wrist while a demo runs", () => {
    const session = new SortSession();
    const update = session.update({
      frame: handAt([0.2, 0.05, 0.17]),
      gripper: CLOSED,
      dtSeconds: DT,
    });

    expect(update.teleopTarget?.ee_position_m[2]).toBeCloseTo(0.17 + 0.06, 9);
    expect(update.teleopTarget?.gripper).toBeCloseTo(CLOSED, 9);
  });

  it("produces no target when the hand is not tracked", () => {
    const session = new SortSession();
    expect(session.update({ frame: null, gripper: 0, dtSeconds: DT }).teleopTarget).toBeNull();
  });
});

describe("SortSession reset", () => {
  it("puts every ball back and forgets the score", () => {
    const session = new SortSession();
    const ball = BALLS[0]!;
    const basket = basketFor(ball.color);
    const over: Vec3 = [basket.center[0], basket.center[1], basket.center[2] + 0.1];

    session.update({ frame: handAt(ball.start), gripper: CLOSED, dtSeconds: DT });
    for (const point of pathBetween(ball.start, over, 10)) {
      session.update({ frame: handAt(point), gripper: CLOSED, dtSeconds: DT });
    }
    for (let i = 0; i < 150; i += 1) {
      session.update({ frame: handAt(over), gripper: OPEN, dtSeconds: DT });
    }
    expect(session.task.score("red")).toBe(1);

    session.reset();

    expect(session.task.score("red")).toBe(0);
    expect(session.task.ballPosition(ball.id)).toEqual(ball.start);
    expect(session.task.events).toHaveLength(0);
  });

  it("lets a ball be picked up again after a reset", () => {
    const session = new SortSession();
    const ball = BALLS[0]!;
    session.update({ frame: handAt(ball.start), gripper: CLOSED, dtSeconds: DT });
    session.reset();

    const after = session.update({
      frame: handAt(ball.start),
      gripper: CLOSED,
      dtSeconds: DT,
    });
    expect(after.heldId).toBe(ball.id);
  });
});

/** Straight-line waypoints from a to b, excluding a. */
function pathBetween(a: Vec3, b: Vec3, steps: number): Vec3[] {
  const points: Vec3[] = [];
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps;
    points.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
  }
  return points;
}

describe("scene layout sanity", () => {
  it("never places two balls closer than their own diameter", () => {
    // The first layout spaced them 4.5cm apart with a 6cm ball, so they
    // started life interpenetrating -- visible immediately in a browser and
    // invisible to a test that only asserted "more than one radius".
    for (const a of BALLS) {
      for (const b of BALLS) {
        if (a.id === b.id) continue;
        const distance = Math.hypot(
          a.start[0] - b.start[0],
          a.start[1] - b.start[1],
          a.start[2] - b.start[2],
        );
        expect(distance).toBeGreaterThan(BALL_RADIUS_M * 2);
      }
    }
  });

  it("keeps balls clear of the basket walls they start beside", () => {
    for (const ball of BALLS) {
      for (const basket of BASKETS) {
        const half = BASKET_INTERIOR_M / 2 + BASKET_WALL_THICKNESS_M;
        const overlapsX = Math.abs(ball.start[0] - basket.center[0]) < half + BALL_RADIUS_M;
        const overlapsY = Math.abs(ball.start[1] - basket.center[1]) < half + BALL_RADIUS_M;
        expect(overlapsX && overlapsY).toBe(false);
      }
    }
  });

  it("starts no ball already inside a basket", () => {
    const session = new SortSession();
    expect(session.task.score("red")).toBe(0);
    expect(session.task.score("blue")).toBe(0);
  });

  it("keeps every ball and basket inside the measured reachable workspace", () => {
    // sortLayout's bounds come from tools/so101_reach_envelope.py. A scene
    // that drifts outside them is a scene the arm cannot follow a hand into.
    const inside = (p: readonly number[], pad: number): boolean =>
      p[0]! - pad >= WORKSPACE_X_M[0] &&
      p[0]! + pad <= WORKSPACE_X_M[1] &&
      p[1]! - pad >= WORKSPACE_Y_M[0] &&
      p[1]! + pad <= WORKSPACE_Y_M[1];

    for (const ball of BALLS) expect(inside(ball.start, BALL_RADIUS_M)).toBe(true);
    for (const basket of BASKETS) {
      expect(inside(basket.center, BASKET_INTERIOR_M / 2)).toBe(true);
    }
  });

  it("separates the two baskets so a drop is never ambiguous", () => {
    const [red, blue] = BASKETS;
    const gap = Math.abs(red!.center[1] - blue!.center[1]) - BASKET_INTERIOR_M;
    expect(gap).toBeGreaterThan(BALL_RADIUS_M * 2);
  });
});
