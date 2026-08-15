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
    throw new Error(`${url} -> ${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

/** Grasp-and-place / pull task predicates (Track C) -- see
 * ar_contracts.simulation_provider.TaskSpec's own docstring for exactly
 * what these check. Omitted fields keep the original single-goal "reach"
 * predicate (button press) exercised unchanged. */
export interface TaskPredicateExtra {
  objectPositionM?: [number, number, number];
  objectCaptureRadiusM?: number;
  pullAxis?: [number, number, number];
  pullDistanceM?: number;
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
  taskPredicateExtra: TaskPredicateExtra = {},
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
      object_position_m: taskPredicateExtra.objectPositionM ?? null,
      object_capture_radius_m: taskPredicateExtra.objectCaptureRadiusM ?? 0.05,
      pull_axis: taskPredicateExtra.pullAxis ?? null,
      pull_distance_m: taskPredicateExtra.pullDistanceM ?? null,
    },
  );
}
