/**
 * WebXR Hand Input -> the recording schema in master plan §8.
 *
 *     {t, head_pose, left_hand: 26 joints, right_hand: 26 joints,
 *      object_poses[], events[]}
 *
 * and §8's datapipe step 1:
 *
 *     wrist pose -> 6-DoF target
 *     pinch aperture (thumb-index distance) -> gripper command
 *
 * That is the whole reason hand tracking matters here: the human's hand IS the
 * input device, so this is where physical-world motion enters the system. A
 * controller trigger would be a different signal and would not retarget the
 * same way.
 */

/** The 26 joints WebXR reports per hand, in the spec's canonical order. */
export const HAND_JOINTS = [
  "wrist",
  "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
  "index-finger-metacarpal", "index-finger-phalanx-proximal",
  "index-finger-phalanx-intermediate", "index-finger-phalanx-distal", "index-finger-tip",
  "middle-finger-metacarpal", "middle-finger-phalanx-proximal",
  "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal", "middle-finger-tip",
  "ring-finger-metacarpal", "ring-finger-phalanx-proximal",
  "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal", "ring-finger-tip",
  "pinky-finger-metacarpal", "pinky-finger-phalanx-proximal",
  "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal", "pinky-finger-tip",
] as const;

// WebXR defines 25 named joints; the spec's "26" counts the palm, which some
// runtimes expose and others do not. We record what the runtime gives us and
// say how many, rather than padding to hit a number.
export const PALM_JOINT = "palm";

export type JointPose = {
  position: [number, number, number];
  orientation: [number, number, number, number];
  radius: number | null;
};

export interface HandFrame {
  handedness: "left" | "right";
  /** Joint name -> pose, only for joints the runtime actually tracked. */
  joints: Record<string, JointPose>;
  /** Thumb-tip to index-tip distance, meters. null when either is untracked. */
  pinchApertureM: number | null;
  /** Derived gripper command in [0,1]: 1 = closed. */
  gripper: number;
}

/**
 * Aperture at which the gripper reads fully closed / fully open.
 *
 * Measured against adult hands: tips touching is ~1.5 cm apart (the fingers
 * have thickness), a relaxed open pinch is ~8 cm. Clamping between those
 * rather than using the raw distance means the gripper saturates instead of
 * asking for a grip the robot cannot make.
 */
export const PINCH_CLOSED_M = 0.015;
export const PINCH_OPEN_M = 0.08;

export function gripperFromAperture(apertureM: number | null): number {
  if (apertureM === null || !Number.isFinite(apertureM)) return 0;
  const t = (PINCH_OPEN_M - apertureM) / (PINCH_OPEN_M - PINCH_CLOSED_M);
  return Math.min(1, Math.max(0, t));
}

export function distance(a: JointPose, b: JointPose): number {
  return Math.hypot(
    a.position[0] - b.position[0],
    a.position[1] - b.position[1],
    a.position[2] - b.position[2],
  );
}

/**
 * Read one hand for this frame.
 *
 * Returns null when the hand is not being tracked at all -- an untracked hand
 * is absent from the record, never a frame of zeros, because zeros would
 * retarget into a real robot pose at the origin.
 */
export function readHand(
  frame: XRFrame,
  inputSource: XRInputSource,
  referenceSpace: XRReferenceSpace,
): HandFrame | null {
  const hand = inputSource.hand;
  if (!hand) return null;

  const joints: Record<string, JointPose> = {};
  const names = [...HAND_JOINTS, PALM_JOINT];

  for (const name of names) {
    const jointSpace = hand.get(name as unknown as never);
    if (!jointSpace) continue;

    const pose = frame.getJointPose?.(jointSpace, referenceSpace);
    if (!pose) continue;

    const { position: p, orientation: o } = pose.transform;
    joints[name] = {
      position: [p.x, p.y, p.z],
      orientation: [o.x, o.y, o.z, o.w],
      radius: pose.radius ?? null,
    };
  }

  if (Object.keys(joints).length === 0) return null;

  const thumb = joints["thumb-tip"];
  const index = joints["index-finger-tip"];
  const pinchApertureM = thumb && index ? distance(thumb, index) : null;

  return {
    handedness: inputSource.handedness === "left" ? "left" : "right",
    joints,
    pinchApertureM,
    gripper: gripperFromAperture(pinchApertureM),
  };
}

/** The 6-DoF end-effector target: the wrist pose (§8 datapipe step 1). */
export function wristTarget(hand: HandFrame): JointPose | null {
  return hand.joints["wrist"] ?? null;
}
