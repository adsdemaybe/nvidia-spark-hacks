/**
 * WebcamHandProvider — laptop-webcam real hand tracking, a peer to
 * MockHandProvider/OpenXR (spec: STRUCT_Webcam_MediaPipe_Hands_Claude.md).
 * Runs MediaPipe's Hand Landmarker in-browser via `@mediapipe/tasks-vision`
 * (the official JS/WASM port -- same model, same 21 landmarks as the spec's
 * literal Python/OpenCV framing) rather than a separate Python service,
 * because it reuses ShadowHand/LiveRetargetSession/HumanEpisodeRecorder
 * exactly as built, with zero new backend plumbing. See STATE.md for why.
 *
 * Produces the same `hands.ts` HandFrame every other provider produces --
 * built by authoring an intermediate struct_world frame (StructHandFrame,
 * imported from mockHand.ts, the same shape the mock fixture uses) and
 * converting it through mockHand.ts's `toHandFrame`, the existing
 * device-frame boundary. No new coordinate-conversion code duplicated here.
 *
 * MediaPipe's 21 landmarks are coarser than WebXR's 25: there is one
 * metacarpal point (thumb only); index/middle/ring/pinky have no separate
 * metacarpal landmark. Those four WebXR `*-finger-metacarpal` joints, and
 * `palm`, are simply never emitted for webcam frames -- ShadowHand already
 * skips any bone segment touching a missing joint, so the visible effect is
 * that those four fingers don't visually connect to the wrist (only thumb
 * does). A real, disclosed gap, not a bug.
 *
 * "screen_control" mode (spec's only mode this round -- calibrated_workspace
 * is explicitly deferred): the wrist's image-space x/y and MediaPipe's
 * relative z pick an anchor position inside a configurable virtual control
 * volume; MediaPipe's hand-centered *world* landmarks (real relative 3D hand
 * shape, not room-anchored -- spec's own explicit limitation) are translated
 * so the wrist lands on that anchor, giving every other joint a geometrically
 * faithful position around it. Depth is never treated as an absolute camera
 * distance (`depth_quality` stays "estimated" throughout).
 *
 * Axis note: this app's struct_world convention is right-handed Z-up (Z is
 * real "up", matching e.g. spatialTeachMain.ts's button at z=0.53m table
 * height). The spec's own screen_control example literally assigns image
 * up/down to a field it calls "Y" and depth to "Z" -- that labeling doesn't
 * match this app's actual Z-up convention, so it is deliberately NOT copied
 * literally: image up/down drives struct Z (real "up"), camera depth drives
 * struct Y (this scene's existing forward/depth axis -- see
 * ROBOT_BASE_STRUCT/ASSET_WORLD_POSITION in spatialTeachMain.ts). The default
 * control-volume bounds are also recentered on this fixture scene's actual
 * reachable geometry (robot base at struct Y=-0.7, button at Y=0.0,Z=0.53)
 * rather than the spec's generic example numbers, which don't overlap it.
 *
 * Two-handed capture: the landmarker has always run with numHands: 2, but
 * for a while only one hand ever left this module, because the single-hand
 * teleop path (ArmRetargeter / LiveRetargetSession) drives one end-effector
 * and would interleave unrelated targets if fed both. That constraint is
 * real and unchanged -- `mediapipeResultToHandFrame` / `start` still pick
 * exactly one hand. But the task being recorded for RL is bimanual, so
 * dropping a hand at the provider is data loss no downstream stage can undo.
 * `mediapipeResultToHandPair` / `startPair` therefore exist alongside, in the
 * same left/right-slot shape (`hands.ts`'s HandPair) the WebXR path's
 * `readBothHands` already emits, so ShadowHand and the recorders cannot tell
 * which provider produced a pair. Same relationship, same reasoning, as
 * `preferredInputSource` vs `readBothHands` in hands.ts.
 *
 * Depth sign, world-landmark axis mapping, and the mirrored-preview
 * handedness flip are documented, deliberate choices below -- each is
 * internally consistent and testable, but real-camera *calibration*
 * (do image-space bounds feel right, is handedness correct on screen) can
 * only be confirmed live, with an actual camera, by the person testing this.
 * "Measure rather than assume" (spec's own words) -- flagged, not hidden.
 */

import {
  FilesetResolver,
  HandLandmarker,
  type HandLandmarkerResult,
} from "@mediapipe/tasks-vision";
import { PINCH_CLOSED_M, PINCH_OPEN_M, type HandFrame, type HandPair } from "./hands";
import { toHandFrame, type StructHandFrame, type StructJoint } from "./mockHand";

type Vec3 = [number, number, number];
type Quat = [number, number, number, number];

const IDENTITY_QUAT: Quat = [0, 0, 0, 1];

// ------------------------------------------------------------- MediaPipe --

