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
  readBothHands,
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

// Faking WebXR hand input takes three cooperating pieces: an XRHand whose
// `get(name)` hands back a joint space, an XRFrame that turns a joint space
// back into a pose, and a tag carried between them. The tag is what makes a
// swap detectable -- each fake hand stamps its own `originX` onto every
// joint it produces, so a pose's X coordinate says which input source it
// came from and no amount of correct-looking `handedness` fields can hide a
// hand landing in the wrong slot.
type FakeJointSpace = { joint: string; originX: number };

/** A hand whose joints all resolve, offset in X by `originX`. */
function trackedHand(handedness: "left" | "right", originX: number): XRInputSource {
  const hand = { get: (joint: string): FakeJointSpace => ({ joint, originX }) };
  return { handedness, hand } as unknown as XRInputSource;
}

/** A hand input source the runtime granted but is not currently resolving
 * joints for -- an occluded or out-of-view hand, which is the common case
 * mid-session, not an exotic one. */
function untrackedHand(handedness: "left" | "right"): XRInputSource {
  const hand = { get: (): undefined => undefined };
  return { handedness, hand } as unknown as XRInputSource;
}

function xrFrame(): XRFrame {
  const order = [...HAND_JOINTS, "palm"];
  return {
    getJointPose(space: FakeJointSpace) {
      // Spreading joints along Y keeps them distinct, so the thumb/index
      // aperture is a real nonzero distance rather than a degenerate zero.
      const y = order.indexOf(space.joint) * 0.01;
      return {
        transform: {
          position: { x: space.originX, y, z: 0 },
          orientation: { x: 0, y: 0, z: 0, w: 1 },
        },
        radius: 0.008,
      };
    },
  } as unknown as XRFrame;
}

const REF_SPACE = {} as XRReferenceSpace;
const LEFT_X = -0.25;
const RIGHT_X = 0.25;

describe("readBothHands", () => {
  it("returns both hands when both are tracked", () => {
    const pair = readBothHands(
      xrFrame(),
      session([trackedHand("left", LEFT_X), trackedHand("right", RIGHT_X)]),
      REF_SPACE,
    );

    expect(pair.left?.handedness).toBe("left");
    expect(pair.right?.handedness).toBe("right");
    expect(Object.keys(pair.left!.joints).length).toBeGreaterThan(0);
    expect(Object.keys(pair.right!.joints).length).toBeGreaterThan(0);
  });

  it("does not swap left and right, in either inputSources order", () => {
    // The highest-value assertion in this file. `handedness` alone cannot
    // catch a swap -- it is copied off the input source, so a swapped pair
    // still self-reports correctly. The per-source X offset can: it proves
    // the joints in the left slot were read from the left input source.
    const left = trackedHand("left", LEFT_X);
    const right = trackedHand("right", RIGHT_X);

    for (const sources of [
      [left, right],
      [right, left],
    ]) {
      const pair = readBothHands(xrFrame(), session(sources), REF_SPACE);
      expect(wristTarget(pair.left!)?.position[0]).toBe(LEFT_X);
      expect(wristTarget(pair.right!)?.position[0]).toBe(RIGHT_X);
    }
  });

  it("returns only the left hand when only it is tracked", () => {
    const pair = readBothHands(
      xrFrame(),
      session([trackedHand("left", LEFT_X)]),
      REF_SPACE,
    );

    expect(pair.left?.handedness).toBe("left");
    expect(wristTarget(pair.left!)?.position[0]).toBe(LEFT_X);
    expect(pair.right).toBeNull();
  });

  it("returns only the right hand when only it is tracked", () => {
    const pair = readBothHands(
      xrFrame(),
      session([trackedHand("right", RIGHT_X)]),
      REF_SPACE,
    );

    expect(pair.right?.handedness).toBe("right");
    expect(wristTarget(pair.right!)?.position[0]).toBe(RIGHT_X);
    expect(pair.left).toBeNull();
  });

  it("returns nulls, never a frame of zeros, when neither hand is tracked", () => {
    // Zeros would retarget into a real pose at the origin and would render a
    // hand collapsed at the play-space floor -- the exact failure readHand's
    // null contract exists to prevent, preserved across both hands.
    expect(readBothHands(xrFrame(), session([]), REF_SPACE)).toEqual({
      left: null,
      right: null,
    });

    const occluded = readBothHands(
      xrFrame(),
      session([untrackedHand("left"), untrackedHand("right")]),
      REF_SPACE,
    );
    expect(occluded).toEqual({ left: null, right: null });
  });

  it("keeps the tracked hand when the other one drops out", () => {
    const pair = readBothHands(
      xrFrame(),
      session([untrackedHand("left"), trackedHand("right", RIGHT_X)]),
      REF_SPACE,
    );

    expect(pair.left).toBeNull();
    expect(wristTarget(pair.right!)?.position[0]).toBe(RIGHT_X);
  });

  it("ignores an input source with no hand (e.g. a tracked controller)", () => {
    // A controller reports a handedness of its own, so an implementation
    // that bucketed by handedness before checking for `hand` would let a
    // held controller occupy the slot its hand belongs in.
    const controller = inputSource("left", false);
    const pair = readBothHands(
      xrFrame(),
      session([controller, trackedHand("right", RIGHT_X)]),
      REF_SPACE,
    );

    expect(pair.left).toBeNull();
    expect(pair.right?.handedness).toBe("right");
  });

  it("derives the same gripper reading readHand would", () => {
    // readBothHands must be a router, not a second implementation: if it
    // ever stopped delegating to readHand, the derived fields are where the
    // divergence would first show up.
    const pair = readBothHands(
      xrFrame(),
      session([trackedHand("right", RIGHT_X)]),
      REF_SPACE,
    );
    const hand = pair.right!;

    expect(hand.pinchApertureM).not.toBeNull();
    expect(hand.gripper).toBe(gripperFromAperture(hand.pinchApertureM));
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
