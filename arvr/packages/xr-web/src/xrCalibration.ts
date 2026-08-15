/**
 * Workspace calibration for the openxr hand path — what makes `HAND=openxr`
 * mean something on a real headset instead of failing IK on every frame.
 *
 * The problem this solves is not cosmetic. WebXR's `local-floor` origin sits
 * on the floor under wherever the headset booted, so a human standing there
 * has their hand at roughly (0, 1.2, -0.4) in WebXR space -- about 1.3m from
 * the origin. The SO-101 is a desktop arm with ~35cm of reach whose base is
 * at struct_world [0,0,0]. Feeding raw tracked hand poses into the retargeter
 * therefore asks the robot to reach a point three times further away than it
 * physically can, every single frame, and the only symptom is `ik_status`
 * stuck at a failure. `spatialTeachMain.ts`'s CALIBRATE was an admitted
 * no-op stand-in; on a headset it is the difference between a working demo
 * and a frozen arm.
 *
 * The fix is the transform the codebase already had and only used in the old
 * app's TWIN mode: `alignment.ts`'s two-anchor solve. The human pinches at
 * the two points below, in their own room, and that fixes where the robot's
 * workspace lives:
 *
 *     T_struct_to_room  ->  places the rendered scene in the room
 *     T_room_to_struct  ->  maps every tracked hand frame into struct_world
 *
 * Both directions are used at once, which is why `invertAlignment` exists.
 *
 * Deliberately NOT scale-corrected. The two anchors are 38cm apart because
 * that is how far apart the robot's base and the button really are; a human
 * who pinches two points 80cm apart has laid out a workspace the robot does
 * not have, and `anchorErrorM` says so rather than quietly stretching the
 * demo to fit. That measured error is the gate (spec's "anchor reprojection
 * error < 5cm"), not a judgement call -- `isCalibrated` is false until the
 * number is good, and the HUD shows the number either way.
 */

import {
  applyAlignment,
  invertAlignment,
  reprojectionErrorM,
  solveAlignment,
  type Alignment,
  type Anchor,
} from "./alignment";
import { webxrToStruct } from "./adapter";
import type { Vec3 } from "./contracts";
import type { HandFrame } from "./hands";
import {
  toWireHandFrame,
  type HandSourceDeviceWire,
  type WireHandFrame,
} from "./liveRetargetSession";
import { ASSET_WORLD_POSITION, ROBOT_BASE_STRUCT } from "./spatialTeachLayout";

// The room<->struct transform is alignment.ts's, in both directions and in
// both position and orientation form. Re-exported here so callers wiring up
// the openxr path have one import to reach for.
export { composeQuaternions, invertAlignment, yawQuaternion } from "./alignment";

/** Spec section 49's target, used here as a hard gate rather than a note. */
export const ANCHOR_ERROR_LIMIT_M = 0.05;

export interface CalibrationAnchor {
  /** Where this point lives in struct_world -- the same constants the
   * renderer places the real objects at (spatialTeachLayout.ts). */
  structPosition: Vec3;
  /** Shown in the HUD while this anchor is the one being captured. */
  prompt: string;
  label: string;
}

/**
 * The two points, in capture order.
 *
 * Anchor A alone fixes the translation; the A->B vector fixes the yaw. Two
 * is the minimum that pins both, and the spec explicitly prefers deterministic
 * manual anchors over waiting on automatic relocalization.
 */
export const CALIBRATION_ANCHORS: readonly CalibrationAnchor[] = [
  {
    structPosition: ROBOT_BASE_STRUCT,
    label: "ROBOT BASE",
    prompt: "Pinch where the robot's base should stand",
  },
  {
    structPosition: ASSET_WORLD_POSITION,
    label: "BUTTON",
    prompt: "Now pinch where the button should sit",
  },
];

/** How far apart a pair of anchors really are, in meters. Surfaced so the
 * HUD can tell the human the distance to aim for instead of making them
 * guess -- and so a scene that defines its own anchors gets the right
 * number rather than the button task's. */
export function anchorSeparationM(
  anchors: readonly CalibrationAnchor[] = CALIBRATION_ANCHORS,
): number {
  const [a, b] = anchors;
  if (!a || !b) throw new Error("calibration needs exactly two anchors");
  return Math.hypot(
    b.structPosition[0] - a.structPosition[0],
    b.structPosition[1] - a.structPosition[1],
    b.structPosition[2] - a.structPosition[2],
  );
}

/** The button task's own separation, kept as a constant for its call sites. */
export const ANCHOR_SEPARATION_M = anchorSeparationM(CALIBRATION_ANCHORS);

