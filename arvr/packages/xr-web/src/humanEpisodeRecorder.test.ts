import { describe, expect, it } from "vitest";
import type { HandFrame } from "./hands";
import { HumanEpisodeRecorder } from "./humanEpisodeRecorder";

function hand(x: number): HandFrame {
  return {
    handedness: "right",
    joints: { wrist: { position: [x, 0, 0], orientation: [0, 0, 0, 1], radius: null } },
    pinchApertureM: null,
    gripper: 0,
  };
}

describe("HumanEpisodeRecorder", () => {
  it("captures nothing until it is started", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.captureHand(hand(0), 0);
    expect(recorder.frameCount).toBe(0);
  });

  it("captures hand frames once started, converted to struct_world", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.start(0);
    recorder.captureHand(hand(0), 0);
    recorder.captureHand(hand(1), 33_000_000);

    expect(recorder.frameCount).toBe(2);
    const frames = recorder.handFrames() as Array<{ hand: string; frame: string }>;
    expect(frames.every((f) => f.frame === "struct_world")).toBe(true);
    expect(frames.every((f) => f.hand === "right")).toBe(true);
  });

  it("rejects a hand frame that arrives out of order", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.start(0);
    recorder.captureHand(hand(0), 5_000);

    expect(() => recorder.captureHand(hand(0), 1_000)).toThrow(/monotonic/);
  });

  it("keeps frames locally so a demo survives losing the network", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.start(0);
    for (let i = 0; i < 10; i++) recorder.captureHand(hand(i), i * 33_000_000);

    const metadata = recorder.finish();
    expect(recorder.handFrames()).toHaveLength(10);
    expect(metadata.coordinate_frame).toBe("struct_world");
    expect(metadata.status).toBe("recorded");
  });

  it("cancelling discards the episode rather than half-saving it", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.start(0);
    recorder.captureHand(hand(0), 0);
    recorder.captureHand(hand(1), 1000);

    recorder.cancel();

    expect(recorder.frameCount).toBe(0);
    expect(recorder.isRecording).toBe(false);
  });

  it("captures object states and events alongside hand frames", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.start(0);
    recorder.captureObject({ id: "button_01", position_m: [0.4, 0, 0.53] });
    recorder.markEvent("contact", 500_000_000);

    expect(recorder.objectStates()).toHaveLength(1);
    expect(recorder.events().map((e) => e.type)).toEqual(["contact"]);
  });

  it("returns a copy of the buffers, not a live reference", () => {
    const recorder = new HumanEpisodeRecorder({
      taskId: "press_button", assetId: "button_01", handProvider: "mock",
    });
    recorder.start(0);
    recorder.captureHand(hand(0), 0);
    const first = recorder.handFrames();
    recorder.captureHand(hand(1), 1000);
    expect(first).toHaveLength(1); // unaffected by the second capture
  });
});
