/**
 * Uploads a finished TEACH recording through ar_backend's Episodes API
 * (STRUCT_2.md 36, section 42's TEACH pipeline) and reports the verdict.
 *
 * The recorder (recorder.ts) buffers locally and hands over a complete
 * SpatialEpisode + frame list only once FINISH is pressed — this module is
 * what turns that into the create -> artifact -> finish call sequence and
 * surfaces the same PASS/FAIL checklist the spec's demo script describes
 * (section 75), not just a console.info.
 */

import type { EpisodeEvent, SpatialEpisode, SpatialFrame, Vec3 } from "./contracts";

export interface EpisodeVerdict {
  episode_id: string;
  status: string;
  tracking_error_m: number | null;
  task_success: boolean | null;
  dataset_id: string | null;
  rejection_reason: string | null;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${url} -> ${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

/**
 * Runs the full upload: create the episode server-side, hand over the
 * buffered frames/events as one artifact, then finish — which is where
 * ar_datapipe actually retargets/verifies/exports (synchronous, so the
 * verdict comes back in this same call, no polling needed at demo scale).
 */
export async function uploadEpisode(
  apiBase: string,
  meta: SpatialEpisode,
  frames: SpatialFrame[],
  events: EpisodeEvent[],
  goalPositionM: Vec3,
): Promise<EpisodeVerdict> {
  const created = await postJson<{ episode_id: string; status: string }>(
    `${apiBase}/xr/episodes`,
    { task_id: meta.task_id, source: meta.source, coordinate_frame: meta.coordinate_frame },
  );

  await postJson(`${apiBase}/xr/episodes/${created.episode_id}/artifact`, { frames, events });

  return postJson<EpisodeVerdict>(`${apiBase}/xr/episodes/${created.episode_id}/finish`, {
    goal_position_m: goalPositionM,
  });
}
