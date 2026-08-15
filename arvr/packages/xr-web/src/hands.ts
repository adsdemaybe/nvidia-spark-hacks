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
 * Which tracked hand should drive the arm, when a session tracks more than
 * one at once. The whole downstream pipeline (recorder, ArmRetargeter,
 * LiveRetargetSession) assumes a single hand drives one end-effector --
 * spec section 26's ordinary ArmRetargeter, not a two-handed one. Calling
 * back once per tracked hand instead of picking one would interleave
 * left+right frames into one buffer and send the server alternating,
 * unrelated targets every other frame -- a real bug a WebXR session
 * exercises by default whenever both hands are in view, not a hypothetical.
 * Prefers right (matches the mock fixture's own handedness).
 */
export function preferredInputSource(session: XRSession): XRInputSource | undefined {
  let fallback: XRInputSource | undefined;
  for (const source of session.inputSources) {
    if (!source.hand) continue;
    if (source.handedness === "right") return source;
    fallback ??= source;
  }
  return fallback;
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

/**
 * Both hands for one frame, each slot null when that hand is untracked.
 *
 * A pair rather than an array because the two hands are not interchangeable
 * to any caller that consumes them: a renderer colors them differently and a
 * room-scale interaction asks "is the left hand doing X" by name. An array
 * would push every caller into re-deriving the same `find(h => h.handedness
 * === "left")` lookup, which is exactly where a left/right mix-up gets
 * introduced.
 */
export interface HandPair {
  left: HandFrame | null;
  right: HandFrame | null;
}

/**
 * Read every tracked hand for this frame, bucketed by handedness.
 *
 * This is the *other* half of the story `preferredInputSource` tells. That
 * function exists because the robot-teleop path -- recorder, ArmRetargeter,
 * LiveRetargetSession -- drives a single end-effector, so feeding it both
 * hands would interleave unrelated targets into one buffer. That reasoning
 * is untouched and still governs teleop. But a room-scale interaction (the
 * ball-pit demo) has no end-effector at all: the hands are the manipulators,
 * both of them, and dropping one is the bug rather than the fix. So this
 * reads both and keeps them separate, instead of relaxing the single-hand
 * rule that teleop still depends on.
 *
 * Each hand goes through the same `readHand` as the single-hand path, so an
 * untracked hand stays null here for the same reason it does there: zeros
 * would be indistinguishable from a hand resting at the origin.
 */
export function readBothHands(
  frame: XRFrame,
  session: XRSession,
  referenceSpace: XRReferenceSpace,
): HandPair {
  const pair: HandPair = { left: null, right: null };

  for (const source of session.inputSources) {
    // Controllers and other non-hand input sources share `inputSources` with
    // hands, and a controller reports a handedness too -- skipping them here
    // rather than trusting `readHand`'s null keeps a held controller from
    // ever occupying the slot its hand belongs in.
    if (!source.hand) continue;

    const hand = readHand(frame, source, referenceSpace);
    if (!hand) continue;

    // Bucket on the frame's own `handedness` field, never on the input
    // source's. They are derived from the same value, but routing by the
    // frame's field makes `pair.left.handedness === "left"` true by
    // construction, so a slot can never disagree with the frame sitting in
    // it -- a swap would have to be a deliberate edit, not an oversight.
    // `readHand` maps anything not "left" to "right", so a source reporting
    // handedness "none" lands in `right` consistently in both places.
    if (hand.handedness === "left") {
      pair.left ??= hand;
    } else {
      pair.right ??= hand;
    }
  }

  return pair;
}

/** The 6-DoF end-effector target: the wrist pose (§8 datapipe step 1). */
export function wristTarget(hand: HandFrame): JointPose | null {
  return hand.joints["wrist"] ?? null;
}

/**
 * Where a pinch "happens": midway between the thumb and index tips.
 *
 * Used to place calibration anchors. Neither tip on its own is what a human
 * means when they pinch at a spot -- they mean the point the two fingers
 * close on, and being a centimeter off matters when the whole calibration
 * budget is five.
 */
export function pinchPoint(hand: HandFrame): [number, number, number] | null {
  const thumb = hand.joints["thumb-tip"];
  const index = hand.joints["index-finger-tip"];
  if (!thumb || !index) return null;
  return [
    (thumb.position[0] + index.position[0]) / 2,
    (thumb.position[1] + index.position[1]) / 2,
    (thumb.position[2] + index.position[2]) / 2,
  ];
}

/** The index fingertip — what pokes the in-scene HUD (xrHud.ts). */
export function pokePoint(hand: HandFrame): [number, number, number] | null {
  return hand.joints["index-finger-tip"]?.position ?? null;
}

/** Gripper closure at which a pinch counts as made, and the looser value it
 * must fall back below before another pinch can be made. Two thresholds, not
 * one: a hand held at exactly the boundary would otherwise emit a stream of
 * pinches as tracking noise crosses it. */
export const PINCH_ENGAGE = 0.85;
export const PINCH_RELEASE = 0.6;

/**
 * How curled the fingers are, 0 = flat open hand, 1 = closed fist.
 *
 * Thumb-to-index aperture describes a *precision pinch* and nothing else. It
 * is the right signal for a parallel gripper and for picking up something
 * small, and it is completely wrong for a power grasp: wrap your hand around
 * a 66mm can and your thumb and index finger end up roughly 7cm apart, which
 * `gripperFromAperture` reports as a fully OPEN hand. A task built on pinch
 * alone therefore cannot detect the most natural way to pick up a can.
 *
 * Curl is measured instead as how far the four fingertips sit from the wrist,
 * averaged. That falls as the fingers close regardless of whether they are
 * closing onto each other, onto the thumb, or around an object -- so it reads
 * a pinch and a power grasp alike.
 */
export function graspClosure(hand: HandFrame): number {
  const wrist = hand.joints["wrist"];
  if (!wrist) return 0;

  const tips = [
    "index-finger-tip",
    "middle-finger-tip",
    "ring-finger-tip",
    "pinky-finger-tip",
  ];

  let total = 0;
  let counted = 0;
  for (const name of tips) {
    const tip = hand.joints[name];
    if (!tip) continue;
    total += distance(wrist, tip);
    counted += 1;
  }
  // No fingers tracked is not a closed hand. Returning 0 keeps a partially
  // tracked hand from reading as a grab, the same way an untracked pinch
  // reports open rather than closed.
  if (counted === 0) return 0;

  const mean = total / counted;
  const t = (GRIP_OPEN_M - mean) / (GRIP_OPEN_M - GRIP_CLOSED_M);
  return Math.min(1, Math.max(0, t));
}

/**
 * Fingertip-to-wrist distances for an open hand and a closed fist.
 *
 * An adult hand runs about 18cm wrist to fingertip fully extended; curled
 * into a fist the tips sit roughly 7cm from the wrist. The range below is
 * deliberately narrower than that at the open end, because a hand *reaching*
 * for something is already slightly curled and should not have to be
 * splayed flat to read as open.
 */
export const GRIP_OPEN_M = 0.135;
export const GRIP_CLOSED_M = 0.075;

/** Grip closure at which a grab is made, and the looser value it must fall
 * back below to release. Lower and wider apart than the pinch thresholds:
 * curl is a coarser signal than tip separation, and a power grasp does not
 * close as completely as a fist. */
export const GRIP_ENGAGE = 0.55;
export const GRIP_RELEASE = 0.35;

/**
 * Edge-triggered grab detection for whole-hand grasps.
 *
 * Same hysteresis discipline as {@link PinchLatch} -- and a separate class
 * rather than a parameter on that one, because the two read different
 * signals and a task should say which it means.
 */
export class GripLatch {
  private engaged = false;

  get isEngaged(): boolean {
    return this.engaged;
  }

  /** True on the frame the grab closes. A lost hand releases. */
  update(hand: HandFrame | null): boolean {
    if (!hand) {
      this.engaged = false;
      return false;
    }
    const closure = graspClosure(hand);
    if (this.engaged) {
      if (closure < GRIP_RELEASE) this.engaged = false;
      return false;
    }
    if (closure >= GRIP_ENGAGE) {
      this.engaged = true;
      return true;
    }
    return false;
  }

  reset(): void {
    this.engaged = false;
  }
}

/**
 * Edge-triggered pinch detection.
 *
 * A calibration anchor is placed on the *transition* into a pinch. Reading
 * the level instead would place an anchor every frame the fingers are
 * touching, and the last one -- captured as the hand pulls away -- would be
 * the one that counted.
 */
export class PinchLatch {
  private engaged = false;

  get isEngaged(): boolean {
    return this.engaged;
  }

  /** True exactly on the frame the pinch closes. A lost hand releases.
   *
   * Takes anything carrying a gripper reading, not a full `HandFrame`, so
   * the same latch works on an already-converted struct_world frame without
   * a second copy of the hysteresis. */
  update(hand: { gripper: number } | null): boolean {
    if (!hand) {
      this.engaged = false;
      return false;
    }
    if (this.engaged) {
      if (hand.gripper < PINCH_RELEASE) this.engaged = false;
      return false;
    }
    if (hand.gripper >= PINCH_ENGAGE) {
      this.engaged = true;
      return true;
    }
    return false;
  }

  reset(): void {
    this.engaged = false;
  }
}
