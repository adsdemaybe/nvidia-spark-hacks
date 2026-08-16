/**
 * Uploads a finished spatial-teach recording through ar_backend's Spatial
 * Episodes API (spec section 46) and reports the verdict -- mirrors
 * episodeUpload.ts's create -> artifact -> finish sequence exactly, against
 * /spatial/episodes instead of /xr/episodes.
 */

import type { HumanEpisodeEvent, HumanEpisodeMetadata, ObjectStateCapture } from "./humanEpisodeRecorder";

export interface SpatialEpisodeVerdict {
  episode_id: string;
  status: string;
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
    // A 404 on a route that exists in this repo almost always means the
    // running backend is older than the client -- uvicorn without --reload
    // keeps serving whatever it imported at startup, so a route added since
    // then is simply absent and FastAPI answers `{"detail":"Not Found"}`.
    // Said plainly here because the symptom ("export 404s") otherwise sends
    // people looking for a bug in code that is correct on disk.
    if (response.status === 404) {
      throw new Error(
        `${url} -> 404. This route exists in the repo, so the backend serving ` +
          `:8000 is probably a stale process started before it was added. ` +
          `Restart it: uv run --no-sync uvicorn ar_backend.app:app --port 8000 --reload`,
      );
    }
    throw new Error(`${url} -> ${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

/** What the human-layer export returns. No verdict and no task_success:
 * nothing was retargeted or verified, so there is nothing to pass or fail.
 *
 * Field names mirror the server's `HumanExportResponse` exactly. They did not
 * always: this said `frames` where the server sends `n_rows`, so the recorded
 * frame count rendered as `undefined` on the HUD -- the one number that tells
 * a demonstrator the take actually contained something. TypeScript cannot
 * catch that, because the response is parsed from JSON and cast. */
export interface HumanExportResult {
  episode_id: string;
  /** The LeRobot parquet dataset. Null when pyarrow is unavailable -- the
   * recording still exported to the shareable database below. */
  dataset_id: string | null;
  n_rows: number | null;
  parquet_error: string | null;
  /** The committable SQLite copy, always written. */
  database_path: string;
  database_episodes: number;
  database_frames: number;
}

/**
 * Record a demonstration and export it as training data, with no robot in
 * the loop at all.
 *
 * `uploadHumanEpisode` below finishes through retarget -> verify -> export,
 * which needs a robot model to retarget onto and Pinocchio to do it with.
 * Neither exists for a robot hand that is still being generated, and
 * Pinocchio is Linux-only regardless -- so on this path `/finish` can only
 * ever 503.
 *
 * This is the other layer of the same architecture rather than a workaround.
 * A HumanEpisode is robot-independent by design (never destroy raw human
 * data), and a policy can be trained on human hand trajectories now, while
 * the same stored episodes stay available to compile onto the generated hand
 * the moment it exists. Two datasets from one recording, which is the whole
 * point of keeping the layers apart.
 */
export async function exportHumanEpisodeForTraining(
  apiBase: string,
  meta: HumanEpisodeMetadata,
  handFrames: object[],
  objectStates: ObjectStateCapture[],
  events: HumanEpisodeEvent[],
): Promise<HumanExportResult> {
  const created = await postJson<{ episode_id: string; status: string }>(
    `${apiBase}/spatial/episodes`,
    { task_id: meta.task_id, asset_id: meta.asset_id, hand_provider: meta.hand_provider },
  );

  await postJson(`${apiBase}/spatial/episodes/${created.episode_id}/artifact`, {
    hand_frames: handFrames,
    object_states: objectStates,
    events,
  });

  return postJson<HumanExportResult>(
    `${apiBase}/spatial/episodes/${created.episode_id}/export-human`,
    {},
  );
}

export async function uploadHumanEpisode(
  apiBase: string,
  meta: HumanEpisodeMetadata,
  handFrames: object[],
  objectStates: ObjectStateCapture[],
  events: HumanEpisodeEvent[],
  assetWorldPose: { position_m: [number, number, number]; orientation_xyzw: [number, number, number, number] },
  goalPositionM: [number, number, number],
  goalToleranceM = 0.05,
): Promise<SpatialEpisodeVerdict> {
  const created = await postJson<{ episode_id: string; status: string }>(
    `${apiBase}/spatial/episodes`,
    { task_id: meta.task_id, asset_id: meta.asset_id, hand_provider: meta.hand_provider },
  );

  await postJson(`${apiBase}/spatial/episodes/${created.episode_id}/artifact`, {
    hand_frames: handFrames,
    object_states: objectStates,
    events,
  });

  return postJson<SpatialEpisodeVerdict>(
    `${apiBase}/spatial/episodes/${created.episode_id}/finish`,
    {
      asset_world_pose: assetWorldPose,
      goal_position_m: goalPositionM,
      goal_tolerance_m: goalToleranceM,
    },
  );
}
