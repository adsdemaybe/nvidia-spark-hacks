import { describe, expect, it } from "vitest";
import {
  BALLS,
  BALL_RADIUS_M,
  BASKETS,
  BASKET_INTERIOR_M,
  TABLE_Z_M,
  basketFor,
} from "./sortLayout";
import { SortTask, insideBasket } from "./sortTask";
import type { Vec3 } from "./contracts";

const RED_BASKET = basketFor("red");
const BLUE_BASKET = basketFor("blue");

/** A point just above a basket's floor, inset from its walls. */
function overBasket(basket: typeof RED_BASKET, dx = 0, dy = 0): Vec3 {
  return [
    basket.center[0] + dx,
    basket.center[1] + dy,
    basket.center[2] + BALL_RADIUS_M,
  ];
}

describe("insideBasket", () => {
  it("accepts a ball resting in the middle of the basket", () => {
    expect(insideBasket(overBasket(RED_BASKET), RED_BASKET)).toBe(true);
  });

  it("rejects a ball outside the interior footprint", () => {
    const justOutside = overBasket(RED_BASKET, 0, BASKET_INTERIOR_M);
    expect(insideBasket(justOutside, RED_BASKET)).toBe(false);
  });

  it("rejects a ball held high above the basket", () => {
    // Carrying a ball over a basket is not placing it in one -- otherwise
    // simply passing overhead would score.
    const above: Vec3 = [RED_BASKET.center[0], RED_BASKET.center[1], TABLE_Z_M + 0.4];
    expect(insideBasket(above, RED_BASKET)).toBe(false);
  });

  it("rejects a ball below the basket floor", () => {
    const below: Vec3 = [RED_BASKET.center[0], RED_BASKET.center[1], TABLE_Z_M - 0.2];
    expect(insideBasket(below, RED_BASKET)).toBe(false);
  });

  it("does not confuse the two baskets", () => {
    expect(insideBasket(overBasket(RED_BASKET), BLUE_BASKET)).toBe(false);
    expect(insideBasket(overBasket(BLUE_BASKET), RED_BASKET)).toBe(false);
  });
});

describe("SortTask scoring", () => {
  it("starts with nothing sorted and nothing complete", () => {
    const task = new SortTask();
    expect(task.score("red")).toBe(0);
    expect(task.score("blue")).toBe(0);
    expect(task.isComplete).toBe(false);
  });

  it("scores a red ball placed in the red basket", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", overBasket(RED_BASKET));
    task.settle();

    expect(task.score("red")).toBe(1);
    expect(task.containerOf("red_ball_0")).toBe("red_basket");
  });

  it("counts a red ball in the blue basket as wrong, not as a point", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", overBasket(BLUE_BASKET));
    task.settle();

    expect(task.score("red")).toBe(0);
    expect(task.score("blue")).toBe(0);
    expect(task.containerOf("red_ball_0")).toBe("blue_basket");
    expect(task.events.some((e) => e.type === "wrong_basket")).toBe(true);
  });

  it("does not double-count a ball that stays in its basket", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", overBasket(RED_BASKET));
    task.settle();
    task.settle();
    task.settle();

    expect(task.score("red")).toBe(1);
    expect(task.events.filter((e) => e.type === "ball_enter_basket")).toHaveLength(1);
  });

  it("does not score a ball that is still held inside the basket", () => {
    // Reaching into a basket while holding a ball is not placing it there.
    const task = new SortTask();
    task.setHeld("red_ball_0", true);
    task.setBallPosition("red_ball_0", overBasket(RED_BASKET));
    task.settle();

    expect(task.score("red")).toBe(0);
    expect(task.containerOf("red_ball_0")).toBeNull();

    task.setHeld("red_ball_0", false);
    task.settle();

    expect(task.score("red")).toBe(1);
  });

  it("does not score a ball carried through a basket on the way past", () => {
    // The path from the table to anywhere beyond a basket passes through
    // its volume. Scoring on the way through, then again on landing, is
    // how sort_complete fired twice.
    const task = new SortTask();
    task.setHeld("red_ball_0", true);
    for (let i = 0; i <= 10; i += 1) {
      const t = i / 10;
      task.setBallPosition("red_ball_0", [
        RED_BASKET.center[0],
        RED_BASKET.center[1],
        RED_BASKET.center[2] + t * 0.2,
      ]);
      task.settle();
    }

    expect(task.events.filter((e) => e.type === "ball_enter_basket")).toHaveLength(0);
  });

  it("takes the point back when a ball is lifted out again", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", overBasket(RED_BASKET));
    task.settle();
    expect(task.score("red")).toBe(1);

    task.setBallPosition("red_ball_0", [0.2, 0, TABLE_Z_M + 0.3]);
    task.settle();

    expect(task.score("red")).toBe(0);
    expect(task.containerOf("red_ball_0")).toBeNull();
  });

  it("is complete only when every ball is in its matching basket", () => {
    const task = new SortTask();
    for (const ball of BALLS) {
      task.setBallPosition(ball.id, overBasket(basketFor(ball.color)));
      task.settle();
    }

    expect(task.isComplete).toBe(true);
    expect(task.score("red")).toBe(3);
    expect(task.score("blue")).toBe(3);
    expect(task.events.filter((e) => e.type === "sort_complete")).toHaveLength(1);
  });

  it("is not complete when one ball sits in the wrong basket", () => {
    const task = new SortTask();
    for (const ball of BALLS) {
      const basket = ball.id === "blue_ball_2" ? RED_BASKET : basketFor(ball.color);
      task.setBallPosition(ball.id, overBasket(basket));
      task.settle();
    }

    expect(task.isComplete).toBe(false);
    expect(task.events.some((e) => e.type === "sort_complete")).toBe(false);
  });

  it("emits sort_complete once, not on every settle after completion", () => {
    const task = new SortTask();
    for (const ball of BALLS) {
      task.setBallPosition(ball.id, overBasket(basketFor(ball.color)));
    }
    task.settle();
    task.settle();

    expect(task.events.filter((e) => e.type === "sort_complete")).toHaveLength(1);
  });
});

