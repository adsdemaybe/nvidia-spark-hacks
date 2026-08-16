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
  CAMERA_PLACEMENTS,
  DEFAULT_IMAGE_ASPECT,
  DEPTH_TRAVEL_M,
  PALM_LENGTH_M,
  PLACEMENT_TILT_DEG,
  metresPerImageUnit,
  cameraBasis,
  placeInControlVolume,
  type CameraPlacement,
  type ControlVolumeBounds,
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

/** The struct-space x a detection tagged with this image x must produce.
 *
 * Takes the mirroring, because a user-facing camera sees the demonstrator
 * reversed and struct x is flipped to compensate -- so the expected value for
 * a given image x depends on it, exactly as the conversion does. */
function expectedStructX(wristImageX: number, mirrored = true): number {
  // Must pass the SAME palm span the fixture produces, because placement is
  // metric now: `imageLandmarksAtX` puts the wrist at (x, 0.5) and the middle
  // knuckle at (0.5, 0.5), so the span is exactly that separation. Predicting
  // with a null span would silently compare against the fallback mapping.
  return imageToControlSpace(
    wristImageX,
    0.5,
    WRIST_IMAGE_Z,
    DEFAULT_CONTROL_VOLUME,
    Math.abs(0.5 - wristImageX),
    "frontal",
    mirrored,
  )[0];
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
    // Linearity and full coverage of the range. The DIRECTION is the mirroring
    // question and is asserted separately -- here the camera is declared
    // non-mirrored so this stays a test about the interpolation.
    const bounds = DEFAULT_CONTROL_VOLUME;
    const at = (x: number): number =>
      imageToControlSpace(x, 0.5, 0, bounds, null, "frontal", false)[0];
    expect(at(0)).toBeCloseTo(bounds.xMin, 6);
    expect(at(1)).toBeCloseTo(bounds.xMax, 6);
    expect(at(0.5)).toBeCloseTo((bounds.xMin + bounds.xMax) / 2, 6);
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
    // Monotonic, and in the direction the geometry dictates: the lens sits
    // across the desk from the demonstrator, so a hand NEARER the lens is a
    // hand reaching further AWAY from them -- a LARGER struct Y.
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

  it("does not flip handedness for a mirrored preview", () => {
    // The preview is mirrored with a CSS transform, which cannot affect the
    // pixels the landmarker reads -- so MediaPipe's label needs no correction.
    // Applying one inverted the hands in every recorded frame.
    const hand = mediapipeResultToHandFrame(syntheticResult());
    expect(hand!.handedness).toBe("right");
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
    // Half-blended (alpha 0.5): strictly between the old and the fully-moved
    // anchor. Which side is which follows from the mirroring -- a larger image
    // x is a smaller struct x, hence a LARGER webxr z (structToWebxr negates
    // it) -- so this brackets the value rather than asserting a direction.
    const rawNewWebxrZ = -expectedStructX(0.9);
    const low = Math.min(firstWebxrZ, rawNewWebxrZ);
    const high = Math.max(firstWebxrZ, rawNewWebxrZ);
    expect(secondWebxrZ).toBeGreaterThan(low);
    expect(secondWebxrZ).toBeLessThan(high);
    expect(secondWebxrZ).toBeCloseTo((firstWebxrZ + rawNewWebxrZ) / 2, 6);
  });

  it("serializes cleanly (no NaN leaking into the wire shape)", () => {
    const hand = mediapipeResultToHandFrame(syntheticResult());
    const json = JSON.stringify(hand);
    expect(json).not.toContain("NaN");
    expect(JSON.parse(json)).toMatchObject({ handedness: "right" });
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
      expect(wristStructX(hand!)).toBeCloseTo(expectedStructX(IMAGE_X_B, mirroredPreview), 6);
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
    expect(wristStructX(swapped!)).toBeCloseTo(expectedStructX(IMAGE_X_B, false), 6);
  });
});

