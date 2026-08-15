/**
 * HumanEpisode recorder — Shadow Robot Spatial Demonstration Pipeline spec
 * section 21, Phase 8. Same buffer-locally-until-FINISH discipline as
 * recorder.ts (what makes "network loss does not destroy episode" true):
 * frames are only handed over as a finished artifact, never streamed live.
 *
 * Converts each captured HandFrame from hands.ts's WebXR-space shape to the
 * wire's struct_world convention via liveRetargetSession.ts's
 * `toWireHandFrame` (the same conversion, reused rather than duplicated) --
 * this is the one place raw device data and the canonical ar_contracts
 * shape meet. ShadowHand deliberately does NOT do this conversion (see its
 * own docstring); this class is where it belongs.
 */

import type { Alignment } from "./alignment";
import type { HandFrame } from "./hands";
import { toWireHandFrame } from "./liveRetargetSession";

export type HandProviderKind = "openxr" | "phone" | "mock" | "webcam";

/** Mirrors ar_contracts.HumanEventType. The last six are the ball-sorting
 * vocabulary added in Round 10 -- an additive widening, so every episode
 * recorded before it still validates. See the Python contract for why they
 * could not be folded into the original five. */
export type HumanEpisodeEventType =
  | "pinch"
  | "release"
  | "contact"
  | "task_start"
  | "task_finish"
  | "grasp_start"
  | "grasp_end"
  | "ball_enter_basket"
  | "wrong_basket"
  | "sort_complete"
  | "tracking_lost";

export interface HumanEpisodeRecorderOptions {
  taskId: string;
  assetId: string;
  handProvider: HandProviderKind;
  episodeId?: string;
}

export interface HumanEpisodeEvent {
  type: HumanEpisodeEventType;
  timestamp_ns: number;
  /** Which object the event concerns. Absent for hand-only events. */
  object_id?: string;
  /** Which container it went into. Absent unless the event is about one. */
  container_id?: string;
}

export interface ObjectStateCapture {
  id: string;
  position_m: [number, number, number];
  orientation_xyzw?: [number, number, number, number];
  /** Set when object poses are recorded as a time series (the sort task),
   * absent for a single end-of-episode snapshot (the button task). */
  timestamp_ns?: number;
}

export interface HumanEpisodeMetadata {
  schema_version: string;
  episode_id: string;
  task_id: string;
  asset_id: string;
  coordinate_frame: "struct_world";
  hand_provider: HandProviderKind;
  status: "recorded";
}

export class HumanEpisodeRecorder {
  private readonly options: HumanEpisodeRecorderOptions;
  private handFrameBuffer: object[] = [];
  private objectStateBuffer: ObjectStateCapture[] = [];
  private eventBuffer: HumanEpisodeEvent[] = [];
  private recording = false;
  private lastTimestampNs: number | undefined;

  constructor(options: HumanEpisodeRecorderOptions) {
    this.options = options;
  }

  get isRecording(): boolean {
    return this.recording;
  }

  get frameCount(): number {
    return this.handFrameBuffer.length;
  }

  /** Wire-shaped (struct_world) hand frames, ready to upload as-is. A copy,
   * so a caller cannot mutate the buffer out from under the recorder. */
  handFrames(): object[] {
    return [...this.handFrameBuffer];
  }

  objectStates(): ObjectStateCapture[] {
    return [...this.objectStateBuffer];
  }

  events(): HumanEpisodeEvent[] {
    return [...this.eventBuffer];
  }

  start(_timestampNs: number): void {
    this.handFrameBuffer = [];
    this.objectStateBuffer = [];
    this.eventBuffer = [];
    this.lastTimestampNs = undefined;
    this.recording = true;
  }

  /** Converts WebXR-space -> struct_world at capture time (see module docstring).
   *
   * `roomToStruct` is the openxr path's workspace calibration: a headset's
   * tracking origin is wherever it booted, so without it a recorded episode
   * would be full of coordinates relative to a point that means nothing to
   * the robot. Every other provider authors struct_world directly and passes
   * nothing. */
  captureHand(frame: HandFrame, timestampNs: number, roomToStruct?: Alignment): void {
    if (!this.recording) return;
    if (this.lastTimestampNs !== undefined && timestampNs < this.lastTimestampNs) {
      throw new Error(
        `hand frames must be monotonic in timestamp_ns; ` +
          `got ${timestampNs} after ${this.lastTimestampNs}`,
      );
    }
    this.lastTimestampNs = timestampNs;
    this.handFrameBuffer.push(
      toWireHandFrame(frame, timestampNs, this.options.handProvider, roomToStruct),
    );
  }

  captureObject(state: ObjectStateCapture): void {
    if (!this.recording) return;
    this.objectStateBuffer.push(state);
  }

  markEvent(
    type: HumanEpisodeEventType,
    timestampNs: number,
    about?: { objectId?: string; containerId?: string },
  ): void {
    if (!this.recording) return;
    const event: HumanEpisodeEvent = { type, timestamp_ns: timestampNs };
    if (about?.objectId) event.object_id = about.objectId;
    if (about?.containerId) event.container_id = about.containerId;
    this.eventBuffer.push(event);
  }

  /** Abandon the take. Nothing half-saved -- a partial demo is not data. */
  cancel(): void {
    this.handFrameBuffer = [];
    this.objectStateBuffer = [];
    this.eventBuffer = [];
    this.lastTimestampNs = undefined;
    this.recording = false;
  }

  finish(): HumanEpisodeMetadata {
    this.recording = false;
    const episodeId = this.options.episodeId ?? crypto.randomUUID();
    return {
      schema_version: "1.0",
      episode_id: episodeId,
      task_id: this.options.taskId,
      asset_id: this.options.assetId,
      coordinate_frame: "struct_world",
      hand_provider: this.options.handProvider,
      status: "recorded",
    };
  }
}
