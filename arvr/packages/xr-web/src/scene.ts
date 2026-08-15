/**
 * Builds the three.js scene from a SceneManifest (STRUCT_2.md 34, 41).
 *
 * USD stays the simulation's authoritative representation; this client only
 * ever loads the GLB visualisation half. The fixture pack stands in until F3
 * hands over real room assets (STRUCT_2.md 84).
 *
 * Everything rendered lives in WebXR's Y-up space, so STRUCT poses are
 * converted on the way in. The conversion lives in adapter.ts and nowhere else.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { structToWebxr } from "./adapter";
import type { SceneManifest, Vec3 } from "./contracts";
import { parseSceneManifest } from "./contracts";

export const FIXTURES_BASE = "/ar-xr/";

/**
 * Where each fixture asset sits in struct_world, in meters.
 *
 * The table spans x in [-0.2, 1.0] and y in [-0.4, 0.4], so the bin has to
 * clear that footprint and the robot has to stand off the near edge. These
 * agree with MockTwinSource's object positions on the Python side; SceneManifest
 * carries no placement field (STRUCT_2.md 34), so until it does, the two live
 * side by side and have to be changed together.
 */
export const LAYOUT: Record<string, Vec3> = {
  table: [0.4, 0.0, 0.0],
  cube: [0.3, 0.1, 0.78],
  bin: [0.6, -0.7, 0.0],
  robot: [0.15, -0.7, 0.0], // matches ROBOT_BASE in ar-sim/scene_mjcf.py
};

const PALETTE: Record<string, number> = {
  table: 0x8b7355,
  cube: 0x4a9eda,
  bin: 0x6b6b6b,
  robot: 0xd4d4d4,
};

export interface LoadedScene {
  manifest: SceneManifest;
  root: THREE.Group;
  objects: Map<string, THREE.Object3D>;
}

/**
 * The basis change as a rotation, for geometry rather than points.
 *
 * The fixture GLBs are authored in struct_world (Z-up) because that is the
 * convention the contracts mandate. three.js renders Y-up, so the meshes
 * themselves have to be rotated, not just positioned -- otherwise the robot
 * renders lying on its side and every collision judgement a human makes from
 * the view is wrong.
 *
 * Columns are the images of struct +X, +Y, +Z, and they agree exactly with
 * structToWebxr.position by construction.
 */
export const STRUCT_TO_WEBXR_BASIS = new THREE.Matrix4().makeBasis(
  new THREE.Vector3(0, 0, -1), // struct +X (forward) -> webxr -Z
  new THREE.Vector3(-1, 0, 0), // struct +Y (left)    -> webxr -X
  new THREE.Vector3(0, 1, 0), // struct +Z (up)      -> webxr +Y
);

/** Orient an object authored in struct_world for display in the Y-up scene. */
export function orientToStruct(object: THREE.Object3D): void {
  object.quaternion.setFromRotationMatrix(STRUCT_TO_WEBXR_BASIS);
}

export function placeAtStruct(object: THREE.Object3D, position: Vec3): void {
  const [x, y, z] = structToWebxr.position(position);
  object.position.set(x, y, z);
}

export async function loadScene(base = FIXTURES_BASE): Promise<LoadedScene> {
  const manifest = parseSceneManifest(await fetch(`${base}scene.json`).then((r) => r.text()));

  const loader = new GLTFLoader();
  const root = new THREE.Group();
  const objects = new Map<string, THREE.Object3D>();

  for (const asset of manifest.visual_assets) {
    const gltf = await loader.loadAsync(`${base}${asset.glb}`);
    const object = gltf.scene;

    const color = PALETTE[asset.id] ?? 0x9aa0a6;
    object.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        (child as THREE.Mesh).material = new THREE.MeshStandardMaterial({
          color,
          roughness: 0.75,
          metalness: 0.05,
        });
        child.castShadow = true;
        child.receiveShadow = true;
      }
    });

    orientToStruct(object);
    placeAtStruct(object, LAYOUT[asset.id] ?? [0, 0, 0]);
    object.name = asset.id;
    root.add(object);
    objects.set(asset.id, object);
  }

  return { manifest, root, objects };
}

/** Floor grid and lighting. Deliberately plain -- the room is the real thing. */
export function buildEnvironment(scene: THREE.Scene): void {
  scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x2a2a2a, 1.6));

  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.position.set(2.5, 4, 2.5);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  scene.add(key);

  const grid = new THREE.GridHelper(8, 32, 0x3b4252, 0x2a2f3a);
  scene.add(grid);
}

/**
 * A dashed line from the human to where the robot should stand. FOLLOW's
 * entire visible output (STRUCT_2.md 23).
 */
export function makeTargetLine(color = 0x00d4aa): THREE.Line {
  const geometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(),
    new THREE.Vector3(),
  ]);
  return new THREE.Line(geometry, new THREE.LineDashedMaterial({ color, dashSize: 0.08, gapSize: 0.05 }));
}

export function updateLine(line: THREE.Line, from: Vec3, to: Vec3): void {
  const a = structToWebxr.position(from);
  const b = structToWebxr.position(to);
  line.geometry.setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]);
  line.computeLineDistances();
}

/**
 * The trail a TEACH demonstration leaves behind (STRUCT_2.md 18).
 *
 * The buffer is allocated once at full size and revealed with setDrawRange.
 * Calling setFromPoints on a growing array instead makes three.js reuse a
 * buffer that is too small -- it warns every frame and silently drops the tail,
 * which at 60 Hz means a recording whose trail does not match its frames.
 */
export class Trail {
  readonly line: THREE.Line;
  private readonly positions: Float32Array;
  private count = 0;

  constructor(color = 0xffb347, private readonly maxPoints = 4000) {
    this.positions = new Float32Array(maxPoints * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(this.positions, 3));
    geometry.setDrawRange(0, 0);
    this.line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color }));
    this.line.frustumCulled = false;
  }

  push(position: Vec3): void {
    if (this.count >= this.maxPoints) return;
    const [x, y, z] = structToWebxr.position(position);
    this.positions.set([x, y, z], this.count * 3);
    this.count += 1;

    const attribute = this.line.geometry.getAttribute("position") as THREE.BufferAttribute;
    attribute.needsUpdate = true;
    this.line.geometry.setDrawRange(0, this.count);
  }

  clear(): void {
    this.count = 0;
    this.line.geometry.setDrawRange(0, 0);
  }
}
