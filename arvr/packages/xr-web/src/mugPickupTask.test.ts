import { describe, expect, it } from "vitest";
import { MugPickupTask } from "./mugPickupTask";
import {
  LIFT_THRESHOLD_Z_M,
  MUG_ID,
  MUG_START,
  TABLE_DROP_M,
  TABLE_X_M,
  TABLE_Z_M,
} from "./mugPickupLayout";
import type { Vec3 } from "./contracts";

const DT = 1 / 60;

/** Put the mug's *base* at a given height, which is what the predicate reads. */
function centreForBaseZ(baseZ: number, x = MUG_START[0], y = MUG_START[1]): Vec3 {
  // The asset's origin is the centre of its base, so the tracked position is
  // the base directly.
  return [x, y, baseZ];
}

describe("MugPickupTask starting state", () => {
  it("starts resting on the table, not held, not lifted", () => {
    const task = new MugPickupTask();
    expect(task.mugPosition()).toEqual(MUG_START);
    expect(task.isHeld).toBe(false);
    expect(task.wasLifted).toBe(false);
    expect(task.isSuccess).toBe(false);
  });

  it("starts with its base exactly on the tabletop", () => {
    expect(new MugPickupTask().baseZ).toBeCloseTo(TABLE_Z_M, 9);
  });
});

describe("MugPickupTask lift predicate", () => {
  it("does not count a mug that is merely held at rest", () => {
    // Closing your hand around a mug is not picking it up.
    const task = new MugPickupTask();
    task.setHeld(true);
    task.settle();

    expect(task.wasLifted).toBe(false);
    expect(task.isSuccess).toBe(false);
  });

  it("counts a held can lifted past the clearance", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();

    expect(task.wasLifted).toBe(true);
    expect(task.isSuccess).toBe(true);
    expect(task.events.some((e) => e.type === "lifted")).toBe(true);
  });

  it("does not count a lift a hair under the threshold", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M - 0.001));
    task.settle();

    expect(task.wasLifted).toBe(false);
  });

  it("treats the tracked position as the base, matching the asset's origin", () => {
    // The generated mug's local origin is the centre of its base. A stand-in
    // that was centre-origin would need half-height arithmetic here; getting
    // that wrong reads a mug standing on the table as already lifted.
    const task = new MugPickupTask();
    expect(task.baseZ).toBeCloseTo(TABLE_Z_M, 9);
    expect(task.mugPosition()[2]).toBeCloseTo(task.baseZ, 9);
  });

  it("does not count a mug that rose without being held", () => {
    // Nothing should lift an unheld can, but if the physics ever did, that is
    // not a demonstration of a pickup.
    const task = new MugPickupTask();
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.2));
    task.settle();

    expect(task.wasLifted).toBe(false);
  });

  it("latches: setting the mug back down does not un-demonstrate the pickup", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.05));
    task.settle();
    task.setHeld(false);
    for (let i = 0; i < 300; i += 1) task.step(DT);

    expect(task.wasLifted).toBe(true);
    expect(task.isSuccess).toBe(true);
  });

  it("announces success exactly once", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.1));
    for (let i = 0; i < 20; i += 1) task.settle();

    expect(task.events.filter((e) => e.type === "task_success")).toHaveLength(1);
  });
});

describe("MugPickupTask physics", () => {
  it("drops a released can back onto the table", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(TABLE_Z_M + 0.3));
    task.setHeld(false);

    for (let i = 0; i < 400; i += 1) task.step(DT);

    expect(task.baseZ).toBeCloseTo(TABLE_Z_M, 4);
  });

  it("does not move a held can", () => {
    const task = new MugPickupTask();
    const held = centreForBaseZ(TABLE_Z_M + 0.25);
    task.setHeld(true);
    task.setMugPosition(held);

    for (let i = 0; i < 120; i += 1) task.step(DT);

    expect(task.mugPosition()).toEqual(held);
  });

  it("falls to the floor below when released beyond the table edge", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(TABLE_Z_M + 0.3, TABLE_X_M[1] + 0.3));
    task.setHeld(false);

    for (let i = 0; i < 600; i += 1) task.step(DT);

    // The tabletop is the origin plane, so the floor is a table's height
    // BELOW zero -- not at zero, which is where it sat when the tabletop
    // itself was at +0.14.
    expect(task.baseZ).toBeCloseTo(-TABLE_DROP_M, 3);
  });

  it("never sinks below its resting height", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(TABLE_Z_M + 0.5));
    task.setHeld(false);

    for (let i = 0; i < 500; i += 1) {
      task.step(DT);
      expect(task.baseZ).toBeGreaterThanOrEqual(TABLE_Z_M - 1e-6);
    }
  });

  it("is deterministic", () => {
    const drop = (): Vec3 => {
      const task = new MugPickupTask();
      task.setHeld(true);
      task.setMugPosition(centreForBaseZ(TABLE_Z_M + 0.4));
      task.setHeld(false);
      for (let i = 0; i < 300; i += 1) task.step(DT);
      return task.mugPosition();
    };
    expect(drop()).toEqual(drop());
  });
});

describe("MugPickupTask events", () => {
  it("records grasp start and end once each", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setHeld(true);
    task.setHeld(false);
    task.setHeld(false);

    expect(task.events.filter((e) => e.type === "grasp_start")).toHaveLength(1);
    expect(task.events.filter((e) => e.type === "grasp_end")).toHaveLength(1);
  });

  it("names the mug on every object event", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();

    for (const event of task.events) {
      if (event.type === "tracking_lost") continue;
      expect(event.objectId).toBe(MUG_ID);
    }
  });

  it("records a placement after a real lift", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.05));
    task.settle();
    task.setHeld(false);
    for (let i = 0; i < 400; i += 1) task.step(DT);

    expect(task.events.filter((e) => e.type === "placed")).toHaveLength(1);
  });

  it("records no placement when the mug was never lifted", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setHeld(false);
    for (let i = 0; i < 100; i += 1) task.step(DT);

    expect(task.events.some((e) => e.type === "placed")).toBe(false);
  });

  it("records tracking loss", () => {
    const task = new MugPickupTask();
    task.markTrackingLost();
    expect(task.events.filter((e) => e.type === "tracking_lost")).toHaveLength(1);
  });
});

describe("MugPickupTask reset", () => {
  it("puts the mug back and clears the demonstration", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.1));
    task.settle();
    task.reset();

    expect(task.mugPosition()).toEqual(MUG_START);
    expect(task.wasLifted).toBe(false);
    expect(task.isSuccess).toBe(false);
    expect(task.isHeld).toBe(false);
    expect(task.events).toHaveLength(0);
  });

  it("can demonstrate a second pickup after a reset", () => {
    const task = new MugPickupTask();
    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();
    task.reset();

    task.setHeld(true);
    task.setMugPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();

    expect(task.isSuccess).toBe(true);
    expect(task.events.filter((e) => e.type === "task_success")).toHaveLength(1);
  });
});

describe("object states", () => {
  it("reports the mug's live position for the episode record", () => {
    const task = new MugPickupTask();
    const moved = centreForBaseZ(TABLE_Z_M + 0.2);
    task.setHeld(true);
    task.setMugPosition(moved);

    const states = task.objectStates();
    expect(states).toHaveLength(1);
    expect(states[0]!.id).toBe(MUG_ID);
    expect(states[0]!.position_m).toEqual(moved);
  });
});
