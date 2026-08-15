import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { PINCH_CLOSED_M, PINCH_OPEN_M, type HandFrame, type HandPair } from "./hands";
import {
  DEFAULT_CONTROL_VOLUME,
  HandPairTracking,
  MEDIAPIPE_LANDMARK_TO_JOINT,
  PinchHysteresis,
  WebcamHandProvider,
  createHandPairSmoothingState,
  emaSmooth,
  imageToControlSpace,
  mediapipeResultToHandFrame,
  mediapipeResultToHandPair,
  palmOrientation,
  preferredHandIndex,
  resolveHandSide,
  worldLandmarksToStructJoints,
  type MediapipeHandResult,
  type MediapipeLandmark,
  type WebcamHandProviderOptions,
  PALM_SPAN_FAR,
  PALM_SPAN_NEAR,
  depthFromPalmSpan,
  palmSpanImage,
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

// ------------------------------------------------- two-handed test fixtures --

// Only the wrist's image-space landmark is read (it picks the control-volume
// anchor), so a detection can be tagged by giving its wrist a distinct image
// x. That tag is what makes a left/right swap *provable*: `handedness` alone
// cannot catch one, because a swap carries the frame's own self-reported
// label into the wrong slot along with it. Same reasoning, and the same
// trick, as hands.test.ts's LEFT_X / RIGHT_X.
const WRIST_IMAGE_Z = -0.05;
const IMAGE_X_A = 0.2;
const IMAGE_X_B = 0.8;
const IMAGE_X_MOVED = 0.6;

function imageLandmarksAtX(wristImageX: number): MediapipeLandmark[] {
  return WORLD_LANDMARKS_OPEN.map((_, i) =>
    i === 0 ? lm(wristImageX, 0.5, WRIST_IMAGE_Z) : lm(0.5, 0.5, 0),
  );
}

/** The struct-space x a detection tagged with this image x must produce. */
function expectedStructX(wristImageX: number): number {
  return imageToControlSpace(wristImageX, 0.5, WRIST_IMAGE_Z, DEFAULT_CONTROL_VOLUME)[0];
}

/** Recovers the tag from a converted frame. hands.ts's HandFrame is WebXR
 * space and adapter.ts's structToWebxr maps struct [x,y,z] -> webxr
 * [-y, z, -x], so the struct x that identifies a detection comes back as the
 * negated webxr z. */
function wristStructX(hand: HandFrame): number {
  return -hand.joints["wrist"]!.position[2];
}

interface Detection {
  categoryName: string;
  imageX: number;
}

function multiHandResult(...detections: readonly Detection[]): MediapipeHandResult {
  return {
    landmarks: detections.map((d) => imageLandmarksAtX(d.imageX)),
    worldLandmarks: detections.map(() => WORLD_LANDMARKS_OPEN),
    handedness: detections.map((d) => [{ categoryName: d.categoryName, score: 0.95 }]),
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

  it("still collapses two detections to the one preferredHandIndex picks", () => {
    // The regression guard on the single-hand path. Adding the pair path must
    // not tempt this one into emitting both, or into changing which of the
    // two it picks: the teleop pipeline it feeds drives a single end-effector
    // and would interleave unrelated targets if either happened.
    // preferredHandIndex prefers the MediaPipe category "Right" -- the
    // camera's label, deliberately read before any mirroring is applied, so
    // the chosen detection is the same one whatever mirroredPreview says.
    for (const mirroredPreview of [true, false]) {
      const hand = mediapipeResultToHandFrame(
        multiHandResult(
          { categoryName: "Left", imageX: IMAGE_X_A },
          { categoryName: "Right", imageX: IMAGE_X_B },
        ),
        { mirroredPreview },
      );
      expect(hand).not.toBeNull();
      expect(wristStructX(hand!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
    }
  });

  it("is unaffected by detection order, as before", () => {
    const swapped = mediapipeResultToHandFrame(
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_B },
        { categoryName: "Left", imageX: IMAGE_X_A },
      ),
      { mirroredPreview: false },
    );
    expect(swapped!.handedness).toBe("right");
    expect(wristStructX(swapped!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
  });
});

describe("resolveHandSide", () => {
  it("flips the camera's label onto the user's body for a mirrored preview", () => {
    // The camera faces the user, so the hand MediaPipe calls "Right" is the
    // one the user reaches with on their left. A mirrored preview is what the
    // user is actually looking at, so that is the frame the label has to be
    // expressed in.
    expect(resolveHandSide({ categoryName: "Right", score: 1 }, true)).toBe("left");
    expect(resolveHandSide({ categoryName: "Left", score: 1 }, true)).toBe("right");
  });

  it("keeps the camera's label when the preview is not mirrored", () => {
    expect(resolveHandSide({ categoryName: "Right", score: 1 }, false)).toBe("right");
    expect(resolveHandSide({ categoryName: "Left", score: 1 }, false)).toBe("left");
  });
});

describe("mediapipeResultToHandPair", () => {
  it("returns both hands when both are detected", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult(
        { categoryName: "Left", imageX: IMAGE_X_A },
        { categoryName: "Right", imageX: IMAGE_X_B },
      ),
    );
    expect(pair.left?.handedness).toBe("left");
    expect(pair.right?.handedness).toBe("right");
    expect(Object.keys(pair.left!.joints).length).toBeGreaterThan(0);
    expect(Object.keys(pair.right!.joints).length).toBeGreaterThan(0);
    expect(pair.left).not.toBe(pair.right);
  });

  it("does not swap the user's hands on a mirrored preview, in either detection order", () => {
    // The highest-value assertion in this file, and the one bug worth the
    // most to prevent: MediaPipe labels from the camera's point of view, so
    // with the default mirrored preview its "Right" detection is the user's
    // LEFT hand. Bucketing on categoryName instead of the resolved side would
    // put both hands in the wrong slot -- and every frame would still
    // self-report a plausible handedness, so nothing downstream could tell.
    // The per-detection image-x tag is what makes the swap visible.
    const cameraRight = { categoryName: "Right", imageX: IMAGE_X_A };
    const cameraLeft = { categoryName: "Left", imageX: IMAGE_X_B };

    for (const detections of [
      [cameraRight, cameraLeft],
      [cameraLeft, cameraRight],
    ]) {
      const pair = mediapipeResultToHandPair(multiHandResult(...detections));
      // The user's left hand is the one the camera called "Right".
      expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
      expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
      expect(pair.left!.handedness).toBe("left");
      expect(pair.right!.handedness).toBe("right");
    }
  });

  it("does not swap the user's hands on a non-mirrored preview either", () => {
    // Same fixture, mirroring off: now the camera's label already is the
    // user's side, so both hands must land in the *opposite* slots from the
    // test above. A single-frame convention (always flip, or never flip)
    // would pass one of these two tests and fail the other.
    const pair = mediapipeResultToHandPair(
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_A },
        { categoryName: "Left", imageX: IMAGE_X_B },
      ),
      { mirroredPreview: false },
    );
    expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
  });

  it("returns only the left hand when only it is detected", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult({ categoryName: "Right", imageX: IMAGE_X_A }),
    );
    expect(pair.left?.handedness).toBe("left");
    expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(pair.right).toBeNull();
  });

  it("returns only the right hand when only it is detected", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult({ categoryName: "Left", imageX: IMAGE_X_B }),
    );
    expect(pair.right?.handedness).toBe("right");
    expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
    expect(pair.left).toBeNull();
  });

  it("returns nulls, never frames of zeros, when neither hand is detected", () => {
    // Zeros would retarget into a real robot pose at the origin and render a
    // hand collapsed at the floor -- the failure the null contract exists to
    // prevent, preserved across both slots.
    expect(mediapipeResultToHandPair(multiHandResult())).toEqual({ left: null, right: null });
  });

  it("drops a hand with too few landmarks instead of fabricating one", () => {
    const result = multiHandResult(
      { categoryName: "Left", imageX: IMAGE_X_A },
      { categoryName: "Right", imageX: IMAGE_X_B },
    );
    const truncated: MediapipeHandResult = {
      ...result,
      worldLandmarks: [WORLD_LANDMARKS_OPEN.slice(0, 5), WORLD_LANDMARKS_OPEN],
    };
    const pair = mediapipeResultToHandPair(truncated);
    expect(pair.right).toBeNull(); // the truncated one: category "Left" -> user's right
    expect(pair.left).not.toBeNull();
  });

  it("keeps the first of two detections classified as the same hand", () => {
    // MediaPipe can misclassify and report the same handedness twice. One
    // slot cannot hold two frames, so the first wins -- matching
    // readBothHands -- rather than the second silently replacing the first.
    const pair = mediapipeResultToHandPair(
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_A },
        { categoryName: "Right", imageX: IMAGE_X_B },
      ),
    );
    expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(pair.right).toBeNull();
  });

  it("smooths each hand against its own history, not the other's", () => {
    // The failure this pins down: one shared SmoothingState is keyed by joint
    // name alone, so both hands write "wrist" into the same slot and each
    // hand's EMA drags toward wherever the other one was. The output stays
    // smooth and plausible while being wrong, which is why it needs a test
    // and not just care.
    const smoothing = createHandPairSmoothingState(0.5);
    const first = mediapipeResultToHandPair(
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_A }, // user's left
        { categoryName: "Left", imageX: IMAGE_X_B }, // user's right
      ),
      { smoothing },
    );

    // Each hand's first frame passes through unblended. With a shared filter
    // the second hand converted would already be dragged halfway to the
    // first, so this alone catches the shared-state bug.
    expect(wristStructX(first.left!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(wristStructX(first.right!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);

    // Now move ONLY the user's left hand. The right hand's input is
    // byte-identical to last frame, so its smoothed output must be too.
    const second = mediapipeResultToHandPair(
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_MOVED },
        { categoryName: "Left", imageX: IMAGE_X_B },
      ),
      { smoothing },
    );
    expect(wristStructX(second.right!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 10);
    // ...while the hand that actually moved lands half-way, alpha being 0.5.
    expect(wristStructX(second.left!)).toBeCloseTo(
      (expectedStructX(IMAGE_X_A) + expectedStructX(IMAGE_X_MOVED)) / 2,
      6,
    );
  });

  it("resumes a returning hand from its own history, not the other hand's", () => {
    const smoothing = createHandPairSmoothingState(0.5);
    const both = multiHandResult(
      { categoryName: "Right", imageX: IMAGE_X_A },
      { categoryName: "Left", imageX: IMAGE_X_B },
    );
    mediapipeResultToHandPair(both, { smoothing });
    // The user's right hand leaves frame for a while; only the left is fed.
    mediapipeResultToHandPair(
      multiHandResult({ categoryName: "Right", imageX: IMAGE_X_MOVED }),
      { smoothing },
    );
    const back = mediapipeResultToHandPair(both, { smoothing });
    // It returns to exactly where it left off -- its own filter never saw the
    // other hand's motion in the meantime.
    expect(wristStructX(back.right!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 10);
  });
});

