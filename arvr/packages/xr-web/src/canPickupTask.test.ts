import { describe, expect, it } from "vitest";
import { CanPickupTask } from "./canPickupTask";
import {
  CAN_HEIGHT_M,
  CAN_ID,
  CAN_START,
  LIFT_THRESHOLD_Z_M,
  TABLE_X_M,
  TABLE_Z_M,
} from "./canPickupLayout";
import type { Vec3 } from "./contracts";

const DT = 1 / 60;

/** Put the can's *base* at a given height, which is what the predicate reads. */
function centreForBaseZ(baseZ: number, x = CAN_START[0], y = CAN_START[1]): Vec3 {
  return [x, y, baseZ + CAN_HEIGHT_M / 2];
}

describe("CanPickupTask starting state", () => {
  it("starts resting on the table, not held, not lifted", () => {
    const task = new CanPickupTask();
    expect(task.canPosition()).toEqual(CAN_START);
    expect(task.isHeld).toBe(false);
    expect(task.wasLifted).toBe(false);
    expect(task.isSuccess).toBe(false);
  });

  it("starts with its base exactly on the tabletop", () => {
    expect(new CanPickupTask().baseZ).toBeCloseTo(TABLE_Z_M, 9);
  });
});

describe("CanPickupTask lift predicate", () => {
  it("does not count a can that is merely held at rest", () => {
    // Closing your hand around a can is not picking it up.
    const task = new CanPickupTask();
    task.setHeld(true);
    task.settle();

    expect(task.wasLifted).toBe(false);
    expect(task.isSuccess).toBe(false);
  });

  it("counts a held can lifted past the clearance", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();

    expect(task.wasLifted).toBe(true);
    expect(task.isSuccess).toBe(true);
    expect(task.events.some((e) => e.type === "lifted")).toBe(true);
  });

  it("does not count a lift a hair under the threshold", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M - 0.001));
    task.settle();

    expect(task.wasLifted).toBe(false);
  });

  it("measures the can's base, not its centre", () => {
    // A 12cm can standing on the table has its centre 6cm up; reading the
    // centre would call a standing can "lifted" at half the real clearance.
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition([CAN_START[0], CAN_START[1], LIFT_THRESHOLD_Z_M]);
    task.settle();

    expect(task.wasLifted).toBe(false);
  });

  it("does not count a can that rose without being held", () => {
    // Nothing should lift an unheld can, but if the physics ever did, that is
    // not a demonstration of a pickup.
    const task = new CanPickupTask();
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.2));
    task.settle();

    expect(task.wasLifted).toBe(false);
  });

  it("latches: setting the can back down does not un-demonstrate the pickup", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.05));
    task.settle();
    task.setHeld(false);
    for (let i = 0; i < 300; i += 1) task.step(DT);

    expect(task.wasLifted).toBe(true);
    expect(task.isSuccess).toBe(true);
  });

  it("announces success exactly once", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.1));
    for (let i = 0; i < 20; i += 1) task.settle();

    expect(task.events.filter((e) => e.type === "task_success")).toHaveLength(1);
  });
});

describe("CanPickupTask physics", () => {
  it("drops a released can back onto the table", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(TABLE_Z_M + 0.3));
    task.setHeld(false);

    for (let i = 0; i < 400; i += 1) task.step(DT);

    expect(task.baseZ).toBeCloseTo(TABLE_Z_M, 4);
  });

  it("does not move a held can", () => {
    const task = new CanPickupTask();
    const held = centreForBaseZ(TABLE_Z_M + 0.25);
    task.setHeld(true);
    task.setCanPosition(held);

    for (let i = 0; i < 120; i += 1) task.step(DT);

    expect(task.canPosition()).toEqual(held);
  });

  it("falls to the floor when released beyond the table edge", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(TABLE_Z_M + 0.3, TABLE_X_M[1] + 0.3));
    task.setHeld(false);

    for (let i = 0; i < 600; i += 1) task.step(DT);

    expect(task.baseZ).toBeCloseTo(0, 4);
  });

  it("never sinks below its resting height", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(TABLE_Z_M + 0.5));
    task.setHeld(false);

    for (let i = 0; i < 500; i += 1) {
      task.step(DT);
      expect(task.baseZ).toBeGreaterThanOrEqual(TABLE_Z_M - 1e-6);
    }
  });

  it("is deterministic", () => {
    const drop = (): Vec3 => {
      const task = new CanPickupTask();
      task.setHeld(true);
      task.setCanPosition(centreForBaseZ(TABLE_Z_M + 0.4));
      task.setHeld(false);
      for (let i = 0; i < 300; i += 1) task.step(DT);
      return task.canPosition();
    };
    expect(drop()).toEqual(drop());
  });
});

describe("CanPickupTask events", () => {
  it("records grasp start and end once each", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setHeld(true);
    task.setHeld(false);
    task.setHeld(false);

    expect(task.events.filter((e) => e.type === "grasp_start")).toHaveLength(1);
    expect(task.events.filter((e) => e.type === "grasp_end")).toHaveLength(1);
  });

  it("names the can on every object event", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();

    for (const event of task.events) {
      if (event.type === "tracking_lost") continue;
      expect(event.objectId).toBe(CAN_ID);
    }
  });

  it("records a placement after a real lift", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.05));
    task.settle();
    task.setHeld(false);
    for (let i = 0; i < 400; i += 1) task.step(DT);

    expect(task.events.filter((e) => e.type === "placed")).toHaveLength(1);
  });

  it("records no placement when the can was never lifted", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setHeld(false);
    for (let i = 0; i < 100; i += 1) task.step(DT);

    expect(task.events.some((e) => e.type === "placed")).toBe(false);
  });

  it("records tracking loss", () => {
    const task = new CanPickupTask();
    task.markTrackingLost();
    expect(task.events.filter((e) => e.type === "tracking_lost")).toHaveLength(1);
  });
});

describe("CanPickupTask reset", () => {
  it("puts the can back and clears the demonstration", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M + 0.1));
    task.settle();
    task.reset();

    expect(task.canPosition()).toEqual(CAN_START);
    expect(task.wasLifted).toBe(false);
    expect(task.isSuccess).toBe(false);
    expect(task.isHeld).toBe(false);
    expect(task.events).toHaveLength(0);
  });

  it("can demonstrate a second pickup after a reset", () => {
    const task = new CanPickupTask();
    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();
    task.reset();

    task.setHeld(true);
    task.setCanPosition(centreForBaseZ(LIFT_THRESHOLD_Z_M));
    task.settle();

    expect(task.isSuccess).toBe(true);
    expect(task.events.filter((e) => e.type === "task_success")).toHaveLength(1);
  });
});

describe("object states", () => {
  it("reports the can's live position for the episode record", () => {
    const task = new CanPickupTask();
    const moved = centreForBaseZ(TABLE_Z_M + 0.2);
    task.setHeld(true);
    task.setCanPosition(moved);

    const states = task.objectStates();
    expect(states).toHaveLength(1);
    expect(states[0]!.id).toBe(CAN_ID);
    expect(states[0]!.position_m).toEqual(moved);
  });
});
