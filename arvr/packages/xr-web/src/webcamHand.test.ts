import { describe, expect, it } from "vitest";
import { PINCH_CLOSED_M, PINCH_OPEN_M } from "./hands";
import {
  DEFAULT_CONTROL_VOLUME,
  MEDIAPIPE_LANDMARK_TO_JOINT,
  PinchHysteresis,
  emaSmooth,
  imageToControlSpace,
  mediapipeResultToHandFrame,
  palmOrientation,
  preferredHandIndex,
  worldLandmarksToStructJoints,
  type MediapipeHandResult,
  type MediapipeLandmark,
} from "./webcamHand";

function lm(x: number, y: number, z: number): MediapipeLandmark {
  return { x, y, z };
}

// A synthetic, anatomically-plausible open right hand: wrist at the world
// origin, fingers spread along +x with a mild spread in y/z so the palm
// basis is well-conditioned (not degenerate). Index order matches
// MediaPipe's fixed 21-point layout (WRIST, THUMB x4, INDEX x4, MIDDLE x4,
// RING x4, PINKY x4).
const WORLD_LANDMARKS_OPEN: MediapipeLandmark[] = [
  lm(0, 0, 0), // wrist
  lm(-0.02, 0.01, 0.01), lm(-0.03, 0.03, 0.02), lm(-0.03, 0.05, 0.03), lm(-0.03, 0.07, 0.03), // thumb
  lm(0.02, 0.02, -0.01), lm(0.02, 0.06, -0.01), lm(0.02, 0.08, -0.01), lm(0.02, 0.1, -0.01), // index
  lm(0.0, 0.02, -0.01), lm(0.0, 0.07, -0.01), lm(0.0, 0.09, -0.01), lm(0.0, 0.11, -0.01), // middle
  lm(-0.02, 0.02, -0.01), lm(-0.02, 0.06, -0.01), lm(-0.02, 0.08, -0.01), lm(-0.02, 0.1, -0.01), // ring
  lm(-0.04, 0.02, -0.01), lm(-0.04, 0.05, -0.01), lm(-0.04, 0.07, -0.01), lm(-0.04, 0.09, -0.01), // pinky
];

// Matching image-space landmarks (normalized [0,1]); values don't need to be
// geometrically consistent with the world landmarks for these tests.
const IMAGE_LANDMARKS_OPEN: MediapipeLandmark[] = WORLD_LANDMARKS_OPEN.map((_, i) =>
  lm(0.5, 0.5, i === 0 ? -0.05 : 0.0),
);

function syntheticResult(overrides: Partial<MediapipeHandResult> = {}): MediapipeHandResult {
  return {
    landmarks: [IMAGE_LANDMARKS_OPEN],
    worldLandmarks: [WORLD_LANDMARKS_OPEN],
    handedness: [[{ categoryName: "Right", score: 0.98 }]],
    ...overrides,
  };
}

describe("MEDIAPIPE_LANDMARK_TO_JOINT", () => {
  it("covers all 21 MediaPipe landmark indices", () => {
    expect(MEDIAPIPE_LANDMARK_TO_JOINT).toHaveLength(21);
    expect(MEDIAPIPE_LANDMARK_TO_JOINT[0]).toBe("wrist");
  });

  it("never maps to a non-thumb metacarpal or palm -- MediaPipe has no such landmark", () => {
    const names = new Set(MEDIAPIPE_LANDMARK_TO_JOINT);
    for (const finger of ["index", "middle", "ring", "pinky"]) {
      expect(names.has(`${finger}-finger-metacarpal`)).toBe(false);
    }
    expect(names.has("palm")).toBe(false);
  });

  it("maps the thumb 1:1 (both sides have 4 points)", () => {
    expect(MEDIAPIPE_LANDMARK_TO_JOINT.slice(1, 5)).toEqual([
      "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
    ]);
  });
});

