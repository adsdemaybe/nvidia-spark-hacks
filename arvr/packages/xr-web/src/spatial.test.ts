import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { followTarget, heading, normalizeQuaternion, rotateVector } from "./spatial";

const FIXTURES = fileURLToPath(new URL("../../../fixtures/ar-xr/", import.meta.url));

const IDENTITY: [number, number, number, number] = [0, 0, 0, 1];
const YAW_90: [number, number, number, number] = [0, 0, Math.SQRT1_2, Math.SQRT1_2];

describe("rotateVector", () => {
  it("leaves a vector alone under the identity rotation", () => {
    expect(rotateVector(IDENTITY, [1, 2, 3])).toEqual([1, 2, 3]);
  });

  it("turns +X into +Y for a 90 degree yaw about Z", () => {
    const [x, y, z] = rotateVector(YAW_90, [1, 0, 0]);
    expect(x).toBeCloseTo(0, 12);
    expect(y).toBeCloseTo(1, 12);
    expect(z).toBeCloseTo(0, 12);
  });
});

describe("normalizeQuaternion", () => {
  it("accepts the 2-decimal quaternions the spec prints and returns unit norm", () => {
    const q = normalizeQuaternion([0.02, 0.71, 0.03, 0.7]);
    const norm = Math.hypot(...q);
    expect(norm).toBeCloseTo(1, 12);
  });

  it("rejects a quaternion that is not plausibly a rotation", () => {
    expect(() => normalizeQuaternion([1, 1, 0, 0])).toThrow(/unit/);
  });
});

describe("followTarget", () => {
  it("trails the human along their forward axis", () => {
    const target = followTarget({ position_m: [1, 2, 0], orientation_xyzw: IDENTITY }, 1);
    expect(target[0]).toBeCloseTo(0, 12);
    expect(target[1]).toBeCloseTo(2, 12);
  });

  it("rejects a non-positive distance", () => {
    expect(() =>
      followTarget({ position_m: [0, 0, 0], orientation_xyzw: IDENTITY }, 0),
    ).toThrow(/distance/);
  });

  it("agrees with the Python implementation on every fixture frame", () => {
    // This is the contract doing its job. The phone, this browser client and
    // the backend all have to land on the same numbers, or "same pipeline
    // regardless of device" (ar-xr-plan.md 5) is a slogan rather than a fact.
    const lines = readFileSync(`${FIXTURES}sample_follow.jsonl`, "utf8")
      .split("\n")
      .filter((l) => l.trim());

    expect(lines.length).toBeGreaterThanOrEqual(30);

    for (const line of lines) {
      const state = JSON.parse(line);
      const ours = followTarget(state.human_pose, state.desired_follow_distance_m);
      const theirs = state.follow_target.position_m;

      for (let i = 0; i < 3; i++) {
        expect(ours[i]).toBeCloseTo(theirs[i], 12);
      }
    }
  });
});

describe("heading", () => {
  it("is a unit vector", () => {
    const h = heading({ position_m: [0, 0, 0], orientation_xyzw: YAW_90 });
    expect(Math.hypot(...h)).toBeCloseTo(1, 12);
  });
});
