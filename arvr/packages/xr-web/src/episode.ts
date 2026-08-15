/**
 * Loading a recorded demonstration for REPLAY (ar-xr-plan.md 20, 43).
 *
 * The pipeline's artifact format is Parquet (ar-xr-plan.md 35); the fixture pack
 * publishes the identical frames as JSONL because a browser cannot read Parquet
 * without a large decoder. A Python test asserts the two encodings agree, so
 * what REPLAY shows here is the same recording the backend retargets.
 */

import type { SpatialEpisode, SpatialFrame, Vec3 } from "./contracts";
import { requireKnownSchema } from "./contracts";

export interface LoadedEpisode {
  meta: SpatialEpisode;
  frames: SpatialFrame[];
  durationSeconds: number;
}

export async function loadEpisode(base: string): Promise<LoadedEpisode> {
  const [metaText, framesText] = await Promise.all([
    fetch(`${base}sample_episode.json`).then((r) => r.text()),
    fetch(`${base}sample_episode.jsonl`).then((r) => r.text()),
  ]);

  const meta = JSON.parse(metaText) as SpatialEpisode;
  requireKnownSchema(meta, "SpatialEpisode");

  const frames = framesText
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => {
      const frame = JSON.parse(line) as SpatialFrame;
      requireKnownSchema(frame, "SpatialFrame");
      return frame;
    });

  if (frames.length === 0) throw new Error("episode has no frames");

  const first = frames[0]!;
  const last = frames[frames.length - 1]!;
  return {
    meta,
    frames,
    durationSeconds: (last.timestamp_ns - first.timestamp_ns) / 1e9,
  };
}

/**
 * The pose at `seconds` into the recording, by nearest frame.
 *
 * Deliberately not interpolated: REPLAY is showing what was recorded, and
 * inventing intermediate poses would misrepresent the demonstration a human
 * is being asked to verify.
 */
export function poseAt(episode: LoadedEpisode, seconds: number): SpatialFrame {
  const frames = episode.frames;
  const start = frames[0]!.timestamp_ns;
  const wanted = start + seconds * 1e9;

  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid]!.timestamp_ns < wanted) lo = mid + 1;
    else hi = mid;
  }
  return frames[lo]!;
}

/** The demonstrated path, for drawing the human's trail all at once. */
export function path(episode: LoadedEpisode): Vec3[] {
  return episode.frames.map((f) => f.position_m);
}

/** Timestamps of the GRAB/RELEASE moments, in seconds from the start. */
export function gripperEvents(episode: LoadedEpisode): Array<{ type: string; at: number }> {
  const start = episode.frames[0]!.timestamp_ns;
  return episode.meta.events
    .filter((e) => e.type === "GRAB" || e.type === "RELEASE")
    .map((e) => ({ type: e.type, at: (e.timestamp_ns - start) / 1e9 }));
}
