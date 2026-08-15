import { describe, expect, it } from "vitest";
import {
  HAND_JOINTS,
  PINCH_CLOSED_M,
  PINCH_ENGAGE,
  PINCH_OPEN_M,
  PINCH_RELEASE,
  PinchLatch,
  distance,
  gripperFromAperture,
  pinchPoint,
  pokePoint,
  preferredInputSource,
  wristTarget,
  type HandFrame,
  type JointPose,
} from "./hands";

function inputSource(handedness: "left" | "right", hasHand = true): XRInputSource {
  return {
    handedness,
    hand: hasHand ? ({} as XRHand) : undefined,
  } as unknown as XRInputSource;
}

function session(sources: XRInputSource[]): XRSession {
  return { inputSources: sources } as unknown as XRSession;
}

const joint = (p: [number, number, number]): JointPose => ({
  position: p,
  orientation: [0, 0, 0, 1],
  radius: null,
});

describe("HAND_JOINTS", () => {
  it("covers the 25 WebXR-named joints", () => {
    expect(HAND_JOINTS).toHaveLength(25);
    expect(HAND_JOINTS).toContain("wrist");
    expect(HAND_JOINTS).toContain("thumb-tip");
    expect(HAND_JOINTS).toContain("index-finger-tip");
  });

  it("has no duplicates", () => {
    expect(new Set(HAND_JOINTS).size).toBe(HAND_JOINTS.length);
  });
});

describe("gripperFromAperture", () => {
  it("reads fully closed when the tips are touching", () => {
    expect(gripperFromAperture(PINCH_CLOSED_M)).toBe(1);
  });

  it("reads fully open at a relaxed pinch", () => {
    expect(gripperFromAperture(PINCH_OPEN_M)).toBe(0);
  });

  it("saturates rather than asking for a grip the robot cannot make", () => {
    expect(gripperFromAperture(0.001)).toBe(1);
    expect(gripperFromAperture(0.5)).toBe(0);
  });

  it("is monotonic between the two", () => {
    const mid = gripperFromAperture((PINCH_CLOSED_M + PINCH_OPEN_M) / 2);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(1);
  });

  it("treats an untracked pinch as open, not as a grab", () => {
    // A missing hand must never close the gripper -- that would grasp on a
    // tracking dropout.
    expect(gripperFromAperture(null)).toBe(0);
    expect(gripperFromAperture(NaN)).toBe(0);
  });
});

describe("distance", () => {
  it("measures between joint positions", () => {
    expect(distance(joint([0, 0, 0]), joint([0, 0, 0.03]))).toBeCloseTo(0.03, 12);
  });
});

describe("preferredInputSource", () => {
  it("prefers the right hand when both are tracked", () => {
    const left = inputSource("left");
    const right = inputSource("right");
    expect(preferredInputSource(session([left, right]))).toBe(right);
    // Order in inputSources shouldn't matter.
    expect(preferredInputSource(session([right, left]))).toBe(right);
  });

  it("falls back to the left hand when only it is tracked", () => {
    const left = inputSource("left");
    expect(preferredInputSource(session([left]))).toBe(left);
  });

  it("ignores input sources with no hand (e.g. a tracked controller)", () => {
    const controller = inputSource("right", false);
    const hand = inputSource("left");
    expect(preferredInputSource(session([controller, hand]))).toBe(hand);
  });

  it("returns undefined when nothing is tracked", () => {
    expect(preferredInputSource(session([]))).toBeUndefined();
    expect(preferredInputSource(session([inputSource("right", false)]))).toBeUndefined();
  });
});

describe("wristTarget", () => {
  it("returns the wrist pose, which is the 6-DoF end-effector target", () => {
    const hand: HandFrame = {
      handedness: "right",
      joints: { wrist: joint([0.1, 1.2, -0.3]) },
      pinchApertureM: null,
      gripper: 0,
    };

    expect(wristTarget(hand)?.position).toEqual([0.1, 1.2, -0.3]);
  });

  it("returns null when the wrist was not tracked", () => {
    const hand: HandFrame = {
      handedness: "left",
      joints: {},
      pinchApertureM: null,
      gripper: 0,
    };

    expect(wristTarget(hand)).toBeNull();
  });
});

function pinchHand(gripper: number, joints: Record<string, JointPose> = {}): HandFrame {
  return { handedness: "right", joints, pinchApertureM: null, gripper };
}

describe("pinchPoint", () => {
  it("is the midpoint between the thumb and index tips", () => {
    const hand = pinchHand(1, {
      "thumb-tip": joint([0, 1, 0]),
      "index-finger-tip": joint([0.02, 1.04, -0.1]),
    });

    expect(pinchPoint(hand)).toEqual([0.01, 1.02, -0.05]);
  });

  it("returns null when either tip is untracked, rather than guessing", () => {
    expect(pinchPoint(pinchHand(1, { "thumb-tip": joint([0, 1, 0]) }))).toBeNull();
    expect(pinchPoint(pinchHand(1, {}))).toBeNull();
  });
});

describe("pokePoint", () => {
  it("is the index fingertip", () => {
    const hand = pinchHand(0, { "index-finger-tip": joint([0.1, 1.1, -0.2]) });
    expect(pokePoint(hand)).toEqual([0.1, 1.1, -0.2]);
  });

  it("returns null when the index tip is untracked", () => {
    expect(pokePoint(pinchHand(0, {}))).toBeNull();
  });
});

describe("PinchLatch", () => {
  it("fires once on the frame the pinch closes", () => {
    const latch = new PinchLatch();
    expect(latch.update(pinchHand(0.2))).toBe(false);
    expect(latch.update(pinchHand(PINCH_ENGAGE))).toBe(true);
  });

  it("does not fire again while the pinch is held", () => {
    const latch = new PinchLatch();
    latch.update(pinchHand(1));
    expect(latch.update(pinchHand(1))).toBe(false);
    expect(latch.update(pinchHand(0.95))).toBe(false);
  });

  it("stays engaged between the release and engage thresholds", () => {
    // The hysteresis band is the whole point: a gripper reading that dithers
    // around one threshold would place a burst of calibration anchors.
    const latch = new PinchLatch();
    latch.update(pinchHand(1));
    latch.update(pinchHand((PINCH_ENGAGE + PINCH_RELEASE) / 2));
    expect(latch.isEngaged).toBe(true);
    expect(latch.update(pinchHand(1))).toBe(false);
  });

  it("re-arms once the hand opens past the release threshold", () => {
    const latch = new PinchLatch();
    latch.update(pinchHand(1));
    latch.update(pinchHand(PINCH_RELEASE - 0.01));
    expect(latch.isEngaged).toBe(false);
    expect(latch.update(pinchHand(1))).toBe(true);
  });

  it("treats a lost hand as a release, not a held pinch", () => {
    const latch = new PinchLatch();
    latch.update(pinchHand(1));
    expect(latch.update(null)).toBe(false);
    expect(latch.isEngaged).toBe(false);
    expect(latch.update(pinchHand(1))).toBe(true);
  });
});