/** Landmark index (MediaPipe's fixed 21-point order) -> the WebXR joint name
 * (hands.ts's HAND_JOINTS) it corresponds to. Thumb has 4 points on both
 * sides (CMC/MCP/IP/TIP <-> metacarpal/proximal/distal/tip); the other four
 * fingers have 4 MediaPipe points (MCP/PIP/DIP/TIP) against WebXR's 5
 * (metacarpal/proximal/intermediate/distal/tip) -- MCP maps to
 * phalanx-proximal, never to metacarpal (see module docstring). */
export const MEDIAPIPE_LANDMARK_TO_JOINT: readonly string[] = [
  "wrist",
  "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
  "index-finger-phalanx-proximal", "index-finger-phalanx-intermediate",
  "index-finger-phalanx-distal", "index-finger-tip",
  "middle-finger-phalanx-proximal", "middle-finger-phalanx-intermediate",
  "middle-finger-phalanx-distal", "middle-finger-tip",
  "ring-finger-phalanx-proximal", "ring-finger-phalanx-intermediate",
  "ring-finger-phalanx-distal", "ring-finger-tip",
  "pinky-finger-phalanx-proximal", "pinky-finger-phalanx-intermediate",
  "pinky-finger-phalanx-distal", "pinky-finger-tip",
] as const;

export interface MediapipeLandmark {
  x: number;
  y: number;
  z: number;
}

export interface MediapipeHandednessCategory {
  categoryName: string;
  score: number;
}

/** The subset of HandLandmarkerResult this module actually reads -- kept
 * narrow and structural so tests can pass plain synthetic objects (spec:
 * "no real camera required... synthetic MediaPipe-like results"), not the
 * real SDK type. */
export interface MediapipeHandResult {
  landmarks: ReadonlyArray<ReadonlyArray<MediapipeLandmark>>;
  worldLandmarks: ReadonlyArray<ReadonlyArray<MediapipeLandmark>>;
  handedness: ReadonlyArray<ReadonlyArray<MediapipeHandednessCategory>>;
}

// ------------------------------------------------------------------ math --

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function clamp01(t: number): number {
  return Math.min(1, Math.max(0, t));
}

export function emaSmooth(previous: Vec3 | undefined, next: Vec3, alpha: number): Vec3 {
  if (!previous) return next;
  return [
    previous[0] + alpha * (next[0] - previous[0]),
    previous[1] + alpha * (next[1] - previous[1]),
    previous[2] + alpha * (next[2] - previous[2]),
  ];
}

// --------------------------------------------------------- control space --

export interface ControlVolumeBounds {
  xMin: number;
  xMax: number;
  /** Depth (this fixture scene's existing forward/back axis). */
  yMin: number;
  yMax: number;
  /** Height (struct_world's real "up" axis). */
  zMin: number;
  zMax: number;
}

/** Recentered on this fixture scene's actual reachable geometry -- see
 * module docstring's axis note. Not the spec's literal example numbers.
 *
 * Retuned again for the real SO-101 (Track A) -- it's a small desktop arm
 * with maybe 30-40cm of usable reach, nothing like the placeholder arm the
 * first live-tested bounds were sized for (robot base was at struct
 * Y=-0.7 then; the robot now sits at the origin). Bounds below are
 * centered on the same real, IK-verified points spatialTeachMain.ts's
 * ASSET_WORLD_POSITION/DEFAULT_GOAL_M use (button ~(0.35,-0.05,0.14),
 * retract goal ~(0.37,0.07,0.2)), with margin on every axis so hand motion
 * can reach past both. Still needs a live camera to confirm feel -- see
 * module docstring's "measure rather than assume." */
export const DEFAULT_CONTROL_VOLUME: ControlVolumeBounds = {
  xMin: 0.2, xMax: 0.42,
  yMin: -0.15, yMax: 0.15,
  zMin: 0.08, zMax: 0.28,
};

// relative_mediapipe depth strategy: the wrist's raw, image-space MediaPipe z
// is a small, hand-relative, unitless value with no fixed physical scale.
// Retained only as the fallback below -- see depthFromPalmSpan for why it is
// no longer the primary signal.
const Z_NEAR_RAW: number = -0.15;
const Z_FAR_RAW: number = 0.02;

function normalizeDepth(zRaw: number): number {
  return clamp01((zRaw - Z_FAR_RAW) / (Z_NEAR_RAW - Z_FAR_RAW));
}

/**
 * Apparent palm length at the near and far ends of the usable range, in
 * normalized image units (the image is 0..1 across).
 *
 * A hand is a rigid object of roughly fixed size, so its projected size falls
 * as 1/distance -- which makes apparent size a far stronger monocular depth
 * cue than MediaPipe's landmark z, a hand-relative value with no physical
 * scale that barely moves as the whole hand translates toward the camera.
 * Depth was the axis people could not control, and this is why.
 *
 * The reference numbers: an adult palm (wrist to middle-finger knuckle) is
 * about 10cm. A typical laptop camera has a horizontal field of view near
 * 60 degrees, so it sees ~35cm across at 30cm away and ~80cm across at 70cm.
 * A 10cm palm therefore spans ~0.29 of the image up close and ~0.12 at arm's
 * length. The bounds below sit just inside that, so the extremes of the
 * control volume are reachable without pressing your hand into the lens.
 */
