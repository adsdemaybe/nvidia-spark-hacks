import { describe, expect, it } from "vitest";
import { DesktopMockAdapter } from "./adapter";
import { EpisodeRecorder } from "./recorder";

const adapter = new DesktopMockAdapter();

function record(recorder: EpisodeRecorder, n: number, startNs = 1_000): void {
  for (let i = 0; i < n; i++) {
    recorder.capture(
      adapter.toSpatialFrame(
        { position: [i * 0.01, 0, 0], orientation: [0, 0, 0, 1], trigger: 0 },
        startNs + i * 33_333_333,
      ),
    );
  }
}

describe("EpisodeRecorder", () => {
  it("captures nothing until it is started", () => {
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });

    record(recorder, 5);

    expect(recorder.frameCount).toBe(0);
  });

  it("captures frames once started", () => {
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });
    recorder.start(0);

    record(recorder, 5);

    expect(recorder.frameCount).toBe(5);
  });

  it("records grab and release as events on the same clock as the frames", () => {
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });
    recorder.start(0);
    record(recorder, 3, 0);
    recorder.grab(100_000_000);
    record(recorder, 3, 200_000_000);
    recorder.release(300_000_000);

    const episode = recorder.finish(400_000_000);

    expect(episode.events.map((e) => e.type)).toEqual([
      "START",
      "GRAB",
      "RELEASE",
      "FINISH",
    ]);
    expect(episode.events.map((e) => e.timestamp_ns)).toEqual([
      0, 100_000_000, 300_000_000, 400_000_000,
    ]);
  });

  it("rejects a frame that arrives out of order", () => {
    // A recorder that silently accepts these produces training data whose
    // trajectory jumps backwards (ar-xr-plan.md 61, 62).
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });
    recorder.start(0);
    record(recorder, 2, 5_000);

    expect(() =>
      recorder.capture(
        adapter.toSpatialFrame(
          { position: [0, 0, 0], orientation: [0, 0, 0, 1], trigger: 0 },
          1_000,
        ),
      ),
    ).toThrow(/monotonic/);
  });

  it("keeps the frames locally so a demo survives losing the network", () => {
    // ar-xr-plan.md 19: "If Wi-Fi disappears: demo must survive."
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });
    recorder.start(0);
    record(recorder, 10);

    const episode = recorder.finish(9_999_999_999);
    const frames = recorder.frames();

    expect(frames).toHaveLength(10);
    expect(episode.frames_artifact).toMatch(/\.parquet$/);
    expect(frames.every((f) => f.frame === "struct_world")).toBe(true);
  });

  it("reports the duration a human would recognise", () => {
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });
    recorder.start(0);
    record(recorder, 31, 0);

    expect(recorder.durationSeconds).toBeCloseTo(1.0, 2);
  });

  it("cancelling discards the episode rather than half-saving it", () => {
    const recorder = new EpisodeRecorder({ taskId: "cube_to_bin" });
    recorder.start(0);
    record(recorder, 5);

    recorder.cancel(500);

    expect(recorder.frameCount).toBe(0);
    expect(recorder.isRecording).toBe(false);
  });
});