describe("imageToControlSpace", () => {
  it("maps image x linearly across the configured x bounds", () => {
    const bounds = DEFAULT_CONTROL_VOLUME;
    expect(imageToControlSpace(0, 0.5, 0, bounds)[0]).toBeCloseTo(bounds.xMin, 6);
    expect(imageToControlSpace(1, 0.5, 0, bounds)[0]).toBeCloseTo(bounds.xMax, 6);
  });

  it("inverts image y so upward hand motion raises the anchor", () => {
    const bounds = DEFAULT_CONTROL_VOLUME;
    const top = imageToControlSpace(0.5, 0, 0, bounds)[2];
    const bottom = imageToControlSpace(0.5, 1, 0, bounds)[2];
    expect(top).toBeGreaterThan(bottom);
    expect(top).toBeCloseTo(bounds.zMax, 6);
    expect(bottom).toBeCloseTo(bounds.zMin, 6);
  });

  it("depth strategy is monotonic in raw z and stays within bounds", () => {
    const bounds = DEFAULT_CONTROL_VOLUME;
    const near = imageToControlSpace(0.5, 0.5, -0.3, bounds)[1];
    const mid = imageToControlSpace(0.5, 0.5, -0.06, bounds)[1];
    const far = imageToControlSpace(0.5, 0.5, 0.3, bounds)[1];
    expect(near).toBeGreaterThanOrEqual(mid);
    expect(mid).toBeGreaterThanOrEqual(far);
    for (const y of [near, mid, far]) {
      expect(y).toBeGreaterThanOrEqual(bounds.yMin);
      expect(y).toBeLessThanOrEqual(bounds.yMax);
    }
  });
});

describe("worldLandmarksToStructJoints", () => {
  it("places the wrist exactly on the anchor", () => {
    const anchor: [number, number, number] = [0.3, -0.2, 0.6];
    const joints = worldLandmarksToStructJoints(WORLD_LANDMARKS_OPEN, anchor);
    expect(joints["wrist"]).toEqual(anchor);
  });

  it("maps a downward (mediapipe +y) landmark to a lower struct z", () => {
    const anchor: [number, number, number] = [0, 0, 0.5];
    const worldLandmarks = [...WORLD_LANDMARKS_OPEN];
    worldLandmarks[8] = lm(0.02, 0.2, -0.01); // index-finger-tip, pushed further down in image
    const joints = worldLandmarksToStructJoints(worldLandmarks, anchor);
    expect(joints["index-finger-tip"]![2]).toBeLessThan(joints["wrist"]![2]);
  });

  it("only emits joints with a known MediaPipe-mapped name", () => {
    const joints = worldLandmarksToStructJoints(WORLD_LANDMARKS_OPEN, [0, 0, 0]);
    expect(joints["index-finger-metacarpal"]).toBeUndefined();
    expect(joints["palm"]).toBeUndefined();
    expect(joints["index-finger-phalanx-proximal"]).toBeDefined();
  });
});

describe("palmOrientation", () => {
  it("returns a unit quaternion for a well-conditioned hand", () => {
    const wrist = worldLandmarksToStructJoints(WORLD_LANDMARKS_OPEN, [0, 0, 0])["wrist"]!;
    const indexMcp = worldLandmarksToStructJoints(WORLD_LANDMARKS_OPEN, [0, 0, 0])["index-finger-phalanx-proximal"]!;
    const middleMcp = worldLandmarksToStructJoints(WORLD_LANDMARKS_OPEN, [0, 0, 0])["middle-finger-phalanx-proximal"]!;
    const pinkyMcp = worldLandmarksToStructJoints(WORLD_LANDMARKS_OPEN, [0, 0, 0])["pinky-finger-phalanx-proximal"]!;
    const q = palmOrientation(wrist, indexMcp, middleMcp, pinkyMcp);
    const magnitude = Math.hypot(q[0], q[1], q[2], q[3]);
    expect(magnitude).toBeCloseTo(1, 3);
    expect(q.every((v) => Number.isFinite(v))).toBe(true);
  });

  it("falls back to identity on degenerate (collinear) geometry, never NaN", () => {
    const q = palmOrientation([0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]);
    expect(q).toEqual([0, 0, 0, 1]);
  });
});

describe("emaSmooth", () => {
  it("passes the first value through unchanged", () => {
    expect(emaSmooth(undefined, [1, 2, 3], 0.35)).toEqual([1, 2, 3]);
  });

  it("blends toward the new value without overshooting", () => {
    const smoothed = emaSmooth([0, 0, 0], [1, 0, 0], 0.5);
    expect(smoothed[0]).toBeCloseTo(0.5, 6);
  });
});

