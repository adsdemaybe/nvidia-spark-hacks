/**
 * Spatial adapters (ar-xr-plan.md 5, 28).
 *
 * This file is the entire reason a headset does not create a new robot backend.
 * A tracked controller, a hand, or a mouse all land in the same SpatialFrame,
 * and everything downstream is blind to which one produced it.
 *
 * The conversion that actually matters is the coordinate frame. WebXR and
 * three.js are Y-up with -Z forward; STRUCT is Z-up with +X forward. Getting
 * this wrong does not crash -- it silently produces a robot that reaches in the
 * wrong direction, which is the most expensive kind of bug to find on a device.
 */

import type { DeviceType, InputType, Quat, SpatialFrame, Vec3 } from "./contracts";
import { SCHEMA_VERSION } from "./contracts";
import { normalizeQuaternion } from "./spatial";

/** Raw pose off a device, in that device's own frame. */
export interface DevicePose {
  position: Vec3;
  orientation: Quat;
  /** 0 = open, 1 = fully squeezed. */
  trigger: number;
}

interface FrameConversion {
  position(v: Vec3): Vec3;
  quaternion(q: Quat): Quat;
}

/**
 * WebXR (X right, Y up, -Z forward) -> STRUCT (X forward, Y left, Z up).
 *
 * The basis change is a proper rotation (det = +1), so a rotation quaternion
 * converts by rotating its vector part and leaving w alone -- same axis
 * expressed in the new basis, same angle.
 */
export const webxrToStruct: FrameConversion = {
  position: ([x, y, z]) => [pz(-z), pz(-x), pz(y)],
  quaternion: ([x, y, z, w]) => [pz(-z), pz(-x), pz(y), pz(w)],
};

/** The inverse of {@link webxrToStruct}, for putting STRUCT poses back on screen. */
export const structToWebxr: FrameConversion = {
  position: ([x, y, z]) => [pz(-y), pz(z), pz(-x)],
  quaternion: ([x, y, z, w]) => [pz(-y), pz(z), pz(-x), pz(w)],
};

/**
 * Collapse -0 to +0. Negating an axis is how the basis change works, so -0 falls
 * out constantly. It survives JSON as "0" and Python treats it as equal, but JS
 * strict equality does not -- so a client comparing poses would see spurious
 * differences. Cheaper to normalize here than to explain later.
 */
function pz(v: number): number {
  return v === 0 ? 0 : v;
}

/** Anything that can turn a device pose into the one canonical representation. */
export interface SpatialAdapter {
  readonly deviceType: DeviceType;
  readonly inputType: InputType;
  toSpatialFrame(pose: DevicePose, timestampNs: number): SpatialFrame;
}

abstract class BaseAdapter implements SpatialAdapter {
  abstract readonly deviceType: DeviceType;
  abstract readonly inputType: InputType;

  toSpatialFrame(pose: DevicePose, timestampNs: number): SpatialFrame {
    return {
      schema_version: SCHEMA_VERSION,
      timestamp_ns: timestampNs,
      source: { device_type: this.deviceType, input_type: this.inputType },
      frame: "struct_world",
      position_m: webxrToStruct.position(pose.position),
      orientation_xyzw: normalizeQuaternion(webxrToStruct.quaternion(pose.orientation)),
      gripper: clamp01(pose.trigger),
    };
  }
}

/** A WebXR tracked controller. Trigger squeeze becomes gripper closure. */
export class XRControllerAdapter extends BaseAdapter {
  readonly deviceType = "xr_controller" as const;
  readonly inputType = "tracked_controller" as const;
}

/**
 * Mouse/keyboard stand-in, so the client is developable and demoable on a
 * machine with no headset attached. Marked distinctly on the wire so a
 * recorded episode is never mistaken for real device data.
 */
export class DesktopMockAdapter extends BaseAdapter {
  readonly deviceType = "desktop_mock" as const;
  readonly inputType = "mock" as const;
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.min(1, Math.max(0, v));
}