/** Where three.js should put the scene root, in WebXR space. */
export interface ScenePlacement {
  /** Rotation about WebXR +Y. Equal to the struct-frame yaw about +Z: the
   * basis change between the two frames maps one axis onto the other, so a
   * yaw is the one rotation that survives it unchanged. */
  yaw: number;
  position: Vec3;
}

export class XrCalibration {
  /** Pinch points as captured, in WebXR space. */
  private captured: Vec3[] = [];
  /** T_struct_to_room. Set as soon as both anchors exist, even when the
   * error is too large -- the human needs to see how far off they were. */
  alignment: Alignment | undefined;
  anchorErrorM: number | undefined;

  /** Which two points this scene calibrates against. Defaults to the button
   * task's; the sort scene passes its own, because the landmarks a human can
   * point at differ per scene while the solve does not. */
  constructor(readonly anchors: readonly CalibrationAnchor[] = CALIBRATION_ANCHORS) {
    if (anchors.length !== 2) {
      throw new Error(`calibration needs exactly two anchors, got ${anchors.length}`);
    }
  }

  get anchorsCaptured(): number {
    return this.captured.length;
  }

  /** The anchor the human should place next, or undefined when done. */
  get nextAnchor(): CalibrationAnchor | undefined {
    return this.anchors[this.captured.length];
  }

  /** Both anchors placed AND the layout actually matches the robot's scale. */
  get isCalibrated(): boolean {
    return this.alignment !== undefined && (this.anchorErrorM ?? Infinity) <= ANCHOR_ERROR_LIMIT_M;
  }

  /** Record one pinch. Extra captures past the last anchor are ignored, so a
   * doubled pinch event cannot silently reopen a finished calibration. */
  captureAnchor(webxrPosition: Vec3): void {
    if (this.captured.length >= this.anchors.length) return;
    this.captured.push([...webxrPosition] as Vec3);
    if (this.captured.length === this.anchors.length) this.solve();
  }

  reset(): void {
    this.captured = [];
    this.alignment = undefined;
    this.anchorErrorM = undefined;
  }

  private solve(): void {
    const anchors: Anchor[] = this.anchors.map((anchor, i) => ({
      structPosition: anchor.structPosition,
      // The pinch arrives in WebXR space; alignment.ts works in the Z-up
      // struct convention, so convert before solving rather than teaching
      // the solver a second axis convention.
      tappedPosition: webxrToStruct.position(this.captured[i]!),
    }));
    this.alignment = solveAlignment(anchors[0]!, anchors[1]!);
    // Anchor A reprojects exactly by construction (it fixes the translation),
    // so B's error is the one that carries information.
    this.anchorErrorM = reprojectionErrorM(this.alignment, anchors[1]!);
  }

  /**
   * T_room_to_struct — the transform every tracked hand frame is mapped
   * through on its way to the recorder and the retargeter.
   *
   * Handed out rather than applied here so there is exactly one conversion
   * function in the client (`toWireHandFrame`) that both the calibrated and
   * uncalibrated providers go through. Undefined until the gate passes, which
   * is what stops uncalibrated frames from being recorded as though they were
   * in struct_world.
   */
  get roomToStruct(): Alignment | undefined {
    if (!this.isCalibrated || !this.alignment) return undefined;
    return invertAlignment(this.alignment);
  }

  /** Where to put the scene root so the rendered robot stands on the spot the
   * human pinched for it. Undefined until calibration passes its gate. */
  scenePlacement(): ScenePlacement | undefined {
    if (!this.isCalibrated || !this.alignment) return undefined;
    const [tx, ty, tz] = this.alignment.translation;
    return { yaw: this.alignment.yaw, position: [-ty, tz, -tx] };
  }

  /**
   * One tracked hand -> the canonical struct_world wire frame.
   *
   * Returns null while uncalibrated. That is the point: without the anchors
   * there is no honest answer, and passing raw device coordinates through
   * would produce a well-formed frame full of numbers that mean nothing to
   * the robot -- exactly the silent failure this module exists to remove.
   */
  handToStruct(
    hand: HandFrame,
    timestampNs = 0,
    sourceDevice: HandSourceDeviceWire = "openxr",
  ): WireHandFrame | null {
    if (!this.isCalibrated || !this.alignment) return null;
    return toWireHandFrame(hand, timestampNs, sourceDevice, invertAlignment(this.alignment));
  }

  /** struct_world point -> where it belongs in the room, for anything the
   * client positions outside the scene root. */
  structToRoom(point: Vec3): Vec3 | undefined {
    if (!this.isCalibrated || !this.alignment) return undefined;
    const [x, y, z] = applyAlignment(this.alignment, point);
    return [-y, z, -x];
  }
}
