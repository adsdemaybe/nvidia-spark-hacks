import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockHandProvider, toHandFrame, toWebxrJoint, type StructHandFrame } from "./mockHand";

const structJoint = (p: [number, number, number]) => ({
  position_m: p,
  orientation_xyzw: [0, 0, 0, 1] as [number, number, number, number],
});

describe("toWebxrJoint", () => {
  it("converts struct_world -> WebXR space via the same basis change as adapter.ts", () => {
    // STRUCT +X (forward) should land on WebXR -Z; STRUCT +Z (up) on WebXR +Y.
    const forward = toWebxrJoint(structJoint([1, 0, 0]));
    expect(forward.position).toEqual([0, 0, -1]);

    const up = toWebxrJoint(structJoint([0, 0, 1]));
    expect(up.position).toEqual([0, 1, 0]);
  });
});

describe("toHandFrame", () => {
  const frame: StructHandFrame = {
    timestamp_ns: 0,
    hand: "right",
    joints: {
      wrist: structJoint([0.3, 0, 0.5]),
      "thumb-tip": structJoint([0.31, 0, 0.48]),
      "index-finger-tip": structJoint([0.29, 0, 0.48]),
    },
  };

  it("preserves handedness", () => {
    expect(toHandFrame(frame).handedness).toBe("right");
  });

  it("derives a finite pinch aperture and gripper value from tip distance", () => {
    const result = toHandFrame(frame);
    expect(result.pinchApertureM).not.toBeNull();
    expect(Number.isFinite(result.pinchApertureM)).toBe(true);
    expect(result.gripper).toBeGreaterThanOrEqual(0);
    expect(result.gripper).toBeLessThanOrEqual(1);
  });

  it("reports null aperture when a fingertip is untracked", () => {
    const partial: StructHandFrame = {
      timestamp_ns: 0,
      hand: "right",
      joints: { wrist: structJoint([0, 0, 0]) },
    };
    expect(toHandFrame(partial).pinchApertureM).toBeNull();
  });

  it("converts every joint present, not a hardcoded subset", () => {
    const result = toHandFrame(frame);
    expect(Object.keys(result.joints).sort()).toEqual(
      ["index-finger-tip", "thumb-tip", "wrist"].sort(),
    );
  });
});

function structFrame(hand: "left" | "right" = "right"): StructHandFrame {
  return {
    timestamp_ns: 0,
    hand,
    joints: { wrist: structJoint([0, 0, 0]) },
  };
}

describe("MockHandProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loops through frames on a timer and calls back with converted HandFrames", () => {
    const provider = new MockHandProvider(
      [toHandFrame(structFrame("left")), toHandFrame(structFrame("right"))],
      30,
    );
    const seen: string[] = [];
    provider.start((hand) => seen.push(hand.handedness));

    const period = 1000 / 30;
    vi.advanceTimersByTime(period);
    vi.advanceTimersByTime(period);
    vi.advanceTimersByTime(period);

    provider.stop();
    expect(seen).toEqual(["left", "right", "left"]);
  });

  it("stop() halts further callbacks", () => {
    const provider = new MockHandProvider([toHandFrame(structFrame())], 30);
    const seen: string[] = [];
    provider.start((hand) => seen.push(hand.handedness));
    provider.stop();
    vi.advanceTimersByTime(1000);
    expect(seen).toEqual([]);
  });
});