// ------------------------------------------------------- tracking-loss gate --

const PAIR_FIXTURE = mediapipeResultToHandPair(
  multiHandResult(
    { categoryName: "Right", imageX: IMAGE_X_A },
    { categoryName: "Left", imageX: IMAGE_X_B },
  ),
);
const LEFT_HAND: HandFrame = PAIR_FIXTURE.left!;
const RIGHT_HAND: HandFrame = PAIR_FIXTURE.right!;
const NO_HANDS: HandPair = { left: null, right: null };

describe("HandPairTracking", () => {
  it("passes both hands straight through while both are tracked", () => {
    const tracking = new HandPairTracking(3);
    expect(tracking.update({ left: LEFT_HAND, right: RIGHT_HAND })).toEqual({
      left: LEFT_HAND,
      right: RIGHT_HAND,
    });
  });

  it("holds a briefly-lost hand's last frame while the other keeps tracking", () => {
    // The single-hand path holds by not calling back at all. A pair callback
    // still has to fire for the hand that IS tracked, so the other slot is
    // filled with its last known frame -- the caller ends up holding exactly
    // what it would have held anyway, rather than being told the hand is gone
    // after one dropped detection.
    const tracking = new HandPairTracking(3);
    tracking.update({ left: LEFT_HAND, right: RIGHT_HAND });
    const held = tracking.update({ left: LEFT_HAND, right: null });
    expect(held).toEqual({ left: LEFT_HAND, right: RIGHT_HAND });
  });

  it("reports a hand null once its dropout is sustained", () => {
    const tracking = new HandPairTracking(3);
    tracking.update({ left: LEFT_HAND, right: RIGHT_HAND });
    expect(tracking.update({ left: LEFT_HAND, right: null })!.right).toBe(RIGHT_HAND);
    expect(tracking.update({ left: LEFT_HAND, right: null })!.right).toBe(RIGHT_HAND);
    expect(tracking.update({ left: LEFT_HAND, right: null })!.right).toBeNull();
    expect(tracking.update({ left: LEFT_HAND, right: null })!.right).toBeNull();
  });

  it("counts each hand's dropout separately", () => {
    // One shared counter would let the steadily-tracked hand keep resetting
    // it, so a hand that had genuinely left the frame would never be reported
    // lost at all.
    const tracking = new HandPairTracking(2);
    tracking.update({ left: LEFT_HAND, right: RIGHT_HAND });
    tracking.update({ left: LEFT_HAND, right: null });
    const lost = tracking.update({ left: LEFT_HAND, right: null });
    expect(lost).toEqual({ left: LEFT_HAND, right: null });
  });

  it("announces a total loss exactly once, then stays quiet", () => {
    // With neither hand fresh there is nobody to call back for, so a brief
    // total dropout skips the callback entirely -- byte for byte the
    // single-hand rule, where the caller simply keeps its last frame.
    const tracking = new HandPairTracking(2);
    tracking.update({ left: LEFT_HAND, right: RIGHT_HAND });
    expect(tracking.update(NO_HANDS)).toBeNull(); // brief: silence, caller holds
    expect(tracking.update(NO_HANDS)).toEqual(NO_HANDS); // sustained: announced
    expect(tracking.update(NO_HANDS)).toBeNull(); // already known: nothing to say
    expect(tracking.update(NO_HANDS)).toBeNull();
  });

  it("still announces a loss for a stream that never tracked anything", () => {
    // The single-hand path emits its null on the Nth miss whether or not a
    // hand was ever seen -- that is how a caller learns the camera is up but
    // there are no hands in it, rather than waiting forever.
    const tracking = new HandPairTracking(2);
    expect(tracking.update(NO_HANDS)).toBeNull();
    expect(tracking.update(NO_HANDS)).toEqual(NO_HANDS);
    expect(tracking.update(NO_HANDS)).toBeNull();
  });

  it("emits a hand again as soon as it returns", () => {
    const tracking = new HandPairTracking(2);
    tracking.update({ left: LEFT_HAND, right: RIGHT_HAND });
    tracking.update({ left: LEFT_HAND, right: null });
    tracking.update({ left: LEFT_HAND, right: null }); // sustained loss
    expect(tracking.update({ left: LEFT_HAND, right: RIGHT_HAND })).toEqual({
      left: LEFT_HAND,
      right: RIGHT_HAND,
    });
  });

  it("does not resurrect a hand that was already reported lost", () => {
    // Once a loss has been announced the held frame has to be forgotten,
    // otherwise the next time the *other* hand moves, this one reappears from
    // a frame that is by then seconds stale -- a hand sitting motionless in
    // the recording where there is no hand at all.
    const tracking = new HandPairTracking(2);
    tracking.update({ left: LEFT_HAND, right: RIGHT_HAND });
    tracking.update(NO_HANDS);
    tracking.update(NO_HANDS); // both sustained-lost
    const back = tracking.update({ left: LEFT_HAND, right: null });
    expect(back).toEqual({ left: LEFT_HAND, right: null });
  });
});