export const PALM_SPAN_NEAR = 0.27;
export const PALM_SPAN_FAR = 0.11;

/**
 * Depth from apparent palm size: 0 = far from the camera, 1 = near.
 *
 * Measured wrist-to-middle-finger-knuckle rather than to a fingertip on
 * purpose. That span is fixed by the skeleton and does not change when the
 * fingers curl -- and the fingers curl precisely when someone grabs
 * something, which is the exact moment a fingertip-based measure would report
 * a spurious lunge toward the camera.
 */
export function depthFromPalmSpan(spanImage: number): number {
  return clamp01((spanImage - PALM_SPAN_FAR) / (PALM_SPAN_NEAR - PALM_SPAN_FAR));
}

/** Normalized-image distance from the wrist (landmark 0) to the middle-finger
 * MCP knuckle (landmark 9). Null when either is missing. */
export function palmSpanImage(
  landmarks: readonly MediapipeLandmark[] | undefined,
): number | null {
  const wrist = landmarks?.[0];
  const middleMcp = landmarks?.[9];
  if (!wrist || !middleMcp) return null;
  // Image x/y only. Including MediaPipe's z here would fold the very signal
  // this function exists to replace back into the measurement.
  return Math.hypot(middleMcp.x - wrist.x, middleMcp.y - wrist.y);
}

/** Spec section 15: image x/y (+ relative z) -> a control-space anchor.
 * Y-image is inverted so real upward hand motion moves the anchor up. */
export function imageToControlSpace(
  xImage: number,
  yImage: number,
  zRaw: number,
  bounds: ControlVolumeBounds = DEFAULT_CONTROL_VOLUME,
  palmSpan?: number | null,
): Vec3 {
  const x = lerp(bounds.xMin, bounds.xMax, clamp01(xImage));
  const z = lerp(bounds.zMin, bounds.zMax, clamp01(1 - yImage));
  // Apparent palm size when we have it, MediaPipe's landmark z only as a
  // fallback. The fallback exists so a partially-tracked hand still produces
  // a depth rather than none, not because the two are equally good.
  const depth =
    palmSpan !== undefined && palmSpan !== null
      ? depthFromPalmSpan(palmSpan)
      : normalizeDepth(zRaw);
  const y = lerp(bounds.yMin, bounds.yMax, depth);
  return [x, y, z];
}

/** Translates MediaPipe's hand-centered *world* landmarks (real relative 3D
 * hand shape, roughly metric, NOT room-anchored) so the wrist lands exactly
 * on `wristAnchor`, giving every other joint a geometrically faithful
 * position around it. MediaPipe world landmarks: x right, y DOWN, z roughly
 * away-from-camera. struct_world here: x right (unchanged), z up (= -dy),
 * y depth (= -dz, a documented arbitrary-but-consistent sign choice -- see
 * module docstring). Only landmarks with a known joint name are emitted. */
export function worldLandmarksToStructJoints(
  worldLandmarks: ReadonlyArray<MediapipeLandmark>,
  wristAnchor: Vec3,
): Record<string, Vec3> {
  const wrist = worldLandmarks[0];
  if (!wrist) return {};
  const joints: Record<string, Vec3> = {};
  for (let i = 0; i < MEDIAPIPE_LANDMARK_TO_JOINT.length; i += 1) {
    const landmark = worldLandmarks[i];
    const name = MEDIAPIPE_LANDMARK_TO_JOINT[i];
    if (!landmark || !name) continue;
    const dx = landmark.x - wrist.x;
    const dy = landmark.y - wrist.y;
    const dz = landmark.z - wrist.z;
    joints[name] = [
      wristAnchor[0] + dx,
      wristAnchor[1] - dz,
      wristAnchor[2] - dy,
    ];
  }
  return joints;
}

// --------------------------------------------------------- orientation --

function subtract(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function norm(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}

function normalize(v: Vec3): Vec3 {
  const n = norm(v);
  return n > 1e-9 ? [v[0] / n, v[1] / n, v[2] / n] : v;
}

/** Rotation matrix (rows = basis vectors, world <- local) -> quaternion
 * [x,y,z,w], standard trace method. */
function matrixToQuat(right: Vec3, up: Vec3, forward: Vec3): Quat {
  const m00 = right[0], m01 = up[0], m02 = forward[0];
  const m10 = right[1], m11 = up[1], m12 = forward[1];
  const m20 = right[2], m21 = up[2], m22 = forward[2];
  const trace = m00 + m11 + m22;
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1.0);
    return [(m21 - m12) * s, (m02 - m20) * s, (m10 - m01) * s, 0.25 / s];
  }
  if (m00 > m11 && m00 > m22) {
    const s = 2.0 * Math.sqrt(1.0 + m00 - m11 - m22);
    return [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s];
  }
  if (m11 > m22) {
    const s = 2.0 * Math.sqrt(1.0 + m11 - m00 - m22);
    return [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s];
  }
  const s = 2.0 * Math.sqrt(1.0 + m22 - m00 - m11);
  return [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s];
}

