import { describe, expect, it } from "vitest";
import { REACH_M, SHOULDER_HEIGHT, reachStatus } from "./arm";
import type { Vec3 } from "./contracts";

// Matches ROBOT_BASE in ar-sim/scene_mjcf.py.
const BASE: Vec3 = [0.15, -0.7, 0];
const CUBE: Vec3 = [0.3, 0.1, 0.78];
// The release point above the bin, not the bin floor -- the arm lets go at
// RELEASE_HEIGHT and gravity does the rest (ar-sim/director.py).
const BIN_RELEASE: Vec3 = [0.6, -0.7, 0.34];

describe("reachStatus", () => {
  it("says the cube on the table is reachable", () => {
    // If this ever fails, the arm cannot do the task the demo claims it does.
    expect(reachStatus(BASE, CUBE).reachable).toBe(true);
  });

  it("says the release point above the bin is reachable", () => {
    expect(reachStatus(BASE, BIN_RELEASE).reachable).toBe(true);
  });

  it("says the bin floor is out of reach, which is why the cube is dropped", () => {
    expect(reachStatus(BASE, [0.6, -0.7, 0.05]).reachable).toBe(false);
  });

  it("says a point across the room is not", () => {
    expect(reachStatus(BASE, [4, 4, 1]).reachable).toBe(false);
  });

  it("measures from the shoulder, not the floor", () => {
    const straightUp: Vec3 = [BASE[0], BASE[1], BASE[2] + SHOULDER_HEIGHT + REACH_M - 0.01];
    expect(reachStatus(BASE, straightUp).reachable).toBe(true);

    const justBeyond: Vec3 = [BASE[0], BASE[1], BASE[2] + SHOULDER_HEIGHT + REACH_M + 0.05];
    expect(reachStatus(BASE, justBeyond).reachable).toBe(false);
  });

  it("reports how much margin is left, so PLACE can warn before it fails", () => {
    const status = reachStatus(BASE, CUBE);
    expect(status.margin).toBeGreaterThan(0);
    expect(status.margin).toBeCloseTo(REACH_M - status.distance, 12);
  });
});