describe("handedness matches which side of the body the hand is on", () => {
  // Measured, not reasoned: across 1215 instants where both hands were tracked
  // in a real 36-second recording, the hand labelled "right" was on the +X
  // side of the hand labelled "left" in 0.0% of them. +X is the user's right,
  // so the labels were inverted in every single frame.
  //
  // The cause is a conflation. `mirroredPreview` was documented as "the
  // preview is shown mirrored", and it drives two different things: where a
  // hand is placed, and what it is called. But the preview is mirrored with a
  // CSS transform, and CSS does not touch the pixels the landmarker reads --
  // MediaPipe is always handed the RAW frame. So the geometric flip (a camera
  // pointed at you sees you mirrored) is real and needed, while the label flip
  // was correcting for a mirroring that never reached MediaPipe.
  //
  // Before the geometry was fixed, both were wrong in the same direction and
  // the result looked self-consistent: a "left" label on the left of the
  // screen, in a world that was entirely mirrored. That is why no test caught
  // it and why it needed real two-handed data to see.
  const rightHandDetection = { categoryName: "Right", imageX: 0.25 };
  const leftHandDetection = { categoryName: "Left", imageX: 0.75 };

  it("puts the hand labelled right on the +X side of the one labelled left", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult(rightHandDetection, leftHandDetection),
    );
    expect(pair.left).not.toBeNull();
    expect(pair.right).not.toBeNull();
    expect(wristStructX(pair.right!)).toBeGreaterThan(wristStructX(pair.left!));
  });

  it("holds in either detection order", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult(leftHandDetection, rightHandDetection),
    );
    expect(wristStructX(pair.right!)).toBeGreaterThan(wristStructX(pair.left!));
  });

  it("labels are unchanged by how the preview happens to be displayed", () => {
    // The CSS transform on the <video> element is a presentation choice. It
    // cannot affect what the landmarker sees, so it must not affect labels.
    // It DOES affect geometry -- `mirroredPreview` still says whether the
    // camera faces the user -- which is why this asserts the label only.
    for (const mirroredPreview of [true, false]) {
      const pair = mediapipeResultToHandPair(
        multiHandResult(rightHandDetection, leftHandDetection),
        { mirroredPreview },
      );
      expect(pair.right!.handedness).toBe("right");
      expect(pair.left!.handedness).toBe("left");
    }
  });

  it("labels a single detection by the side of the body it is on", () => {
    const right = mediapipeResultToHandFrame(multiHandResult(rightHandDetection))!;
    expect(right.handedness).toBe("right");
    const left = mediapipeResultToHandFrame(multiHandResult(leftHandDetection))!;
    expect(left.handedness).toBe("left");
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
      // MediaPipe's label is already the user's own side: it reads the raw
      // frame, which is never mirrored on the way in.
      expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
      expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
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
    expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A, false), 6);
    expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B, false), 6);
  });

  it("returns only the right hand when only it is detected", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult({ categoryName: "Right", imageX: IMAGE_X_A }),
    );
    expect(pair.right?.handedness).toBe("right");
    expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(pair.left).toBeNull();
  });

  it("returns only the left hand when only it is detected", () => {
    const pair = mediapipeResultToHandPair(
      multiHandResult({ categoryName: "Left", imageX: IMAGE_X_B }),
    );
    expect(pair.left?.handedness).toBe("left");
    expect(wristStructX(pair.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);
    expect(pair.right).toBeNull();
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
    expect(pair.left).toBeNull(); // the truncated one is the first detection
    expect(pair.right).not.toBeNull();
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
    expect(wristStructX(pair.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(pair.left).toBeNull();
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
        { categoryName: "Right", imageX: IMAGE_X_A }, // user's right
        { categoryName: "Left", imageX: IMAGE_X_B }, // user's left
      ),
      { smoothing },
    );

    // Each hand's first frame passes through unblended. With a shared filter
    // the second hand converted would already be dragged halfway to the
    // first, so this alone catches the shared-state bug.
    expect(wristStructX(first.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
    expect(wristStructX(first.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 6);

    // Now move ONLY the user's left hand. The right hand's input is
    // byte-identical to last frame, so its smoothed output must be too.
    const second = mediapipeResultToHandPair(
      multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_MOVED },
        { categoryName: "Left", imageX: IMAGE_X_B },
      ),
      { smoothing },
    );
    expect(wristStructX(second.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 10);
    // ...while the hand that actually moved lands half-way, alpha being 0.5.
    expect(wristStructX(second.right!)).toBeCloseTo(
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
    expect(wristStructX(back.left!)).toBeCloseTo(expectedStructX(IMAGE_X_B), 10);
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
    expect(wristStructX(seen[0]!.right!)).toBeCloseTo(expectedStructX(IMAGE_X_A), 6);
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

  it("reports the placement and the raw palm span, so calibration is readable", () => {
    // The two numbers that turn "retune the depth constants" from a guess into
    // a measurement: hold your hand on the table, read the span, hold it at
    // lift height, read it again. Without them the near/far constants can only
    // be tuned by feel, which is how they drifted in the first place.
    const placement: CameraPlacement = "overhead";
    const provider = makeProvider(
      () => multiHandResult(
        { categoryName: "Right", imageX: IMAGE_X_A },
        { categoryName: "Left", imageX: IMAGE_X_B },
      ),
      { placement },
    );
    provider.startPair(() => undefined);
    step();

    const status = provider.getStatus();
    expect(status.placement).toBe("overhead");
    // imageLandmarksAtX puts the wrist at (imageX, 0.5) and the middle knuckle
    // at (0.5, 0.5), so the span is exactly that separation.
    expect(status.palmSpanImage).toBeCloseTo(Math.abs(0.5 - IMAGE_X_B), 9);
  });

  it("reports a null palm span rather than a stale one when no hand is seen", () => {
    let result = multiHandResult({ categoryName: "Right", imageX: IMAGE_X_A });
    const provider = makeProvider(() => result, { trackingLossFrames: 2 });
    provider.startPair(() => undefined);
    step();
    expect(provider.getStatus().palmSpanImage).not.toBeNull();

    result = multiHandResult();
    step();
    step(); // sustained loss
    expect(provider.getStatus().palmSpanImage).toBeNull();
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
    // IS the user's right hand -- the landmarker reads the unmirrored frame,
    // so its label needs no correction. Asserted here so the pair path cannot
    // quietly change it.
    expect(seen[0]!.handedness).toBe("right");
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

  it("pushes the anchor away from the demonstrator as the hand nears the lens", () => {
    // The camera is on the laptop, across the desk. So a hand approaching the
    // lens is a hand reaching OUT, and the object has to travel away from the
    // demonstrator with it. Getting this backwards is not subtle in use:
    // reaching forward drags the mug back toward you.
    const bounds = { xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1 };
    const nearLens = imageToControlSpace(0.5, 0.5, 0, bounds, PALM_SPAN_NEAR);
    const farFromLens = imageToControlSpace(0.5, 0.5, 0, bounds, PALM_SPAN_FAR);
    expect(nearLens[1]).toBeGreaterThan(farFromLens[1]);
    // A fixed travel, not "however wide the box happens to be" -- the volume
    // is a reach limit now and must not double the depth sensitivity when it
    // is widened.
    expect(nearLens[1] - farFromLens[1]).toBeCloseTo(DEPTH_TRAVEL_M, 6);
  });

  it("falls back to MediaPipe z only when no span is available", () => {
    const bounds = { xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1 };
    const withSpan = imageToControlSpace(0.5, 0.5, -0.15, bounds, PALM_SPAN_FAR);
    const withoutSpan = imageToControlSpace(0.5, 0.5, -0.15, bounds, null);
    // Same z, opposite ends of the depth range: the span must win when present.
    expect(withSpan[1]).toBeLessThan(withoutSpan[1]);
  });
});

// ----------------------------------------------------------- metric scale --

describe("the hand is the ruler", () => {
  // Why this exists: the wrist anchor used to be lerped across the control
  // volume, so the full image width became 32cm of struct X no matter how much
  // desk the camera actually saw. The finger geometry, meanwhile, is MediaPipe
  // world landmarks -- real metres. So each hand rendered full size while the
  // GAP between two hands was squashed by whatever ratio the camera's field of
  // view had to the box. Two hands 40cm apart landed ~18cm apart.
  //
  // The fix needs no camera calibration at all: apparent palm span and image
  // position are measured in the SAME normalized units, so their ratio is a
  // metres-per-image-unit scale. The focal length cancels.

  it("converts image units to metres using apparent palm size", () => {
    // A palm spanning a quarter of the frame means the frame is four palms
    // wide, so one image unit is four palm lengths.
    expect(metresPerImageUnit(0.25)).toBeCloseTo(PALM_LENGTH_M * 4, 9);
    expect(metresPerImageUnit(0.5)).toBeCloseTo(PALM_LENGTH_M * 2, 9);
  });

  it("reports a nearer hand as a smaller scale, because it fills more frame", () => {
    expect(metresPerImageUnit(0.4)!).toBeLessThan(metresPerImageUnit(0.2)!);
  });

  it("returns null for a missing or degenerate span rather than dividing by zero", () => {
    expect(metresPerImageUnit(null)).toBeNull();
    expect(metresPerImageUnit(0)).toBeNull();
  });

  it("places two hands the real distance apart", () => {
    // The actual complaint, as a number. Palm span 0.2 means one image unit is
    // half a metre across, so hands 0.4 image units apart are 20cm apart --
    // and they have to render 20cm apart, not 0.4 * the box width.
    const scale = metresPerImageUnit(0.2)!;
    const left = placeInControlVolume(0.3, 0.5, 0.5, WIDE_VOLUME, 0, true, scale);
    const right = placeInControlVolume(0.7, 0.5, 0.5, WIDE_VOLUME, 0, true, scale);
    const separation = Math.abs(left[0] - right[0]);
    expect(separation).toBeCloseTo(0.4 * scale, 6);
    expect(separation).toBeCloseTo(0.2, 6);
  });

  it("keeps hand separation independent of the control volume's width", () => {
    // The clearest statement of the bug: the box is a REACH LIMIT, not a scale
    // factor. Two boxes of different widths must not report two different
    // distances between the same pair of hands.
    const scale = metresPerImageUnit(0.2)!;
    const sep = (bounds: ControlVolumeBounds): number =>
      Math.abs(
        placeInControlVolume(0.4, 0.5, 0.5, bounds, 0, true, scale)[0] -
          placeInControlVolume(0.6, 0.5, 0.5, bounds, 0, true, scale)[0],
      );
    expect(sep(WIDE_VOLUME)).toBeCloseTo(sep({ ...WIDE_VOLUME, xMin: -1.5, xMax: 1.5 }), 9);
  });

  it("scales separation with distance: the same gap on screen is further when far", () => {
    // Two hands a fixed number of pixels apart are physically further apart
    // when they are further from the lens. A fixed box mapping cannot express
    // that at all -- it reports the same distance either way.
    const near = metresPerImageUnit(0.4)!;
    const far = metresPerImageUnit(0.15)!;
    const gap = (scale: number): number =>
      Math.abs(
        placeInControlVolume(0.4, 0.5, 0.5, WIDE_VOLUME, 0, true, scale)[0] -
          placeInControlVolume(0.6, 0.5, 0.5, WIDE_VOLUME, 0, true, scale)[0],
      );
    expect(gap(far)).toBeGreaterThan(gap(near));
  });

  it("corrects for the image aspect so vertical distance is not stretched", () => {
    // MediaPipe normalizes x by width and y by HEIGHT, so one unit of y is a
    // shorter distance than one unit of x on a 16:9 frame. Treating them alike
    // stretches every vertical measurement by ~1.78.
    const scale = metresPerImageUnit(0.2)!;
    const vertical = Math.abs(
      placeInControlVolume(0.5, 0.3, 0.5, WIDE_VOLUME, 0, true, scale)[2] -
        placeInControlVolume(0.5, 0.7, 0.5, WIDE_VOLUME, 0, true, scale)[2],
    );
    const horizontal = Math.abs(
      placeInControlVolume(0.3, 0.5, 0.5, WIDE_VOLUME, 0, true, scale)[0] -
        placeInControlVolume(0.7, 0.5, 0.5, WIDE_VOLUME, 0, true, scale)[0],
    );
    // Same 0.4 of a frame in each direction, but vertically that is less desk.
    expect(vertical).toBeCloseTo(horizontal / DEFAULT_IMAGE_ASPECT, 6);
  });

  it("still produces a position when no span is available", () => {
    // A partially-tracked hand has no palm span, and losing the anchor
    // entirely would be worse than an approximate one -- so the box mapping
    // remains as the fallback, exactly as MediaPipe z remains for depth.
    const p = placeInControlVolume(0.5, 0.5, 0.5, WIDE_VOLUME, 0, true, null);
    expect(p.every((v) => Number.isFinite(v))).toBe(true);
  });

  it("stays inside the control volume however far the metric offset reaches", () => {
    // Metric placement can point outside the reachable box; the box still has
    // to win, or the hand leaves the workspace and can never touch the mug.
    const scale = metresPerImageUnit(0.1)!; // very far away = very large scale
    const p = placeInControlVolume(0.0, 1.0, 0.5, DEFAULT_CONTROL_VOLUME, 0, true, scale);
    expect(p[0]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.xMin - 1e-9);
    expect(p[0]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.xMax + 1e-9);
    expect(p[2]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.zMin - 1e-9);
    expect(p[2]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.zMax + 1e-9);
  });

  it("a hand and the mug keep their real proportions", () => {
    // The sanity check a demonstrator actually performs by eye: an adult hand
    // is a bit over twice the width of an 82mm mug. If the wrist moves in a
    // squashed space while the fingers do not, that proportion breaks as soon
    // as the hand moves away from the centre of frame.
    const scale = metresPerImageUnit(0.2)!;
    const knuckleSpan = 0.09; // wrist to knuckle, metres, from the fixture
    const a = placeInControlVolume(0.5, 0.5, 0.5, WIDE_VOLUME, 0, true, scale);
    const b = placeInControlVolume(0.5 + knuckleSpan / scale, 0.5, 0.5, WIDE_VOLUME, 0, true, scale);
    expect(Math.abs(a[0] - b[0])).toBeCloseTo(knuckleSpan, 6);
  });
});

// ------------------------------------------------------------ camera tilt --

describe("cameraBasis", () => {
  // The whole axis mapping now comes from one rotation, so these vectors are
  // the only place a sign can be wrong -- and a rotation cannot mirror, which
  // makes a whole class of "the hand is inside out" bugs unrepresentable.
  const dotv = (a: readonly number[], b: readonly number[]): number =>
    a[0]! * b[0]! + a[1]! * b[1]! + a[2]! * b[2]!;

  it("at 0 degrees the camera faces you: image down is world down", () => {
    const b = cameraBasis(0, true);
    expect(b.right).toEqual([-1, 0, 0]); // facing you, so image-right is your left
    expect(b.down[2]).toBeCloseTo(-1, 9); // down the image is down in the world
    expect(b.away[1]).toBeCloseTo(-1, 9); // away from the lens is toward you
  });

  it("at 90 degrees the camera looks straight down: image down is away from you", () => {
    const b = cameraBasis(90, true);
    expect(b.right).toEqual([-1, 0, 0]);
    expect(b.down[1]).toBeCloseTo(1, 9); // lower in frame = further across the desk
    expect(b.away[2]).toBeCloseTo(-1, 9); // away from the lens is down toward the table
  });

  it("at 45 degrees image down is part world-down and part away-from-you", () => {
    // The angle a laptop lid actually reaches. Neither endpoint describes it,
    // which is exactly why a binary preset put the vertical axis in the wrong
    // place: half of "up" was being thrown away.
    const b = cameraBasis(45, true);
    expect(b.down[1]).toBeGreaterThan(0);
    expect(b.down[2]).toBeLessThan(0);
    expect(Math.abs(b.down[1])).toBeCloseTo(Math.abs(b.down[2]), 6);
  });

  it("is orthonormal and right-handed at every angle", () => {
    // Right-handed is the property that keeps the rendered hand from being a
    // mirror image of the real one, and it now holds by construction rather
    // than by three separately-maintained sign choices.
    for (const tilt of [0, 15, 30, 45, 60, 75, 90]) {
      const { right, down, away } = cameraBasis(tilt, true);
      for (const v of [right, down, away]) expect(Math.hypot(...v)).toBeCloseTo(1, 9);
      expect(dotv(right, down)).toBeCloseTo(0, 9);
      expect(dotv(down, away)).toBeCloseTo(0, 9);
      expect(dotv(right, away)).toBeCloseTo(0, 9);
      // right x down = away  <=>  the triple is right-handed
      const cross = [
        right[1] * down[2] - right[2] * down[1],
        right[2] * down[0] - right[0] * down[2],
        right[0] * down[1] - right[1] * down[0],
      ];
      for (let i = 0; i < 3; i += 1) expect(cross[i]!).toBeCloseTo(away[i]!, 9);
    }
  });
});

describe("tilt drives the vertical axis continuously", () => {
  it("raising the hand raises struct Z at every tilt", () => {
    // The bug as the demonstrator experienced it: "up is down". Lifting your
    // hand has to raise Z whether the lid is upright, half-tilted, or flat --
    // at 0 degrees that signal arrives as image-y, at 90 as apparent size, and
    // in between as both. A binary preset read only one of the two.
    for (const tilt of [0, 30, 45, 60, 90]) {
      const low = placeInControlVolume(0.5, 0.9, 0.1, UNIT_VOLUME, tilt, true);
      const high = placeInControlVolume(0.5, 0.1, 0.9, UNIT_VOLUME, tilt, true);
      expect(high[2]).toBeGreaterThan(low[2]);
    }
  });

  it("reaching away from yourself increases struct Y at every tilt", () => {
    // +Y is away from the demonstrator. Reaching out means moving toward the
    // lens (the laptop is across the desk), which is a LARGER depth, and --
    // once the lid is tilted -- also lower in the frame. Both signals have to
    // agree, or reach and height fight each other at intermediate angles.
    for (const tilt of [0, 30, 45, 60, 90]) {
      const awayFromBody = placeInControlVolume(0.5, 0.9, 0.9, UNIT_VOLUME, tilt, true);
      const towardBody = placeInControlVolume(0.5, 0.1, 0.1, UNIT_VOLUME, tilt, true);
      expect(awayFromBody[1]).toBeGreaterThan(towardBody[1]);
    }
  });

  it("keeps every axis inside the control volume at every tilt", () => {
    for (const tilt of [0, 45, 90]) {
      for (const v of [-3, 0, 0.5, 1, 3]) {
        const p = placeInControlVolume(v, v, v, DEFAULT_CONTROL_VOLUME, tilt, true);
        expect(p[0]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.xMin - 1e-9);
        expect(p[0]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.xMax + 1e-9);
        expect(p[1]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.yMin - 1e-9);
        expect(p[1]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.yMax + 1e-9);
        expect(p[2]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.zMin - 1e-9);
        expect(p[2]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.zMax + 1e-9);
      }
    }
  });

  it("the hand model stays right-handed at every tilt", () => {
    // "The hand is upside down" and "up is down" were the same bug seen twice.
    // Deriving both from one rotation means fixing the control direction
    // cannot leave the model flipped behind.
    for (const tilt of [0, 30, 45, 60, 90]) {
      const joints = worldLandmarksToStructJoints(
        WORLD_LANDMARKS_OPEN, [0.3, 0.05, 0.1], tilt, true,
      );
      const mediapipe = handChirality((i) => {
        const l = WORLD_LANDMARKS_OPEN[i]!;
        return [l.x, l.y, l.z];
      });
      const struct = handChirality((i) => joints[MEDIAPIPE_LANDMARK_TO_JOINT[i]!]!);
      expect(Math.sign(struct)).toBe(Math.sign(mediapipe));
    }
  });

  it("a fingertip below the wrist stays below it at every tilt", () => {
    // The concrete form of "upside down": with the palm facing the camera, a
    // landmark lower in the image must never render above the wrist.
    for (const tilt of [0, 30, 45, 60, 90]) {
      const anchor: [number, number, number] = [0.3, 0.0, 0.15];
      const joints = worldLandmarksToStructJoints(
        worldOffsetAt(lm(0, 0.08, 0)), anchor, tilt, true,
      );
      expect(joints["index-finger-phalanx-proximal"]![2]).toBeLessThanOrEqual(anchor[2] + 1e-9);
    }
  });
});

// -------------------------------------------------------------- chirality --

function sub(a: readonly number[], b: readonly number[]): [number, number, number] {
  return [a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!];
}

/** Scalar triple product a · (b × c). Its SIGN is the handedness of the triple,
 * and any rotation preserves it while any reflection flips it. */
function tripleProduct(
  a: readonly number[], b: readonly number[], c: readonly number[],
): number {
  const cross: [number, number, number] = [
    b[1]! * c[2]! - b[2]! * c[1]!,
    b[2]! * c[0]! - b[0]! * c[2]!,
    b[0]! * c[1]! - b[1]! * c[0]!,
  ];
  return a[0]! * cross[0]! + a[1]! * cross[1]! + a[2]! * cross[2]!;
}

/** Three independent vectors spanning a hand, in whatever space the joint
 * positions are expressed in. Chirality is the sign of their triple product. */
function handChirality(get: (index: number) => readonly number[]): number {
  const wrist = get(0);
  return tripleProduct(
    sub(get(9), wrist),   // wrist -> middle knuckle, along the palm
    sub(get(5), get(17)), // pinky knuckle -> index knuckle, across the palm
    sub(get(4), wrist),   // wrist -> thumb tip, out of the palm plane
  );
}

describe("hand chirality", () => {
  // The bug this pins down, and the reason the rendered hand looks reversed:
  // a right hand and a left hand are mirror images, so a conversion that
  // reflects turns one into the other. Reflection is not a subtle numerical
  // issue -- it is the determinant of the linear map being negative -- and it
  // survives every other test in this file, because every one of them checks
  // one axis at a time and a reflection is correct on every axis individually.
  //
  // The scalar triple product is the whole test: rotations preserve its sign,
  // reflections flip it. Nothing here depends on which axis convention is
  // "right", so this holds no matter how the placement debate settles.
  for (const placement of ["frontal", "overhead"] as const) {
    it(`does not mirror the hand under ${placement} placement`, () => {
      const joints = worldLandmarksToStructJoints(
        WORLD_LANDMARKS_OPEN, [0.3, 0.05, 0.1], PLACEMENT_TILT_DEG[placement],
      );
      const mediapipe = handChirality((i) => {
        const l = WORLD_LANDMARKS_OPEN[i]!;
        return [l.x, l.y, l.z];
      });
      const struct = handChirality((i) => joints[MEDIAPIPE_LANDMARK_TO_JOINT[i]!]!);
      expect(mediapipe).not.toBeCloseTo(0, 9); // the fixture is non-degenerate
      expect(Math.sign(struct)).toBe(Math.sign(mediapipe));
    });
  }

  it("survives the whole conversion, not just the landmark mapping", () => {
    // End to end through the real entry point, in the frame the recorder
    // actually stores -- a reflection reintroduced downstream would be just as
    // wrong as one here, and this is the frame the dataset is written in.
    const hand = mediapipeResultToHandFrame(syntheticResult())!;
    const mediapipe = handChirality((i) => {
      const l = WORLD_LANDMARKS_OPEN[i]!;
      return [l.x, l.y, l.z];
    });
    const rendered = handChirality((i) => hand.joints[MEDIAPIPE_LANDMARK_TO_JOINT[i]!]!.position);
    // webxr space is a rotation of struct space (structToWebxr is a permutation
    // with det +1), so the sign has to carry through unchanged.
    expect(Math.sign(rendered)).toBe(Math.sign(mediapipe));
  });
});

describe("the camera faces the user, so its image-right is the user's left", () => {
  // `resolveHandSide` has always known this -- it is why a mirrored preview
  // flips the handedness label. The bug was that the flip was applied to the
  // LABEL and to nothing else, so positions were left in the camera's mirrored
  // frame while the labels were in the user's. Reaching right moved the hand
  // left, which is most of "impossible to control".
  it("maps a larger image x to a SMALLER struct x when the preview is mirrored", () => {
    const bounds = DEFAULT_CONTROL_VOLUME;
    expect(imageToControlSpace(0, 0.5, 0, bounds)[0]).toBeCloseTo(bounds.xMax, 6);
    expect(imageToControlSpace(1, 0.5, 0, bounds)[0]).toBeCloseTo(bounds.xMin, 6);
  });

  it("does not flip when the camera is not mirrored", () => {
    const bounds = DEFAULT_CONTROL_VOLUME;
    expect(imageToControlSpace(0, 0.5, 0, bounds, null, "frontal", false)[0])
      .toBeCloseTo(bounds.xMin, 6);
    expect(imageToControlSpace(1, 0.5, 0, bounds, null, "frontal", false)[0])
      .toBeCloseTo(bounds.xMax, 6);
  });

  it("moving the user's hand toward their right raises struct x", () => {
    // Stated the way a demonstrator experiences it. The user's right hand
    // moving to the user's right travels toward the image's LEFT, because the
    // camera is facing them -- so a decreasing image x has to raise struct x.
    const bounds = DEFAULT_CONTROL_VOLUME;
    const towardUserLeft = imageToControlSpace(0.75, 0.5, 0, bounds)[0];
    const towardUserRight = imageToControlSpace(0.25, 0.5, 0, bounds)[0];
    expect(towardUserRight).toBeGreaterThan(towardUserLeft);
  });

  it("applies the same flip to the finger geometry as to the anchor", () => {
    // Flipping only one of the two would put the fingers on the wrong side of
    // the wrist -- a hand that tracks to the right place and is inside out.
    const anchor: [number, number, number] = [0.3, 0.05, 0.1];
    const joints = worldLandmarksToStructJoints(
      worldOffsetAt(lm(0.1, 0, 0)), anchor, 0,
    );
    expect(joints["index-finger-phalanx-proximal"]![0]).toBeCloseTo(anchor[0] - 0.1, 9);
  });
});

// --------------------------------------------------------- camera placement --

/** A unit cube, so an assertion reads as the raw 0..1 parameter along each
 * struct axis rather than as an arbitrary interpolated metre value. */
const UNIT_VOLUME: ControlVolumeBounds = {
  xMin: 0, xMax: 1, yMin: 0, yMax: 1, zMin: 0, zMax: 1,
};

/** Big enough that metric placement is never clipped, so a test about SCALE
 * measures scale rather than the clamp. */
const WIDE_VOLUME: ControlVolumeBounds = {
  xMin: -1, xMax: 1, yMin: -1, yMax: 1, zMin: -1, zMax: 1,
};

/** Landmarks for `worldLandmarksToStructJoints` with the wrist at the origin
 * and index-finger-proximal (MediaPipe landmark 5) displaced by one axis, so
 * each axis of the camera->struct rotation can be pinned independently. */
function worldOffsetAt(offset: MediapipeLandmark): MediapipeLandmark[] {
  const points: MediapipeLandmark[] = [lm(0, 0, 0)];
  for (let i = 1; i < 21; i += 1) points.push(i === 5 ? offset : lm(0, 0, 0));
  return points;
}

describe("placeInControlVolume", () => {
  it("frontal maps x and z as shipped, and corrects the depth direction", () => {
    // The regression anchor for this whole change: extracting the axis table
    // must leave the live-tuned frontal placement byte-identical, or every
    // episode already recorded stops meaning what the ones recorded next mean.
    for (const [x, y, depth] of [
      [0, 0, 0], [1, 1, 1], [0.25, 0.75, 0.5], [0.5, 0.5, 0.9],
    ] as const) {
      const p = placeInControlVolume(x, y, depth, UNIT_VOLUME, 0, false);
      expect(p[0]).toBeCloseTo(x, 9); // image x -> struct X
      // Near the LENS is away from you, so it is a LARGER struct Y -- across a
      // fixed travel centred in the volume, not the volume's full width.
      expect(p[1]).toBeCloseTo(0.5 + (depth - 0.5) * DEPTH_TRAVEL_M, 9);
      expect(p[2]).toBeCloseTo(1 - y, 9); // image up -> larger struct Z
    }
  });

  it("frontal with a user-facing camera flips only X", () => {
    // The shipped mapping was right about Y and Z and wrong about X. Pinning
    // the two separately is what keeps a future placement change from
    // reintroducing the mirror while "fixing" an axis.
    for (const [x, y, depth] of [[0, 0, 0], [1, 1, 1], [0.25, 0.75, 0.5]] as const) {
      const plain = placeInControlVolume(x, y, depth, UNIT_VOLUME, 0, false);
      const mirrored = placeInControlVolume(x, y, depth, UNIT_VOLUME, 0, true);
      expect(mirrored[0]).toBeCloseTo(1 - plain[0], 9);
      expect(mirrored[1]).toBeCloseTo(plain[1], 9);
      expect(mirrored[2]).toBeCloseTo(plain[2], 9);
    }
  });

  it("overhead sends image y to the toward/away axis, not to height", () => {
    // The camera now looks down at the desk, so the image plane IS the desk
    // plane: up/down in frame is no longer height at all, it is how far the
    // hand is from the demonstrator.
    const top = placeInControlVolume(0.5, 0, 0.5, UNIT_VOLUME, 90);
    const bottom = placeInControlVolume(0.5, 1, 0.5, UNIT_VOLUME, 90);
    expect(top[1]).not.toBeCloseTo(bottom[1], 6);
    expect(top[2]).toBeCloseTo(bottom[2], 6); // height is not image y any more
  });

  it("overhead reads the top of the image as nearest the demonstrator", () => {
    // Tilting the lid forward rotates the camera's up-vector from world-up to
    // pointing back at the person, so what is closest to them lands at the top
    // of the frame. This is the one sign a live camera has to confirm; if it
    // feels backwards it is this lerp and nothing else.
    const near = placeInControlVolume(0.5, 0, 0.5, UNIT_VOLUME, 90)[1];
    const far = placeInControlVolume(0.5, 1, 0.5, UNIT_VOLUME, 90)[1];
    expect(near).toBeCloseTo(UNIT_VOLUME.yMin, 6);
    expect(far).toBeCloseTo(UNIT_VOLUME.yMax, 6);
  });

  it("overhead makes apparent hand size the height axis, so lifting raises Z", () => {
    // Looking down, a hand that grows in frame is a hand coming up off the
    // table -- which is exactly the axis the success predicate measures.
    const lifted = placeInControlVolume(0.5, 0.5, 1, UNIT_VOLUME, 90);
    const resting = placeInControlVolume(0.5, 0.5, 0, UNIT_VOLUME, 90);
    expect(lifted[2]).toBeGreaterThan(resting[2]);
    expect(lifted[2] - resting[2]).toBeCloseTo(DEPTH_TRAVEL_M, 6);
  });

  it("treats the left/right axis identically in both placements", () => {
    // Tilting the lid rotates the camera about its own horizontal axis, so the
    // one thing the tilt cannot change is what image x means -- including the
    // user-facing mirror, which both placements share.
    for (const x of [0, 0.25, 1]) {
      expect(placeInControlVolume(x, 0.5, 0.5, UNIT_VOLUME, 90)[0]).toBeCloseTo(
        placeInControlVolume(x, 0.5, 0.5, UNIT_VOLUME, 0)[0], 9,
      );
    }
  });

  it("keeps every axis inside the control volume for every placement", () => {
    for (const placement of ["frontal", "overhead"] as const) {
      for (const v of [-5, 0, 0.5, 1, 5]) {
        const p = placeInControlVolume(v, v, Math.min(1, Math.max(0, v)), DEFAULT_CONTROL_VOLUME, PLACEMENT_TILT_DEG[placement]);
        expect(p[0]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.xMin);
        expect(p[0]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.xMax);
        expect(p[1]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.yMin);
        expect(p[1]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.yMax);
        expect(p[2]).toBeGreaterThanOrEqual(DEFAULT_CONTROL_VOLUME.zMin);
        expect(p[2]).toBeLessThanOrEqual(DEFAULT_CONTROL_VOLUME.zMax);
      }
    }
  });
});

describe("per-placement palm-span calibration", () => {
  it("defaults to the frontal profile, so existing callers are unchanged", () => {
    expect(CAMERA_PLACEMENTS.frontal.palmSpanNear).toBe(PALM_SPAN_NEAR);
    expect(CAMERA_PLACEMENTS.frontal.palmSpanFar).toBe(PALM_SPAN_FAR);
    expect(depthFromPalmSpan(PALM_SPAN_NEAR)).toBe(depthFromPalmSpan(PALM_SPAN_NEAR, "frontal"));
  });

  it("expects a bigger hand overhead, because the camera is closer to it", () => {
    // Overhead the lens is ~50cm above the desk and the useful range is the
    // 0-34cm the control volume is tall, so the hand lives at 20-55cm rather
    // than the 30-70cm a frontal camera sees. Reusing the frontal numbers
    // would peg depth at "near" through the entire lift.
    expect(CAMERA_PLACEMENTS.overhead.palmSpanNear).toBeGreaterThan(PALM_SPAN_NEAR);
    expect(CAMERA_PLACEMENTS.overhead.palmSpanFar).toBeGreaterThan(PALM_SPAN_FAR);
    expect(depthFromPalmSpan(CAMERA_PLACEMENTS.overhead.palmSpanNear, "overhead")).toBe(1);
    expect(depthFromPalmSpan(CAMERA_PLACEMENTS.overhead.palmSpanFar, "overhead")).toBe(0);
  });

  it("reads a mid-range span as saturated under the wrong profile", () => {
    // Why the profile has to travel with the placement rather than being one
    // module constant: the same hand reports two different depths.
    const span = CAMERA_PLACEMENTS.overhead.palmSpanNear;
    expect(depthFromPalmSpan(span, "overhead")).toBe(1);
    expect(depthFromPalmSpan(span, "frontal")).toBe(1);
    const resting = CAMERA_PLACEMENTS.overhead.palmSpanFar;
    expect(depthFromPalmSpan(resting, "overhead")).toBe(0);
    expect(depthFromPalmSpan(resting, "frontal")).toBeGreaterThan(0);
  });

  it("imageToControlSpace routes the span through the placement's profile", () => {
    const resting = CAMERA_PLACEMENTS.overhead.palmSpanFar;
    const lifted = CAMERA_PLACEMENTS.overhead.palmSpanNear;
    const low = imageToControlSpace(0.5, 0.5, 0, UNIT_VOLUME, resting, "overhead");
    const high = imageToControlSpace(0.5, 0.5, 0, UNIT_VOLUME, lifted, "overhead");
    expect(high[2]).toBeGreaterThan(low[2]);
    expect(high[2] - low[2]).toBeCloseTo(DEPTH_TRAVEL_M, 6);
  });
});

describe("worldLandmarksToStructJoints placement rotation", () => {
  // The half that is easy to forget. MediaPipe's world landmarks are
  // camera-relative (x right, y down, z away from the lens), so they rotate
  // with the camera exactly as the anchor does. Leave them frontal while the
  // anchor goes overhead and the wrist tracks correctly while the fingers
  // point 90 degrees wrong -- which quietly corrupts every grasp distance.
  const ANCHOR: [number, number, number] = [0.3, 0.05, 0.1];
  const JOINT = "index-finger-phalanx-proximal";

  it("frontal is unchanged: mediapipe down lowers struct Z, away lowers struct Y", () => {
    const down = worldLandmarksToStructJoints(worldOffsetAt(lm(0, 0.1, 0)), ANCHOR)[JOINT]!;
    expect(down[2]).toBeCloseTo(ANCHOR[2] - 0.1, 9);
    expect(down[1]).toBeCloseTo(ANCHOR[1], 9);

    const away = worldLandmarksToStructJoints(worldOffsetAt(lm(0, 0, 0.1)), ANCHOR)[JOINT]!;
    expect(away[1]).toBeCloseTo(ANCHOR[1] - 0.1, 9);
    expect(away[2]).toBeCloseTo(ANCHOR[2], 9);
  });

  it("overhead sends mediapipe down to the toward/away axis", () => {
    // Looking down, "lower in the image" is not lower in the world -- it is
    // further from the demonstrator, matching what image y now means.
    const down = worldLandmarksToStructJoints(
      worldOffsetAt(lm(0, 0.1, 0)), ANCHOR, 90,
    )[JOINT]!;
    expect(down[1]).toBeCloseTo(ANCHOR[1] + 0.1, 9);
    expect(down[2]).toBeCloseTo(ANCHOR[2], 9);
  });

  it("overhead sends mediapipe away-from-lens to height", () => {
    // The lens is above the desk, so further from it means closer to the
    // table -- a fingertip below the wrist, not behind it.
    const away = worldLandmarksToStructJoints(
      worldOffsetAt(lm(0, 0, 0.1)), ANCHOR, 90,
    )[JOINT]!;
    expect(away[2]).toBeCloseTo(ANCHOR[2] - 0.1, 9);
    expect(away[1]).toBeCloseTo(ANCHOR[1], 9);
  });

  it("keeps mediapipe right on struct X and the wrist on the anchor, in both", () => {
    for (const placement of ["frontal", "overhead"] as const) {
      const joints = worldLandmarksToStructJoints(
        worldOffsetAt(lm(0.1, 0, 0)), ANCHOR, PLACEMENT_TILT_DEG[placement], false,
      );
      expect(joints["wrist"]).toEqual(ANCHOR);
      expect(joints[JOINT]![0]).toBeCloseTo(ANCHOR[0] + 0.1, 9);
      // ...and the wrist stays on the anchor under the mirror too: the flip is
      // applied to offsets FROM the wrist, so the wrist's own offset is zero.
      const flipped = worldLandmarksToStructJoints(
        worldOffsetAt(lm(0.1, 0, 0)), ANCHOR, PLACEMENT_TILT_DEG[placement], true,
      );
      expect(flipped["wrist"]).toEqual(ANCHOR);
      expect(flipped[JOINT]![0]).toBeCloseTo(ANCHOR[0] - 0.1, 9);
    }
  });
});

describe("placement reaches the conversion entry points", () => {
  function structPosition(hand: HandFrame): [number, number, number] {
    // adapter.ts's structToWebxr maps struct [x,y,z] -> webxr [-y, z, -x].
    const p = hand.joints["wrist"]!.position;
    return [-p[2], -p[0], p[1]];
  }

  it("mediapipeResultToHandFrame honours the placement", () => {
    const result = multiHandResult({ categoryName: "Right", imageX: IMAGE_X_A });
    const frontal = mediapipeResultToHandFrame(result, { placement: "frontal" })!;
    const overhead = mediapipeResultToHandFrame(result, { placement: "overhead" })!;
    // Same detection, same image position, different axis assignment.
    expect(structPosition(overhead)[1]).not.toBeCloseTo(structPosition(frontal)[1], 4);
    expect(structPosition(overhead)[0]).toBeCloseTo(structPosition(frontal)[0], 9);
  });

  it("mediapipeResultToHandPair honours the placement for both hands", () => {
    const result = multiHandResult(
      { categoryName: "Right", imageX: IMAGE_X_A },
      { categoryName: "Left", imageX: IMAGE_X_B },
    );
    const pair = mediapipeResultToHandPair(result, { placement: "overhead" });
    const bounds = DEFAULT_CONTROL_VOLUME;
    // Both wrists sit at image y 0.5, which overhead reads as the middle of
    // the toward/away axis rather than the middle of the height axis.
    const span = Math.abs(0.5 - IMAGE_X_A);
    const expected = placeInControlVolume(
      IMAGE_X_A, 0.5,
      depthFromPalmSpan(span, "overhead"),
      bounds, 90, true, metresPerImageUnit(span),
    );
    expect(structPosition(pair.right!)).toEqual(
      expected.map((v) => expect.closeTo(v, 6)) as unknown as [number, number, number],
    );
  });

  it("defaults to frontal when no placement is given", () => {
    const result = multiHandResult({ categoryName: "Right", imageX: IMAGE_X_A });
    expect(structPosition(mediapipeResultToHandFrame(result)!)).toEqual(
      structPosition(mediapipeResultToHandFrame(result, { placement: "frontal" })!),
    );
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