/** Spec section 18: a deterministic palm basis from three real landmark
 * positions. Falls back to identity on degenerate geometry (near-parallel
 * vectors, e.g. the hand edge-on to the camera) -- never fabricates a
 * rotation, and never emits NaN. hands.ts's JointPose has no
 * `orientation_valid` flag to extend for this (a purely cosmetic field for
 * every joint except wrist), so only the wrist's orientation is ever
 * genuinely derived; finger joints stay identity, matching how ShadowHand
 * never reads per-joint orientation anyway. */
export function palmOrientation(wrist: Vec3, indexMcp: Vec3, middleMcp: Vec3, pinkyMcp: Vec3): Quat {
  const forwardRaw = subtract(middleMcp, wrist);
  const rightRaw = subtract(pinkyMcp, indexMcp);
  if (norm(forwardRaw) < 1e-6 || norm(rightRaw) < 1e-6) return IDENTITY_QUAT;
  const forward = normalize(forwardRaw);
  // Gram-Schmidt: strip the forward component out of `right` so the two are
  // exactly orthogonal, then derive `up` so (right, up, forward) is a proper
  // right-handed basis (right x up = forward) -- matrixToQuat's trace method
  // assumes a determinant-+1 rotation matrix; a left-handed triple silently
  // produces a non-unit quaternion instead of a clean error.
  const rightProjection = dot(rightRaw, forward);
  const rightOrtho: Vec3 = [
    rightRaw[0] - rightProjection * forward[0],
    rightRaw[1] - rightProjection * forward[1],
    rightRaw[2] - rightProjection * forward[2],
  ];
  if (norm(rightOrtho) < 1e-6) return IDENTITY_QUAT; // right was parallel to forward
  const right = normalize(rightOrtho);
  const up = cross(forward, right);
  return matrixToQuat(right, up, forward);
}

// -------------------------------------------------------------- pinch --

/** Spec section 19: boolean pinch with hysteresis, reusing hands.ts's own
 * PINCH_CLOSED_M/PINCH_OPEN_M as the two thresholds -- no new constants. */
export class PinchHysteresis {
  private active = false;

  update(apertureM: number | null): boolean {
    if (apertureM === null) return this.active;
    if (this.active && apertureM > PINCH_OPEN_M) this.active = false;
    else if (!this.active && apertureM < PINCH_CLOSED_M) this.active = true;
    return this.active;
  }
}

// ----------------------------------------------------------- handedness --

export function preferredHandIndex(
  handedness: ReadonlyArray<ReadonlyArray<MediapipeHandednessCategory>>,
): number | undefined {
  let fallback: number | undefined;
  for (let i = 0; i < handedness.length; i += 1) {
    const top = handedness[i]?.[0];
    if (!top) continue;
    if (top.categoryName === "Right") return i;
    fallback ??= i;
  }
  return fallback;
}

function flipHand(side: "left" | "right"): "left" | "right" {
  return side === "left" ? "right" : "left";
}

/**
 * MediaPipe's per-hand handedness label -> the side of the *user's* body that
 * hand actually belongs to.
 *
 * This is the single place that decision is made, and everything two-handed
 * routes through it, because it is the one place a left/right swap can enter.
 * MediaPipe classifies from the camera's point of view: the camera faces the
 * user, so the user's right hand appears on the camera image's left and gets
 * labelled accordingly. A selfie-style preview is then shown mirrored so the
 * user's motion feels natural on screen, which undoes exactly that inversion
 * for the viewer but not for the label -- hence the flip, defaulting on
 * because a mirrored preview is the normal laptop-webcam presentation. The
 * flip itself, its default, and this option are pre-existing behaviour
 * carried over verbatim from the single-hand path, not a new judgement call.
 *
 * The consequence that matters for a pair: which slot a hand lands in must be
 * decided from this resolved side, never from `categoryName` directly. Bucket
 * on the raw label with a mirrored preview and both hands land in the wrong
 * slot -- and every frame still self-reports a plausible handedness, so
 * nothing downstream can notice.
 */
export function resolveHandSide(
  category: MediapipeHandednessCategory,
  mirroredPreview: boolean,
): "left" | "right" {
  const rawSide: "left" | "right" = category.categoryName === "Left" ? "left" : "right";
  return mirroredPreview ? flipHand(rawSide) : rawSide;
}

// ------------------------------------------------------------ conversion --

export interface SmoothingState {
  alpha: number;
  previous: Map<string, Vec3>;
}

export interface MediapipeConversionOptions {
  controlVolume?: ControlVolumeBounds;
  /** MediaPipe's handedness classification assumes a non-mirrored input
   * frame; a laptop webcam's on-screen preview is typically shown mirrored
   * for natural selfie-view UX. Default true flips the label to match what
   * the user sees on screen -- confirm live and flip if wrong (module
   * docstring). */
  mirroredPreview?: boolean;
  smoothing?: SmoothingState;
}

