import { describe, expect, it } from "vitest";
import { PINCH_ENGAGE } from "./hands";
import {
  BallPitSession,
  PIT_GRASP_RADIUS_M,
  type BallPitTaskLike,
  type HandInput,
} from "./ballPitSession";
import { BALLS } from "./ballPitLayout";
import type { Vec3 } from "./contracts";

const OPEN = 0;
const CLOSED = PINCH_ENGAGE;
const DT = 1 / 72;

/** A stand-in for the real task: records what the session asked it to do, so
 * a test can assert on the interaction without pulling in physics. */
class StubTask implements BallPitTaskLike {
  positions = new Map<string, Vec3>();
  held = new Set<string>();
  releases: Array<{ id: string; velocity: Vec3 }> = [];
  trackingLost = 0;

  constructor() {
    for (const ball of BALLS) this.positions.set(ball.id, [...ball.start] as Vec3);
  }

  ballPosition(id: string): Vec3 {
    return [...this.positions.get(id)!] as Vec3;
  }
  isHeld(id: string): boolean {
    return this.held.has(id);
  }
  setBallPosition(id: string, position: Vec3): void {
    this.positions.set(id, [...position] as Vec3);
  }
  setHeld(id: string, held: boolean): void {
    if (held) this.held.add(id);
    else this.held.delete(id);
  }
  releaseBall(id: string, velocity: Vec3): void {
    this.releases.push({ id, velocity: [...velocity] as Vec3 });
  }
  markTrackingLost(): void {
    this.trackingLost += 1;
  }
}

function hand(position: Vec3 | null, gripper: number): HandInput {
  return { position, orientation: [0, 0, 0, 1], gripper };
}

const FIRST = BALLS[0]!;
const SECOND = BALLS[1]!;

describe("BallPitSession grabbing", () => {
  it("grabs a ball with the left hand", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand(FIRST.start, CLOSED),
      right: null,
      dtSeconds: DT,
    });

    expect(update.grasped).toEqual([FIRST.id]);
    expect(task.isHeld(FIRST.id)).toBe(true);
  });

  it("grabs a ball with the right hand", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: null,
      right: hand(FIRST.start, CLOSED),
      dtSeconds: DT,
    });

    expect(update.grasped).toEqual([FIRST.id]);
  });

  it("lets both hands hold different balls at once", () => {
    // The whole reason this is not sortSession: two independent manipulators.
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand(FIRST.start, CLOSED),
      right: hand(SECOND.start, CLOSED),
      dtSeconds: DT,
    });

    expect(update.held.has(FIRST.id)).toBe(true);
    expect(update.held.has(SECOND.id)).toBe(true);
    expect(update.held.size).toBe(2);
  });

  it("never lets both hands claim the same ball", () => {
    // Two owners of one ball would fight over its position every frame.
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand(FIRST.start, CLOSED),
      right: hand(FIRST.start, CLOSED),
      dtSeconds: DT,
    });

    expect(update.held.size).toBe(1);
    expect(update.grasped).toEqual([FIRST.id]);
  });

  it("does not grab with an open hand", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand(FIRST.start, OPEN),
      right: null,
      dtSeconds: DT,
    });

    expect(update.grasped).toEqual([]);
    expect(update.held.size).toBe(0);
  });

  it("catches a ball anywhere within a whole-hand radius, not just at its center", () => {
    // These balls are grabbed with a fist, not pinched between fingertips.
    const task = new StubTask();
    const session = new BallPitSession(task);
    const offset: Vec3 = [
      FIRST.start[0] + PIT_GRASP_RADIUS_M * 0.8,
      FIRST.start[1],
      FIRST.start[2],
    ];

    expect(session.update({ left: hand(offset, CLOSED), right: null, dtSeconds: DT }).grasped)
      .toEqual([FIRST.id]);
  });

  it("does not catch a ball beyond the radius", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    const far: Vec3 = [FIRST.start[0] + PIT_GRASP_RADIUS_M * 3, FIRST.start[1], FIRST.start[2]];

    expect(session.update({ left: hand(far, CLOSED), right: null, dtSeconds: DT }).grasped)
      .toEqual([]);
  });
});

