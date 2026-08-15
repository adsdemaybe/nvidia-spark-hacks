/**
 * TypeScript mirror of the frozen STRUCT contracts (STRUCT_2.md 29-34).
 *
 * These must stay identical to ar_contracts (Python)'s Pydantic models. They are duplicated
 * rather than generated because the two runtimes ship separately; the guard
 * against drift is that the tests here validate the same fixture bytes the
 * Python tests do, and spatial.test.ts checks the two produce the same numbers.
 *
 * Canonical convention: right-handed, Z-up, meters, quaternion [x,y,z,w],
 * timestamps in nanoseconds.
 */

export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];

export const SCHEMA_VERSION = "1.0";
export const IDENTITY_QUATERNION: Quat = [0, 0, 0, 1];
export const DEFAULT_FOLLOW_DISTANCE_M = 1.5;

export interface Pose {
  position_m: Vec3;
  orientation_xyzw?: Quat;
}

export type DeviceType = "phone" | "xr_controller" | "hand_tracking" | "desktop_mock";
export type InputType = "tracked_controller" | "hand" | "mock";
export type CoordinateFrame =
  | "device_frame"
  | "ar_world"
  | "struct_world"
  | "robot_base"
  | "end_effector";

export interface SpatialFrame {
  schema_version: string;
  timestamp_ns: number;
  source: { device_type: DeviceType; input_type?: InputType };
  frame: CoordinateFrame;
  position_m: Vec3;
  orientation_xyzw: Quat;
  gripper: number;
}

export type EventType = "GRAB" | "RELEASE" | "START" | "FINISH" | "CANCEL";

export interface EpisodeEvent {
  type: EventType;
  timestamp_ns: number;
}

export interface SpatialEpisode {
  schema_version: string;
  episode_id: string;
  task_id: string;
  source: { device_type: DeviceType; input_type?: InputType };
  coordinate_frame: CoordinateFrame;
  frames_artifact: string;
  events: EpisodeEvent[];
}

export type TaskStatus =
  | "idle"
  | "running"
  | "approaching"
  | "grasping"
  | "moving"
  | "placing"
  | "success"
  | "failed";

export interface TwinState {
  schema_version: string;
  timestamp_ns: number;
  scene_id: string;
  robot: { id: string; joint_positions: number[] };
  objects: Array<{ id: string; position_m: Vec3; orientation_xyzw?: Quat }>;
  task?: { id: string; status: TaskStatus };
}

export interface FollowState {
  schema_version: string;
  timestamp_ns: number;
  human_pose: Pose;
  desired_follow_distance_m: number;
  follow_target: Pose;
}

export interface CorrectionEvent {
  schema_version: string;
  task_id: string;
  timestamp_ns: number;
  original_target: Pose;
  corrected_target: Pose;
  reason?: string;
}

export interface VisualAsset {
  id: string;
  glb: string;
}

export interface SceneManifest {
  schema_version: string;
  scene_id: string;
  canonical_usd?: string | null;
  visual_assets: VisualAsset[];
}

/**
 * Reject a payload whose schema_version we do not implement (STRUCT_2.md 60).
 *
 * Silently rendering a newer contract is worse than failing: the client would
 * show a plausible but wrong world and nobody would know which fields it
 * dropped.
 */
export function requireKnownSchema(payload: { schema_version?: string }, what: string): void {
  if (payload.schema_version !== undefined && payload.schema_version !== SCHEMA_VERSION) {
    throw new Error(
      `${what}: unsupported schema_version ${payload.schema_version}; ` +
        `this client implements ${SCHEMA_VERSION}`,
    );
  }
}

export function parseTwinState(raw: string): TwinState {
  const state = JSON.parse(raw) as TwinState;
  requireKnownSchema(state, "TwinState");
  if (!state.robot || !Array.isArray(state.robot.joint_positions)) {
    throw new Error("TwinState: missing robot.joint_positions");
  }
  if (state.robot.joint_positions.some((j) => !Number.isFinite(j))) {
    throw new Error("TwinState: joint_positions must be finite");
  }
  return state;
}

export function parseSceneManifest(raw: string): SceneManifest {
  const manifest = JSON.parse(raw) as SceneManifest;
  requireKnownSchema(manifest, "SceneManifest");
  if (!Array.isArray(manifest.visual_assets)) {
    throw new Error("SceneManifest: missing visual_assets");
  }
  return manifest;
}