/** Per-hand smoothing filters for the two-handed path.
 *
 * Two filters rather than one because a `SmoothingState`'s `previous` map is
 * keyed by joint *name* only -- there is nothing in the key that says which
 * hand a "wrist" entry came from. Sharing one map across both hands would
 * make each hand's EMA blend toward wherever the other hand's same-named
 * joint was last written, so the two hands would visibly drag each other
 * around and, worse, would do it silently: the output stays smooth and
 * plausible, just wrong, and it gets worse the further apart the hands are.
 * Keying by name *and* side is what fixes it; separate states per side is the
 * simplest spelling of that, and it also means a hand that drops out and
 * returns resumes from its own history rather than the other hand's. */
export interface HandPairSmoothingState {
  left: SmoothingState;
  right: SmoothingState;
}

export function createHandPairSmoothingState(alpha: number): HandPairSmoothingState {
  return {
    left: { alpha, previous: new Map() },
    right: { alpha, previous: new Map() },
  };
}

export interface MediapipePairConversionOptions {
  controlVolume?: ControlVolumeBounds;
  /** Same meaning and same default as the single-hand option -- see
   * `resolveHandSide`, which both paths share. */
  mirroredPreview?: boolean;
  smoothing?: HandPairSmoothingState;
}

/** The whole per-hand conversion, for one already-chosen detection index.
 *
 * Extracted so the single-hand and two-handed entry points are the same code
 * rather than two copies of the landmark mapping, the control-volume math and
 * the palm basis. A second copy would drift: the two would eventually disagree
 * about depth sign or joint naming, and a recording's meaning would depend on
 * which entry point happened to produce it. */
function convertHandAtIndex(
  result: MediapipeHandResult,
  index: number,
  bounds: ControlVolumeBounds,
  mirroredPreview: boolean,
  smoothing: SmoothingState | undefined,
): HandFrame | null {
  const imageLandmarks = result.landmarks[index];
  const worldLandmarks = result.worldLandmarks[index];
  const handedCategory = result.handedness[index]?.[0];
  if (!imageLandmarks?.[0] || !worldLandmarks?.[0] || !handedCategory) return null;
  if (imageLandmarks.length < 21 || worldLandmarks.length < 21) return null;

  const wristImage = imageLandmarks[0];
  const anchor = imageToControlSpace(
    wristImage.x,
    wristImage.y,
    wristImage.z,
    bounds,
    palmSpanImage(imageLandmarks),
  );
  const positions = worldLandmarksToStructJoints(worldLandmarks, anchor);

  const joints: Record<string, StructJoint> = {};
  for (const [name, rawPosition] of Object.entries(positions)) {
    let position = rawPosition;
    if (smoothing) {
      const { alpha, previous } = smoothing;
      position = emaSmooth(previous.get(name), rawPosition, alpha);
      previous.set(name, position);
    }
    joints[name] = { position_m: position, orientation_xyzw: IDENTITY_QUAT };
  }

  const wrist = joints["wrist"];
  const indexMcp = joints["index-finger-phalanx-proximal"];
  const middleMcp = joints["middle-finger-phalanx-proximal"];
  const pinkyMcp = joints["pinky-finger-phalanx-proximal"];
  if (wrist && indexMcp && middleMcp && pinkyMcp) {
    joints["wrist"] = {
      ...wrist,
      orientation_xyzw: palmOrientation(
        wrist.position_m, indexMcp.position_m, middleMcp.position_m, pinkyMcp.position_m,
      ),
    };
  }

  const struct: StructHandFrame = {
    timestamp_ns: 0,
    hand: resolveHandSide(handedCategory, mirroredPreview),
    joints,
  };
  return toHandFrame(struct);
}

/** Pure conversion entry point: one MediaPipe result -> the canonical
 * HandFrame, or null when no hand was detected. No camera/WASM/DOM
 * involved, so this is directly unit-testable with synthetic results.
 *
 * Single-hand by design (see `preferredHandIndex`): the teleop pipeline this
 * feeds drives one end-effector. `mediapipeResultToHandPair` is the entry
 * point for callers that want both. */
export function mediapipeResultToHandFrame(
  result: MediapipeHandResult,
  options: MediapipeConversionOptions = {},
): HandFrame | null {
  const index = preferredHandIndex(result.handedness);
  if (index === undefined) return null;
  return convertHandAtIndex(
    result,
    index,
    options.controlVolume ?? DEFAULT_CONTROL_VOLUME,
    options.mirroredPreview ?? true,
    options.smoothing,
  );
}

/** One MediaPipe result -> both hands, each slot null when that hand was not
 * detected this frame.
 *
 * The pair shape, and the reasons for it, are `hands.ts`'s: the two hands are
 * not interchangeable to any caller, and an array would push every one of
 * them into re-deriving the same `find(h => h.handedness === "left")` lookup,
 * which is precisely where a mix-up gets introduced. An undetected hand is
 * null rather than a frame of zeros, for the same reason it is there too:
 * zeros are indistinguishable from a hand resting at the origin.
 *
 * Never returns the same HandFrame in both slots, and never puts a frame in
 * the slot that disagrees with its own `handedness` -- both slots are filled
 * from the frame's own field, so `pair.left.handedness === "left"` holds by
 * construction rather than by care. */