describe("SortTask ball physics", () => {
  it("drops a released ball onto the table and stops it there", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", [0.2, 0, TABLE_Z_M + 0.3]);

    for (let i = 0; i < 400; i += 1) task.step(1 / 72);

    const [, , z] = task.ballPosition("red_ball_0");
    expect(z).toBeCloseTo(TABLE_Z_M + BALL_RADIUS_M, 3);
  });

  it("does not move a ball that is held", () => {
    const task = new SortTask();
    const held: Vec3 = [0.2, 0, TABLE_Z_M + 0.3];
    task.setBallPosition("red_ball_0", held);
    task.setHeld("red_ball_0", true);

    for (let i = 0; i < 100; i += 1) task.step(1 / 72);

    expect(task.ballPosition("red_ball_0")).toEqual(held);
  });

  it("lands a released ball on the basket floor, not through it", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", [RED_BASKET.center[0], RED_BASKET.center[1], TABLE_Z_M + 0.35]);

    for (let i = 0; i < 400; i += 1) task.step(1 / 72);

    const position = task.ballPosition("red_ball_0");
    expect(position[2]).toBeGreaterThanOrEqual(RED_BASKET.center[2]);
    expect(insideBasket(position, RED_BASKET)).toBe(true);
  });

  it("is deterministic -- the same drop gives the same resting place", () => {
    const drop = (): Vec3 => {
      const task = new SortTask();
      task.setBallPosition("blue_ball_1", [0.22, -0.03, TABLE_Z_M + 0.25]);
      for (let i = 0; i < 300; i += 1) task.step(1 / 90);
      return task.ballPosition("blue_ball_1");
    };
    expect(drop()).toEqual(drop());
  });
});

describe("SortTask events", () => {
  it("records a grasp start and end around a pickup", () => {
    const task = new SortTask();
    task.setHeld("red_ball_0", true);
    task.setHeld("red_ball_0", false);

    const types = task.events.map((e) => e.type);
    expect(types).toContain("grasp_start");
    expect(types).toContain("grasp_end");
  });

  it("does not emit a second grasp_start for a ball already held", () => {
    const task = new SortTask();
    task.setHeld("red_ball_0", true);
    task.setHeld("red_ball_0", true);

    expect(task.events.filter((e) => e.type === "grasp_start")).toHaveLength(1);
  });

  it("stamps every event with the object it concerns", () => {
    const task = new SortTask();
    task.setBallPosition("blue_ball_0", overBasket(BLUE_BASKET));
    task.settle();

    const entered = task.events.find((e) => e.type === "ball_enter_basket");
    expect(entered?.objectId).toBe("blue_ball_0");
    expect(entered?.containerId).toBe("blue_basket");
  });
});

describe("SortTask object states", () => {
  it("reports every ball and basket for the episode record", () => {
    const ids = new SortTask().objectStates().map((state) => state.id);
    for (const ball of BALLS) expect(ids).toContain(ball.id);
    for (const basket of BASKETS) expect(ids).toContain(basket.id);
  });

  it("reports live ball positions, not their starting ones", () => {
    const task = new SortTask();
    const moved: Vec3 = [0.3, 0.1, 0.4];
    task.setBallPosition("red_ball_1", moved);

    const state = task.objectStates().find((s) => s.id === "red_ball_1");
    expect(state?.position_m).toEqual(moved);
  });

  it("resets every ball to its start", () => {
    const task = new SortTask();
    task.setBallPosition("red_ball_0", overBasket(RED_BASKET));
    task.settle();
    task.reset();

    expect(task.score("red")).toBe(0);
    expect(task.events).toHaveLength(0);
    expect(task.ballPosition("red_ball_0")).toEqual(BALLS[0]!.start);
  });
});
