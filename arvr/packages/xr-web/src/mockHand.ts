/**
 * MockHandProvider (TS side) — Shadow Robot Spatial Demonstration Pipeline
 * spec section 52 Phase 4. Replays fixtures/spatial-training/hand/
 * mock_episode.jsonl so the shadow hand / retargeter / recording pipeline
 * is drivable without a headset, mirroring twinProvider.ts's
 * MockTwinStateProvider shape (load/start/stop).
 *
 * The fixture stores struct_world-frame joints (ar_contracts.HandFrame,
 * the canonical Python contract — what a *recorded* episode looks like).
 * hands.ts's HandFrame is the raw WebXR-space shape ShadowHand and the rest
 * of the live pipeline consume directly (see hands.ts's own docstring: no
 * conversion happens there because it IS the device frame). This class is
 * the mock's device-frame boundary: it converts struct_world -> WebXR space
 * via adapter.ts's `structToWebxr`, the same conversion HumanEpisodeRecorder
 * runs in reverse when it stores real WebXR hand data. Get this the wrong
 * way round and the mock hand renders correctly but points backwards.
 */

import { structToWebxr } from "./adapter";
import type { HandFrame, JointPose } from "./hands";
import { distance, gripperFromAperture } from "./hands";

export interface StructJoint {
  position_m: [number, number, number];
  orientation_xyzw: [number, number, number, number];
}

export interface StructHandFrame {
  timestamp_ns: number;
  hand: "left" | "right";
  joints: Record<string, StructJoint>;
}

export function toWebxrJoint(joint: StructJoint): JointPose {
  return {
    position: structToWebxr.position(joint.position_m),
    orientation: structToWebxr.quaternion(joint.orientation_xyzw),
    radius: null,
  };
}

export function toHandFrame(struct: StructHandFrame): HandFrame {
  const joints: Record<string, JointPose> = {};
  for (const [name, joint] of Object.entries(struct.joints)) {
    joints[name] = toWebxrJoint(joint);
  }
  const thumb = joints["thumb-tip"];
  const index = joints["index-finger-tip"];
  const pinchApertureM = thumb && index ? distance(thumb, index) : null;
  return {
    handedness: struct.hand,
    joints,
    pinchApertureM,
    gripper: gripperFromAperture(pinchApertureM),
  };
}

export class MockHandProvider {
  private timer: ReturnType<typeof setInterval> | undefined;
  private index = 0;

  constructor(
    private readonly frames: HandFrame[],
    private readonly hz = 30,
  ) {}

  static async load(url: string, hz = 30): Promise<MockHandProvider> {
    const text = await fetch(url).then((r) => r.text());
    const frames = text
      .split("\n")
      .filter((line) => line.trim())
      .map((line) => toHandFrame(JSON.parse(line) as StructHandFrame));
    return new MockHandProvider(frames, hz);
  }

  start(onFrame: (hand: HandFrame) => void): void {
    this.timer = setInterval(() => {
      const frame = this.frames[this.index % this.frames.length];
      this.index += 1;
      if (frame) onFrame(frame);
    }, 1000 / this.hz);
  }

  stop(): void {
    if (this.timer !== undefined) clearInterval(this.timer);
    this.timer = undefined;
  }
}