export function mediapipeResultToHandPair(
  result: MediapipeHandResult,
  options: MediapipePairConversionOptions = {},
): HandPair {
  const bounds = options.controlVolume ?? DEFAULT_CONTROL_VOLUME;
  const mirroredPreview = options.mirroredPreview ?? true;
  const pair: HandPair = { left: null, right: null };

  for (let i = 0; i < result.handedness.length; i += 1) {
    const category = result.handedness[i]?.[0];
    if (!category) continue;

    // Resolve the side *before* converting, for two reasons: it selects which
    // per-hand smoothing filter this detection belongs to, and it lets a
    // duplicate classification (MediaPipe can, rarely, report two "Right"
    // hands) be dropped without first running it through -- and polluting --
    // that side's smoothing history. First detection wins, matching
    // readBothHands.
    const side = resolveHandSide(category, mirroredPreview);
    if (pair[side] !== null) continue;

    const smoothing = options.smoothing?.[side];
    const hand = convertHandAtIndex(result, i, bounds, mirroredPreview, smoothing);
    if (!hand) continue;

    // Bucket on the frame's own handedness, not on `side`. The two are equal
    // by construction (convertHandAtIndex resolves the side through the same
    // `resolveHandSide`), and routing on the frame's field is what keeps that
    // an invariant instead of a coincidence.
    if (hand.handedness === "left") pair.left = hand;
    else pair.right = hand;
  }

  return pair;
}

/**
 * Per-hand tracking-loss bookkeeping for the two-handed stream.
 *
 * Generalizes the single-hand rule (`start`'s `missCount`) rather than
 * replacing it: a brief dropout holds the last state, and only a sustained
 * one is announced as an explicit loss, exactly once, on the frame it crosses
 * the threshold. What changes is that the counters are per hand, because the
 * hands occlude each other and leave frame independently -- one shared
 * counter would let a steadily-tracked left hand keep resetting the right
 * hand's dropout, so a right hand that vanished entirely would never be
 * reported lost.
 *
 * "Holds the last state" also has to mean something different here. The
 * single-hand path holds by *not calling back at all*, leaving the caller
 * sitting on its last frame. A pair callback fires as soon as either hand has
 * fresh data, so the briefly-lost hand's slot has to be filled with
 * something, and the last frame it was seen in is that something -- the
 * caller ends up holding the same value it would have held anyway. When
 * neither hand has anything fresh and nothing crossed the threshold, this
 * returns null and the callback is skipped, which is the original behaviour
 * unchanged.
 */
export class HandPairTracking {
  private readonly missCount: { left: number; right: number } = { left: 0, right: 0 };
  private readonly lastSeen: HandPair = { left: null, right: null };

  constructor(private readonly lossThreshold: number) {}

  /** The pair to emit for this frame, or null to emit nothing at all. */
  update(fresh: HandPair): HandPair | null {
    const emit: HandPair = { left: null, right: null };
    let anyFresh = false;
    let crossedLossThreshold = false;

    for (const side of ["left", "right"] as const) {
      const hand = fresh[side];
      if (hand) {
        anyFresh = true;
        this.missCount[side] = 0;
        this.lastSeen[side] = hand;
        emit[side] = hand;
        continue;
      }

      this.missCount[side] += 1;
      if (this.missCount[side] >= this.lossThreshold) {
        // Equality, not >=, decides the announcement: the loss is news once,
        // and every frame after it is the same already-reported state.
        if (this.missCount[side] === this.lossThreshold) crossedLossThreshold = true;
        // Forget the held frame as well, so that if the *other* hand comes
        // back later this one is reported as still absent rather than
        // resurrected from a frame that is by now seconds stale.
        this.lastSeen[side] = null;
        emit[side] = null;
      } else {
        emit[side] = this.lastSeen[side];
      }
    }

    return anyFresh || crossedLossThreshold ? emit : null;
  }
}

// ------------------------------------------------------------- provider --

export interface WebcamHandProviderOptions {
  cameraDeviceId?: string;
  width?: number;
  height?: number;
  requestedFps?: number;
  maxHands?: number;
  detectionConfidence?: number;
  presenceConfidence?: number;
  trackingConfidence?: number;
  controlVolume?: ControlVolumeBounds;
  mirroredPreview?: boolean;
  smoothingAlpha?: number;
  /** Consecutive missed-detection frames tolerated before emitting a `null`
   * (explicit tracking-lost) frame -- spec section 24: brief loss holds the
   * last state (skip the callback entirely), only a sustained loss pauses
   * the stream. */
  trackingLossFrames?: number;
  modelAssetUrl?: string;
  wasmBaseUrl?: string;
  delegate?: "CPU" | "GPU";
}

// MediaPipe's own hosted assets -- avoids committing a multi-MB binary and
// matches how MediaPipe's own quickstarts load these. Requires internet on
// first run; a disclosed tradeoff (see STATE.md), not an oversight.
const DEFAULT_WASM_BASE_URL =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.17/wasm";
const DEFAULT_MODEL_ASSET_URL =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task";