describe("BallPitSession carrying and throwing", () => {
  it("carries a held ball with the hand", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });

    const moved: Vec3 = [FIRST.start[0], FIRST.start[1] + 0.5, FIRST.start[2] + 0.4];
    session.update({ left: hand(moved, CLOSED), right: null, dtSeconds: DT });

    const at = task.ballPosition(FIRST.id);
    expect(at[1]).toBeCloseTo(moved[1], 6);
    expect(at[2]).toBeCloseTo(moved[2], 6);
  });

  it("releases with the hand's velocity, so a ball can be thrown", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    // Grab, then sweep the hand along +Y at a steady 2 m/s before opening.
    let position: Vec3 = [...FIRST.start] as Vec3;
    session.update({ left: hand(position, CLOSED), right: null, dtSeconds: DT });
    for (let i = 0; i < 8; i += 1) {
      position = [position[0], position[1] + 2 * DT, position[2]];
      session.update({ left: hand(position, CLOSED), right: null, dtSeconds: DT });
    }
    session.update({ left: hand(position, OPEN), right: null, dtSeconds: DT });

    expect(task.releases).toHaveLength(1);
    const { velocity } = task.releases[0]!;
    expect(velocity[1]).toBeGreaterThan(1);
    expect(velocity[1]).toBeLessThan(3);
  });

  it("drops a ball with no velocity when the hand was still", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });
    for (let i = 0; i < 6; i += 1) {
      session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });
    }
    session.update({ left: hand(FIRST.start, OPEN), right: null, dtSeconds: DT });

    const { velocity } = task.releases[0]!;
    expect(Math.hypot(...velocity)).toBeLessThan(0.05);
  });

  it("clamps an implausible throw rather than launching the ball out of the room", () => {
    // A tracking glitch can teleport a hand a long way in one frame.
    const task = new StubTask();
    const session = new BallPitSession(task);
    let position: Vec3 = [...FIRST.start] as Vec3;
    session.update({ left: hand(position, CLOSED), right: null, dtSeconds: DT });
    for (let i = 0; i < 6; i += 1) {
      position = [position[0], position[1] + 5, position[2]];
      session.update({ left: hand(position, CLOSED), right: null, dtSeconds: DT });
    }
    session.update({ left: hand(position, OPEN), right: null, dtSeconds: DT });

    expect(Math.hypot(...task.releases[0]!.velocity)).toBeLessThanOrEqual(8.0001);
  });
});

describe("BallPitSession tracking loss", () => {
  it("releases a held ball when the hand disappears", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });

    const lost = session.update({ left: hand(null, CLOSED), right: null, dtSeconds: DT });

    expect(lost.released).toEqual([FIRST.id]);
    expect(task.isHeld(FIRST.id)).toBe(false);
  });

  it("does not fling a ball on a tracking dropout", () => {
    // Stale samples from before the dropout must not become a throw.
    const task = new StubTask();
    const session = new BallPitSession(task);
    let position: Vec3 = [...FIRST.start] as Vec3;
    session.update({ left: hand(position, CLOSED), right: null, dtSeconds: DT });
    for (let i = 0; i < 6; i += 1) {
      position = [position[0], position[1] + 3 * DT, position[2]];
      session.update({ left: hand(position, CLOSED), right: null, dtSeconds: DT });
    }
    session.update({ left: hand(null, CLOSED), right: null, dtSeconds: DT });

    expect(Math.hypot(...task.releases[0]!.velocity)).toBe(0);
  });

  it("reports tracking loss once, not per frame", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    session.update({ left: hand(FIRST.start, OPEN), right: null, dtSeconds: DT });
    for (let i = 0; i < 5; i += 1) {
      session.update({ left: null, right: null, dtSeconds: DT });
    }

    expect(task.trackingLost).toBe(1);
  });

  it("keeps one hand working while the other is lost", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand(null, CLOSED),
      right: hand(SECOND.start, CLOSED),
      dtSeconds: DT,
    });

    expect(update.grasped).toEqual([SECOND.id]);
  });
});

describe("BallPitSession highlighting", () => {
  it("marks a ball within reach even before the hand closes", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand(FIRST.start, OPEN),
      right: null,
      dtSeconds: DT,
    });

    expect(update.reachable.has(FIRST.id)).toBe(true);
  });

  it("marks nothing when both hands are far from every ball", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);

    const update = session.update({
      left: hand([-5, -5, 1], OPEN),
      right: null,
      dtSeconds: DT,
    });

    expect(update.reachable.size).toBe(0);
  });

  it("always marks a held ball as reachable, so it does not flicker while carried", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });

    const carried: Vec3 = [FIRST.start[0], FIRST.start[1], FIRST.start[2] + 0.6];
    const update = session.update({ left: hand(carried, CLOSED), right: null, dtSeconds: DT });

    expect(update.reachable.has(FIRST.id)).toBe(true);
  });
});

describe("BallPitSession reset", () => {
  it("drops everything held", () => {
    const task = new StubTask();
    const session = new BallPitSession(task);
    session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });

    session.reset();
    task.held.clear();

    const update = session.update({ left: hand(FIRST.start, CLOSED), right: null, dtSeconds: DT });
    expect(update.grasped).toEqual([FIRST.id]);
  });
});
