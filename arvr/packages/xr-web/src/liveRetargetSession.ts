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

/** Defaults to "openxr" only to keep pre-existing call sites (mock's replay
 * loop, before this default existed) working unchanged -- every real caller
 * should now pass its actual provider so an episode's recorded provenance
 * isn't silently mislabeled (this default previously mislabeled mock frames
 * as "openxr" too; see STATE.md). */
export function toWireHandFrame(
  hand: HandFrame,
  timestampNs: number,
  sourceDevice: HandSourceDeviceWire = "openxr",
): object {
  const joints: Record<string, unknown> = {};
  for (const [name, joint] of Object.entries(hand.joints)) {
    joints[name] = {
      position_m: webxrToStruct.position(joint.position),
      orientation_xyzw: webxrToStruct.quaternion(joint.orientation),
    };
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
    const wsBase = this.apiBase.replace(/^http/, "ws");
    this.state = "connecting";
    this.socket = new WebSocket(`${wsBase}/spatial/live/${this.sessionId}`);
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

  /** Sends one HandFrame. A no-op while not yet open. */
  send(hand: HandFrame, timestampNs: number, sourceDevice?: HandSourceDeviceWire): void {
    if (this.state !== "open" || !this.socket) return;
    this.socket.send(JSON.stringify(toWireHandFrame(hand, timestampNs, sourceDevice)));
  }

  stop(): void {
    this.socket?.close();
    this.socket = undefined;
    this.state = "closed";
  }
}