// -------------------------------------------------------------- provider --

/** The provider's constructor is private (it exists only via `create`, which
 * needs a camera and the MediaPipe WASM). These tests drive the frame loop
 * directly instead, with a stub landmarker -- the loop's wiring is what is
 * under test here; the conversion and loss semantics are covered above. */
type ProviderConstructor = new (
  video: unknown,
  stream: unknown,
  landmarker: unknown,
  options: WebcamHandProviderOptions,
) => WebcamHandProvider;

describe("WebcamHandProvider frame loop", () => {
  let pendingFrame: (() => void) | undefined;
  const realRaf = globalThis.requestAnimationFrame;
  const realCancelRaf = globalThis.cancelAnimationFrame;

  beforeEach(() => {
    pendingFrame = undefined;
    globalThis.requestAnimationFrame = ((callback: FrameRequestCallback): number => {
      pendingFrame = () => callback(0);
      return 1;
    }) as typeof requestAnimationFrame;
    globalThis.cancelAnimationFrame = ((): void => {
      pendingFrame = undefined;
    }) as typeof cancelAnimationFrame;
  });

  afterEach(() => {
    globalThis.requestAnimationFrame = realRaf;
    globalThis.cancelAnimationFrame = realCancelRaf;
  });

  function step(): void {
    const frame = pendingFrame;
    pendingFrame = undefined;
    frame?.();
  }

  function makeProvider(
    nextResult: () => MediapipeHandResult,
    options: WebcamHandProviderOptions = {},
  ): WebcamHandProvider {
    const video = { videoWidth: 640, videoHeight: 480 };
    const stream = { getTracks: () => [] };
    const landmarker = { detectForVideo: () => nextResult(), close: () => undefined };
    return new (WebcamHandProvider as unknown as ProviderConstructor)(
      video,
      stream,
      landmarker,
      options,
    );
  }

  it("startPair streams both hands and reports status for them", () => {
    const provider = makeProvider(() =>
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_A },
        { categoryName: "Left", imageX: IMAGE_X_B },
      ),
    );
    const seen: HandPair[] = [];
    provider.startPair((hands) => seen.push(hands));
    step();

    expect(seen).toHaveLength(1);
    expect(seen[0]!.left?.handedness).toBe("left");
    expect(seen[0]!.right?.handedness).toBe("right");
    expect(wristStructX(seen[0]!.left!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    const status = provider.getStatus();
    expect(status.trackingLost).toBe(false);
    expect(status.handedness).toBe("right");
    expect(status.resolutionWidth).toBe(640);
  });

  it("startPair holds briefly, then announces a sustained total loss once", () => {
    let result = multiHandResult(
      { categoryName: "Right", imageX: IMAGE_X_A },
      { categoryName: "Left", imageX: IMAGE_X_B },
    );
    const provider = makeProvider(() => result, { trackingLossFrames: 2 });
    const seen: HandPair[] = [];
    provider.startPair((hands) => seen.push(hands));
    step();

    result = multiHandResult();
    step(); // brief total loss: no callback, the caller keeps what it has
    step(); // sustained: explicit nulls
    step(); // already reported: no callback at all
    step();

    expect(seen).toHaveLength(2);
    expect(seen[1]).toEqual({ left: null, right: null });
    expect(provider.getStatus().trackingLost).toBe(true);
    expect(provider.getStatus().handedness).toBeNull();
  });

  it("start still emits exactly one hand per frame, unchanged", () => {
    const provider = makeProvider(() =>
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_A },
        { categoryName: "Left", imageX: IMAGE_X_B },
      ),
    );
    const seen: (HandFrame | null)[] = [];
    provider.start((hand) => seen.push(hand));
    step();
    step();

    expect(seen).toHaveLength(2);
    // preferredHandIndex picks the camera-labelled "Right" detection, which
    // the default mirrored preview then reports as the user's left hand --
    // pre-existing behaviour, asserted here so the pair path cannot quietly
    // change it.
    expect(seen[0]!.handedness).toBe("left");
    expect(wristStructX(seen[0]!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
  });
});