describe("PinchHysteresis", () => {
  it("closes below the closed threshold and opens above the open threshold", () => {
    const pinch = new PinchHysteresis();
    expect(pinch.update(PINCH_OPEN_M)).toBe(false);
    expect(pinch.update(PINCH_CLOSED_M - 0.001)).toBe(true);
    expect(pinch.update((PINCH_CLOSED_M + PINCH_OPEN_M) / 2)).toBe(true); // deadband holds
    expect(pinch.update(PINCH_OPEN_M + 0.001)).toBe(false);
  });

  it("holds the last state on missing data rather than flickering", () => {
    const pinch = new PinchHysteresis();
    pinch.update(PINCH_CLOSED_M - 0.001);
    expect(pinch.update(null)).toBe(true);
  });
});

describe("preferredHandIndex", () => {
  it("prefers a right hand when both are tracked", () => {
    const handedness = [[{ categoryName: "Left", score: 0.9 }], [{ categoryName: "Right", score: 0.9 }]];
    expect(preferredHandIndex(handedness)).toBe(1);
  });

  it("falls back to the first tracked hand otherwise", () => {
    expect(preferredHandIndex([[{ categoryName: "Left", score: 0.9 }]])).toBe(0);
  });

  it("returns undefined when nothing is tracked", () => {
    expect(preferredHandIndex([])).toBeUndefined();
  });
});

describe("mediapipeResultToHandFrame", () => {
  it("produces a valid HandFrame for a tracked hand", () => {
    const hand = mediapipeResultToHandFrame(syntheticResult(), { mirroredPreview: false });
    expect(hand).not.toBeNull();
    expect(hand!.handedness).toBe("right");
    expect(hand!.joints["wrist"]).toBeDefined();
    expect(hand!.gripper).toBeGreaterThanOrEqual(0);
    expect(hand!.gripper).toBeLessThanOrEqual(1);
  });

  it("flips handedness for a mirrored preview by default", () => {
    const hand = mediapipeResultToHandFrame(syntheticResult());
    expect(hand!.handedness).toBe("left");
  });

  it("returns null when no hand is tracked", () => {
    expect(
      mediapipeResultToHandFrame(syntheticResult({ landmarks: [], worldLandmarks: [], handedness: [] })),
    ).toBeNull();
  });

  it("returns null rather than fabricating a frame from too few landmarks", () => {
    const truncated = syntheticResult({
      landmarks: [IMAGE_LANDMARKS_OPEN.slice(0, 5)],
      worldLandmarks: [WORLD_LANDMARKS_OPEN.slice(0, 5)],
    });
    expect(mediapipeResultToHandFrame(truncated)).toBeNull();
  });

  it("smoothing state accumulates across calls, converging toward new positions", () => {
    const previous = new Map<string, [number, number, number]>();
    const first = mediapipeResultToHandFrame(syntheticResult(), { smoothing: { alpha: 0.5, previous } });
    // Move the anchor itself (image-space wrist x), not just the world-space
    // hand shape -- the anchor is what image landmarks control.
    const movedImage = IMAGE_LANDMARKS_OPEN.map((p, i) => (i === 0 ? lm(0.9, p.y, p.z) : p));
    const second = mediapipeResultToHandFrame(
      syntheticResult({ landmarks: [movedImage] }),
      { smoothing: { alpha: 0.5, previous } },
    );
    // hands.ts's HandFrame is WebXR-space; adapter.ts's structToWebxr maps
    // struct [x,y,z] -> webxr [-y, z, -x], so a struct-x move shows up
    // (negated) in webxr position[2], not position[0].
    const firstWebxrZ = first!.joints["wrist"]!.position[2];
    const secondWebxrZ = second!.joints["wrist"]!.position[2];
    expect(secondWebxrZ).not.toEqual(firstWebxrZ);
    // Half-blended (alpha 0.5): strictly between the old and the fully-moved anchor.
    const bounds = DEFAULT_CONTROL_VOLUME;
    const rawNewWebxrZ = -imageToControlSpace(0.9, 0.5, -0.05, bounds)[0];
    expect(secondWebxrZ).toBeLessThan(firstWebxrZ);
    expect(secondWebxrZ).toBeGreaterThan(rawNewWebxrZ);
  });

  it("serializes cleanly (no NaN leaking into the wire shape)", () => {
    const hand = mediapipeResultToHandFrame(syntheticResult());
    const json = JSON.stringify(hand);
    expect(json).not.toContain("NaN");
    expect(JSON.parse(json)).toMatchObject({ handedness: "left" });
  });
});