export interface WebcamStatus {
  cameraFps: number;
  resultFps: number;
  mode: "screen_control";
  depthQuality: "estimated";
  /** Which hand the single-hand readouts below describe. Under `startPair`
   * both hands may be tracked at once and this names the one being reported
   * on -- right when it is present, otherwise left -- because this is a HUD
   * line, and preferring right matches `preferredHandIndex`'s own choice so
   * the two streams label the same hand the same way. */
  handedness: "left" | "right" | null;
  /** Pinch state of the hand `handedness` names. Both hands' hysteresis is
   * still advanced every frame under `startPair`; only the reported one is
   * narrowed to a single boolean, which is all this status line can hold. */
  pinchActive: boolean;
  trackingLost: boolean;
  resolutionWidth: number;
  resolutionHeight: number;
}

export class WebcamHandProvider {
  private raf: number | undefined;
  private readonly smoothingState: SmoothingState;
  private readonly pinch = new PinchHysteresis();
  // The two-handed path gets its own filters and its own pinch hysteresis per
  // hand rather than sharing the single-hand ones, so that `start` and
  // `startPair` cannot contaminate each other's history and so that neither
  // hand's smoothing or pinch deadband is driven by the other's motion. See
  // HandPairSmoothingState for why sharing is not merely untidy but wrong.
  private readonly pairSmoothingState: HandPairSmoothingState;
  private readonly pairPinch = {
    left: new PinchHysteresis(),
    right: new PinchHysteresis(),
  };
  private missCount = 0;
  private resultTimestampsMs: number[] = [];
  private status: WebcamStatus;

  private constructor(
    private readonly video: HTMLVideoElement,
    private readonly stream: MediaStream,
    private readonly landmarker: HandLandmarker,
    private readonly options: WebcamHandProviderOptions,
  ) {
    this.smoothingState = { alpha: options.smoothingAlpha ?? 0.35, previous: new Map() };
    this.pairSmoothingState = createHandPairSmoothingState(options.smoothingAlpha ?? 0.35);
    this.status = {
      cameraFps: 0,
      resultFps: 0,
      mode: "screen_control",
      depthQuality: "estimated",
      handedness: null,
      pinchActive: false,
      trackingLost: false,
      resolutionWidth: video.videoWidth,
      resolutionHeight: video.videoHeight,
    };
  }