describe("depth from apparent hand size", () => {
  it("reads a large palm as near and a small one as far", () => {
    // The whole point: a hand's real size is fixed, so its projected size
    // falls as 1/distance. Bigger on screen means closer.
    expect(depthFromPalmSpan(PALM_SPAN_NEAR)).toBe(1);
    expect(depthFromPalmSpan(PALM_SPAN_FAR)).toBe(0);
  });

  it("is monotonic between the calibration points", () => {
    const mid = depthFromPalmSpan((PALM_SPAN_NEAR + PALM_SPAN_FAR) / 2);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(1);
    expect(depthFromPalmSpan(0.20)).toBeGreaterThan(depthFromPalmSpan(0.14));
  });

  it("saturates rather than running off the end of the control volume", () => {
    expect(depthFromPalmSpan(0.9)).toBe(1);
    expect(depthFromPalmSpan(0.01)).toBe(0);
  });

  it("measures wrist to middle knuckle, which curling the fingers does not change", () => {
    // A fingertip-based span would shrink the instant someone closes their
    // hand to grab something, reporting a lunge toward the camera at exactly
    // the wrong moment.
    const open = landmarksWithPalm(0.18);
    const gripping = landmarksWithPalm(0.18);
    // Curl every fingertip inward; the palm landmarks are untouched.
    for (const i of [4, 8, 12, 16, 20]) {
      gripping[i] = { x: 0.5, y: 0.5, z: 0 };
    }
    expect(palmSpanImage(gripping)).toBeCloseTo(palmSpanImage(open)!, 9);
  });

  it("returns null when the palm landmarks are missing rather than guessing", () => {
    expect(palmSpanImage(undefined)).toBeNull();
    expect(palmSpanImage([{ x: 0, y: 0, z: 0 }])).toBeNull();
  });

  it("ignores MediaPipe's z when measuring the span", () => {
    // Folding z back in would reintroduce the very signal this replaces.
    const flat = landmarksWithPalm(0.16);
    const deep = landmarksWithPalm(0.16).map((l) => ({ ...l, z: -0.4 }));
    expect(palmSpanImage(deep)).toBeCloseTo(palmSpanImage(flat)!, 9);
  });

  it("drives the control volume's depth axis from the palm span when given one", () => {
    const bounds = { xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1 };
    const near = imageToControlSpace(0.5, 0.5, 0, bounds, PALM_SPAN_NEAR);
    const far = imageToControlSpace(0.5, 0.5, 0, bounds, PALM_SPAN_FAR);
    expect(near[1]).toBe(1);
    expect(far[1]).toBe(0);
  });

  it("falls back to MediaPipe z only when no span is available", () => {
    const bounds = { xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1 };
    const withSpan = imageToControlSpace(0.5, 0.5, -0.15, bounds, PALM_SPAN_FAR);
    const withoutSpan = imageToControlSpace(0.5, 0.5, -0.15, bounds, null);
    // Same z, opposite ends: the span must win when present.
    expect(withSpan[1]).toBe(0);
    expect(withoutSpan[1]).toBe(1);
  });
});

/** 21 landmarks with the wrist at origin and the middle-finger knuckle
 * `span` away, so palmSpanImage returns exactly `span`. */
function landmarksWithPalm(span: number): MediapipeLandmark[] {
  const points: MediapipeLandmark[] = [];
  for (let i = 0; i < 21; i += 1) points.push({ x: 0.5, y: 0.5, z: 0 });
  points[0] = { x: 0.5, y: 0.5, z: 0 };
  points[9] = { x: 0.5 + span, y: 0.5, z: 0 };
  return points;
}
