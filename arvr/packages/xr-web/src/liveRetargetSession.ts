/**
 * Live retarget session — connection to ar_backend's `/spatial/live` stream
 * (spec section 47, Phase 7). Mirrors followSession.ts's shape exactly,
 * including the same "STOP closes the socket outright" discipline: there is
 * then nothing left that could keep producing shadow-robot motion.
 *
 * Sends hands.ts's WebXR-space HandFrame, converted to the wire's
 * struct_world convention via adapter.ts's `webxrToStruct` -- the same
 * conversion humanEpisodeRecorder.ts (Phase 8) runs when it stores a real
 * recording. Every place raw device data crosses into a canonical
 * ar_contracts shape does this same conversion; ShadowHand deliberately does
 * not (see its own docstring).
 */

import { webxrToStruct } from "./adapter";
import {
  applyAlignment,
  composeQuaternions,
  yawQuaternion,
  type Alignment,
} from "./alignment";
import type { Quat, Vec3 } from "./contracts";
import type { HandFrame } from "./hands";
import type { IkStatus } from "./shadowRobot";

/** Mirrors ar_contracts.RobotShadowState. */
export interface RobotShadowState {
  timestamp_ns: number;
  robot_id: string;
  joint_positions: number[];
  ik_status: IkStatus;
  end_effector: {
    position_m: [number, number, number];
    orientation_xyzw: [number, number, number, number];
  };
}

export type LiveRetargetSessionState = "connecting" | "open" | "closed";

/** Mirrors ar_contracts.HandFrame.source_device / HumanEpisodeMetadata.hand_provider. */
export type HandSourceDeviceWire = "openxr" | "phone" | "mock" | "webcam";

/** One joint on the wire. Mirrors ar_contracts.HandFrame's joint entry. */
export interface WireJoint {
  position_m: Vec3;
  orientation_xyzw: Quat;
}

/** Mirrors ar_contracts.HandFrame. Was an anonymous `object` until the
 * openxr path needed to read a frame back (xrCalibration's tests assert on
 * the mapped wrist position), which `object` makes impossible. */
export interface WireHandFrame {
  schema_version: string;
  timestamp_ns: number;
  source_device: HandSourceDeviceWire;
  hand: "left" | "right";
  frame: "struct_world";
  joints: Record<string, WireJoint>;
}

/**
 * Defaults to "openxr" only to keep pre-existing call sites (mock's replay
 * loop, before this default existed) working unchanged -- every real caller
 * should now pass its actual provider so an episode's recorded provenance
 * isn't silently mislabeled (this default previously mislabeled mock frames
 * as "openxr" too; see STATE.md).
 *
 * `roomToStruct` is the openxr path's workspace calibration (xrCalibration.ts).
 * A tracked hand arrives in coordinates relative to wherever the headset's
 * tracking origin happens to be; without this it would be treated as though
 * the room's origin were the robot's base. Omitted for every other provider,
 * whose frames are already authored in struct_world -- so this stays one
 * conversion function rather than a second, drifting copy.
 */
export function toWireHandFrame(
  hand: HandFrame,
  timestampNs: number,
  sourceDevice: HandSourceDeviceWire = "openxr",
  roomToStruct?: Alignment,
): WireHandFrame {
  const yaw = roomToStruct ? yawQuaternion(roomToStruct.yaw) : undefined;
  const joints: Record<string, WireJoint> = {};
  for (const [name, joint] of Object.entries(hand.joints)) {
    const position = webxrToStruct.position(joint.position);
    const orientation = webxrToStruct.quaternion(joint.orientation);
    joints[name] = roomToStruct
      ? {
          position_m: applyAlignment(roomToStruct, position),
          orientation_xyzw: normalize(composeQuaternions(yaw!, orientation)),
        }
      : { position_m: position, orientation_xyzw: orientation };
  }
  return {
    schema_version: "1.0",
    timestamp_ns: timestampNs,
    source_device: sourceDevice,
    hand: hand.handedness,
    frame: "struct_world",
    joints,
  };
}

/** Composing two unit quaternions drifts off the unit sphere by float error
 * only, so this rescales rather than validating -- a per-frame path is the
 * wrong place to throw over a 1e-16 norm deviation. */
function normalize(q: Quat): Quat {
  const norm = Math.hypot(...q);
  if (norm === 0) return [0, 0, 0, 1];
  return [q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm];
}

/**
 * Absolute WebSocket URL for a backend path.
 *
 * An empty `apiBase` means "same origin as this page", which is how the
 * client talks to a proxied backend (see vite.config.ts). `new WebSocket()`
 * rejects a relative URL outright, so the origin has to be filled in --
 * and it has to come from `location`, because that is what carries https ->
 * wss. Building `ws://` from an https page is blocked by the browser exactly
 * like mixed content is.
 */
export function websocketUrl(apiBase: string, path: string): string {
  const base = apiBase || (typeof location === "undefined" ? "http://127.0.0.1" : location.origin);
  return `${base.replace(/^http/, "ws")}${path}`;
}

export class LiveRetargetSession {
  private socket: WebSocket | undefined;
  state: LiveRetargetSessionState = "closed";

  private constructor(
    private readonly apiBase: string,
    readonly sessionId: string,
  ) {}

  static async start(apiBase: string, robotId = "so101"): Promise<LiveRetargetSession> {
    const response = await fetch(`${apiBase}/spatial/live?robot_id=${robotId}`, {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error(`POST /spatial/live -> ${response.status}`);
    }
    const { session_id: sessionId } = (await response.json()) as { session_id: string };
    return new LiveRetargetSession(apiBase, sessionId);
  }

  connect(onShadowState: (state: RobotShadowState) => void, onClose?: () => void): void {
    this.state = "connecting";
    this.socket = new WebSocket(websocketUrl(this.apiBase, `/spatial/live/${this.sessionId}`));
    this.socket.onopen = () => { this.state = "open"; };
    this.socket.onclose = () => { this.state = "closed"; onClose?.(); };
    this.socket.onerror = () => { this.state = "closed"; };
    this.socket.onmessage = (event) => {
      try {
        onShadowState(JSON.parse(String(event.data)) as RobotShadowState);
      } catch (error) {
        console.warn("dropped malformed RobotShadowState on live retarget stream:", error);
      }
    };
  }

  /** Sends one HandFrame. A no-op while not yet open. `roomToStruct` is the
   * openxr path's workspace calibration; see {@link toWireHandFrame}. */
  send(
    hand: HandFrame,
    timestampNs: number,
    sourceDevice?: HandSourceDeviceWire,
    roomToStruct?: Alignment,
  ): void {
    if (this.state !== "open" || !this.socket) return;
    this.socket.send(
      JSON.stringify(toWireHandFrame(hand, timestampNs, sourceDevice, roomToStruct)),
    );
  }

  stop(): void {
    this.socket?.close();
    this.socket = undefined;
    this.state = "closed";
  }
}