  /** Opens the camera and loads MediaPipe. Reads back what the device
   * actually granted (spec section 9) rather than assuming the request was
   * honored exactly. */
  static async create(options: WebcamHandProviderOptions = {}): Promise<WebcamHandProvider> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("getUserMedia is not available -- no webcam access in this browser/context");
    }
    const videoConstraints: MediaTrackConstraints = {
      width: { ideal: options.width ?? 1280 },
      height: { ideal: options.height ?? 720 },
      frameRate: { ideal: options.requestedFps ?? 30 },
    };
    if (options.cameraDeviceId !== undefined) {
      videoConstraints.deviceId = options.cameraDeviceId;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints });
    } catch (error) {
      throw new Error(`Camera could not be opened: ${String(error)}`);
    }

    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await video.play();
    if (video.videoWidth === 0) {
      await new Promise<void>((resolve) => {
        video.addEventListener("loadedmetadata", () => resolve(), { once: true });
      });
    }

    const vision = await FilesetResolver.forVisionTasks(options.wasmBaseUrl ?? DEFAULT_WASM_BASE_URL);
    const landmarker = await HandLandmarker.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: options.modelAssetUrl ?? DEFAULT_MODEL_ASSET_URL,
        delegate: options.delegate ?? "GPU",
      },
      runningMode: "VIDEO",
      numHands: options.maxHands ?? 2,
      minHandDetectionConfidence: options.detectionConfidence ?? 0.5,
      minHandPresenceConfidence: options.presenceConfidence ?? 0.5,
      minTrackingConfidence: options.trackingConfidence ?? 0.5,
    });

    return new WebcamHandProvider(video, stream, landmarker, options);
  }

  /** The live camera preview, for the split-screen webcam panel (spec
   * section 48). The same stream MediaPipe reads -- no second capture. */
  get videoElement(): HTMLVideoElement {
    return this.video;
  }

  getStatus(): WebcamStatus {
    return { ...this.status };
  }

  /** Stream one hand, the single-hand teleop path. Unchanged, and still the
   * right entry point for anything driving a single end-effector -- see
   * `startPair` for the bimanual one. The two share one rAF handle, so only
   * one may be running at a time; `stop` cancels whichever it is. */
  start(onFrame: (hand: HandFrame | null) => void): void {
    const lossThreshold = this.options.trackingLossFrames ?? 5;
    const tick = (): void => {
      const nowMs = performance.now();
      let result: HandLandmarkerResult;
      try {
        result = this.landmarker.detectForVideo(this.video, nowMs);
      } catch (error) {
        console.warn("MediaPipe detectForVideo failed on this frame:", error);
        this.raf = requestAnimationFrame(tick);
        return;
      }

      const conversionOptions: MediapipeConversionOptions = { smoothing: this.smoothingState };
      if (this.options.controlVolume !== undefined) {
        conversionOptions.controlVolume = this.options.controlVolume;
      }
      if (this.options.mirroredPreview !== undefined) {
        conversionOptions.mirroredPreview = this.options.mirroredPreview;
      }
      const hand = mediapipeResultToHandFrame(result, conversionOptions);

      this.recordResultTiming(nowMs);
      if (hand) {
        this.missCount = 0;
        this.status = {
          ...this.status,
          handedness: hand.handedness,
          pinchActive: this.pinch.update(hand.pinchApertureM),
          trackingLost: false,
          resolutionWidth: this.video.videoWidth,
          resolutionHeight: this.video.videoHeight,
        };
        onFrame(hand);
      } else {
        this.missCount += 1;
        if (this.missCount === lossThreshold) {
          this.status = { ...this.status, handedness: null, pinchActive: false, trackingLost: true };
          onFrame(null);
        }
        // Under threshold: skip the callback -- the caller naturally holds
        // its last state, same as every other frame nothing is emitted for.
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  /** Stream both hands as a `HandPair`, for the bimanual recording path.
   *
   * A peer of `start`, not a replacement: the callback signature differs
   * (a pair, never null -- an absent hand is a null *slot*), so existing
   * single-hand callers are untouched. Emission follows the same
   * tracking-loss rule as `start`, generalized per hand by `HandPairTracking`
   * -- brief dropouts hold, a sustained one is announced once. */
  startPair(onFrame: (hands: HandPair) => void): void {
    // Both loops share `this.raf`; starting a second one without cancelling
    // the first would leave an orphaned loop that `stop` can never reach.
    if (this.raf !== undefined) cancelAnimationFrame(this.raf);
    const tracking = new HandPairTracking(this.options.trackingLossFrames ?? 5);
    const tick = (): void => {
      const nowMs = performance.now();
      let result: HandLandmarkerResult;
      try {
        result = this.landmarker.detectForVideo(this.video, nowMs);
      } catch (error) {
        console.warn("MediaPipe detectForVideo failed on this frame:", error);
        this.raf = requestAnimationFrame(tick);
        return;
      }

      const conversionOptions: MediapipePairConversionOptions = {
        smoothing: this.pairSmoothingState,
      };
      if (this.options.controlVolume !== undefined) {
        conversionOptions.controlVolume = this.options.controlVolume;
      }
      if (this.options.mirroredPreview !== undefined) {
        conversionOptions.mirroredPreview = this.options.mirroredPreview;
      }
      const fresh = mediapipeResultToHandPair(result, conversionOptions);

      this.recordResultTiming(nowMs);
      const hands = tracking.update(fresh);
      if (hands) {
        this.status = { ...this.status, ...this.pairStatus(hands) };
        onFrame(hands);
      }
      // `hands === null` is the pair-shaped version of the single-hand path's
      // skipped callback: nothing fresh, nothing newly lost, so the caller
      // keeps what it already has.
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop(): void {
    if (this.raf !== undefined) cancelAnimationFrame(this.raf);
    this.raf = undefined;
    for (const track of this.stream.getTracks()) track.stop();
    this.landmarker.close();
  }

  /** Collapses a pair down to the single-hand shape `WebcamStatus` can hold.
   *
   * Both hands' pinch hysteresis is advanced every frame regardless of which
   * one gets reported -- a deadband that only ran while its hand happened to
   * be the reported one would latch on stale data the moment the other hand
   * appeared. An absent hand is fed `null`, which `PinchHysteresis` already
   * treats as "hold", so a hand that briefly disappears does not spuriously
   * release its pinch. */
  private pairStatus(
    hands: HandPair,
  ): Pick<
    WebcamStatus,
    "handedness" | "pinchActive" | "trackingLost" | "resolutionWidth" | "resolutionHeight"
  > {
    const leftPinch = this.pairPinch.left.update(hands.left?.pinchApertureM ?? null);
    const rightPinch = this.pairPinch.right.update(hands.right?.pinchApertureM ?? null);
    const reported = hands.right ?? hands.left;
    return {
      handedness: reported?.handedness ?? null,
      pinchActive: hands.right ? rightPinch : hands.left ? leftPinch : false,
      trackingLost: hands.left === null && hands.right === null,
      resolutionWidth: this.video.videoWidth,
      resolutionHeight: this.video.videoHeight,
    };
  }

  private recordResultTiming(nowMs: number): void {
    this.resultTimestampsMs.push(nowMs);
    const windowMs = 1000;
    while (
      this.resultTimestampsMs.length > 1 &&
      nowMs - this.resultTimestampsMs[0]! > windowMs
    ) {
      this.resultTimestampsMs.shift();
    }
    const spanMs = nowMs - (this.resultTimestampsMs[0] ?? nowMs);
    const fps = spanMs > 0 ? ((this.resultTimestampsMs.length - 1) * 1000) / spanMs : 0;
    this.status = { ...this.status, resultFps: fps, cameraFps: fps };
  }
}
