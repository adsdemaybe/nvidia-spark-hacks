/**
 * TEACH-mode episode recorder (STRUCT_2.md 17-19).
 *
 * Frames are buffered locally and only handed over as a finished artifact. That
 * ordering is deliberate: "If Wi-Fi disappears: demo must survive." A recorder
 * that streamed every pose would lose the demonstration the moment the network
 * blinked, and a human cannot repeat a demonstration identically.
 */

import type {
  CoordinateFrame,
  DeviceType,
  EpisodeEvent,
  InputType,
  SpatialEpisode,
  SpatialFrame,
} from "./contracts";
import { SCHEMA_VERSION } from "./contracts";

export interface RecorderOptions {
  taskId: string;
  episodeId?: string;
  deviceType?: DeviceType;
  inputType?: InputType;
  coordinateFrame?: CoordinateFrame;
}

export class EpisodeRecorder {
  private readonly options: RecorderOptions;
  private buffer: SpatialFrame[] = [];
  private events: EpisodeEvent[] = [];
  private recording = false;

  constructor(options: RecorderOptions) {
    this.options = options;
  }

  get isRecording(): boolean {
    return this.recording;
  }

  get frameCount(): number {
    return this.buffer.length;
  }

  get durationSeconds(): number {
    const first = this.buffer[0];
    const last = this.buffer[this.buffer.length - 1];
    if (!first || !last) return 0;
    return (last.timestamp_ns - first.timestamp_ns) / 1e9;
  }

  /** A copy, so a caller cannot mutate the buffer out from under the recorder. */
  frames(): SpatialFrame[] {
    return [...this.buffer];
  }

  start(timestampNs: number): void {
    this.buffer = [];
    this.events = [{ type: "START", timestamp_ns: timestampNs }];
    this.recording = true;
  }

  capture(frame: SpatialFrame): void {
    if (!this.recording) return;

    const previous = this.buffer[this.buffer.length - 1];
    if (previous && frame.timestamp_ns < previous.timestamp_ns) {
      throw new Error(
        `episode frames must be monotonic in timestamp_ns; ` +
          `got ${frame.timestamp_ns} after ${previous.timestamp_ns}`,
      );
    }
    this.buffer.push(frame);
  }

  grab(timestampNs: number): void {
    this.mark("GRAB", timestampNs);
  }

  release(timestampNs: number): void {
    this.mark("RELEASE", timestampNs);
  }

  /** Abandon the take. Nothing half-saved -- a partial demo is not data. */
  cancel(timestampNs: number): void {
    this.mark("CANCEL", timestampNs);
    this.buffer = [];
    this.events = [];
    this.recording = false;
  }

  finish(timestampNs: number): SpatialEpisode {
    this.mark("FINISH", timestampNs);
    this.recording = false;

    const episodeId = this.options.episodeId ?? crypto.randomUUID();
    return {
      schema_version: SCHEMA_VERSION,
      episode_id: episodeId,
      task_id: this.options.taskId,
      source: {
        device_type: this.options.deviceType ?? "desktop_mock",
        input_type: this.options.inputType ?? "mock",
      },
      coordinate_frame: this.options.coordinateFrame ?? "struct_world",
      frames_artifact: `${episodeId}.parquet`,
      events: [...this.events],
    };
  }

  private mark(type: EpisodeEvent["type"], timestampNs: number): void {
    if (!this.recording) return;
    this.events.push({ type, timestamp_ns: timestampNs });
  }
}
