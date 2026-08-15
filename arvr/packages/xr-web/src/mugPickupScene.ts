/**
 * The mug-pickup scene, rendered: a table and a mug.
 *
 * Nothing else is in here. Every additional prop is one more thing a
 * demonstrator can knock over by accident and one more object a policy has to
 * learn to ignore, so the scene contains only the surface and the target.
 * There is no robot: the data is *for* one, but the recording is of a human
 * hand and an object.
 *
 * View layer only -- it reads `MugPickupTask` and draws it. Whether the mug
 * counts as picked up is decided there, in code a test can pin down without a
 * renderer.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { orientToStruct, placeAtStruct } from "./scene";
import {
  MUG_ASSET_URL,
  MUG_BODY_RADIUS_M,
  MUG_HEIGHT_M,
  TABLE_DROP_M,
  TABLE_THICKNESS_M,
  TABLE_X_M,
  TABLE_Y_M,
  TABLE_Z_M,
} from "./mugPickupLayout";
import type { Vec3 } from "./contracts";

/** What the mug turns when a hand is close enough to take it. Feedback before
 * the grasp, so a demonstrator learns the catch distance instead of guessing
 * and producing a take full of failed grabs. */
const REACHABLE_TINT = 0xffd479;

export interface MugVisualState {
  mugPosition(): Vec3;
  readonly isHeld: boolean;
}

export class MugPickupScene {
  readonly root = new THREE.Group();
  /** The placeholder first, then the loaded asset. Both are base-origin, so
   * `update` can place either without knowing which it holds. */
  private mug: THREE.Object3D;
  private readonly mugMaterial: THREE.MeshStandardMaterial;

  constructor() {
    this.root.add(buildTable());

    // Stoneware, not metal: the generated GLB ships with no material at all
    // (the fixture tools export bare geometry, leaving colour a client-side
    // decision), so without this it renders in three.js's default grey and
    // reads as untextured debug geometry rather than an object.
    this.mugMaterial = new THREE.MeshStandardMaterial({
      color: 0xd8dde3,
      roughness: 0.45,
      metalness: 0.02,
    });

    // A placeholder that is the mug's real size, swapped for the loaded mesh
    // when it arrives. Loading is async and the demonstrator should never see
    // an empty table -- and if the asset is missing entirely, a correctly
    // sized stand-in is a far better failure than nothing at all.
    const placeholder = new THREE.Mesh(
      new THREE.CylinderGeometry(MUG_BODY_RADIUS_M, MUG_BODY_RADIUS_M, MUG_HEIGHT_M, 24),
      this.mugMaterial,
    );
    placeholder.castShadow = true;
    // The placeholder cylinder is centre-origin; the real asset is
    // base-origin. Lifting the geometry by half its height makes the
    // placeholder honour the same contract, so `update` can place both by
    // their base without knowing which it has.
    placeholder.geometry.translate(0, MUG_HEIGHT_M / 2, 0);
    placeholder.name = "mug";
    this.mug = placeholder;
    this.root.add(this.mug);

    void this.loadMugAsset();
  }

  /** Replace the placeholder with the generated mug. Fire-and-forget: a
   * missing or broken asset leaves the correctly-sized stand-in in place
   * rather than emptying the table. */
  private async loadMugAsset(): Promise<void> {
    if (typeof location === "undefined") return;
    try {
      const gltf = await new GLTFLoader().loadAsync(MUG_ASSET_URL);
      const loaded = gltf.scene;
      loaded.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.material = this.mugMaterial;
          mesh.castShadow = true;
          mesh.receiveShadow = true;
        }
      });
      // The asset is authored in struct_world (Z-up, base at z=0), and this
      // scene renders in three.js's Y-up frame. `placeAtStruct` only moves an
      // object; without the matching basis rotation the mug renders lying on
      // its side -- which is exactly what it did. The fixture GLBs elsewhere
      // in this repo are authored the same way and get the same treatment.
      orientToStruct(loaded);
      loaded.name = "mug";
      this.root.remove(this.mug);
      this.root.add(loaded);
      this.mug = loaded;
    } catch (error) {
      console.warn(`MugPickupScene: ${MUG_ASSET_URL} did not load; keeping placeholder:`, error);
    }
  }

  update(state: MugVisualState, reachable: boolean): void {
    placeAtStruct(this.mug, state.mugPosition());

    const lit = reachable || state.isHeld;
    this.mugMaterial.emissive.setHex(lit ? REACHABLE_TINT : 0x000000);
    this.mugMaterial.emissiveIntensity = lit ? 0.45 : 0;
  }
}

function buildTable(): THREE.Object3D {
  const width = TABLE_Y_M[1] - TABLE_Y_M[0];
  const depth = TABLE_X_M[1] - TABLE_X_M[0];
  const centreX = (TABLE_X_M[0] + TABLE_X_M[1]) / 2;
  const centreY = (TABLE_Y_M[0] + TABLE_Y_M[1]) / 2;

  const group = new THREE.Group();

  // three.js (x, y, z) <- struct (y, z, x). Spelled out rather than inlined
  // because getting this pair the wrong way round has already produced a
  // visibly broken mesh once in this codebase.
  const top = new THREE.Mesh(
    new THREE.BoxGeometry(width, TABLE_THICKNESS_M, depth),
    new THREE.MeshStandardMaterial({ color: 0x6b5844, roughness: 0.85, metalness: 0.02 }),
  );
  top.receiveShadow = true;
  placeAtStruct(top, [centreX, centreY, TABLE_Z_M - TABLE_THICKNESS_M / 2]);
  group.add(top);

  // Four legs hanging below the surface. Purely visual, but without them the
  // tabletop reads as a slab floating in space, and a demonstrator's sense of
  // where the surface is comes largely from seeing it supported.
  const legRadius = 0.016;
  const legHeight = TABLE_DROP_M;
  const legMaterial = new THREE.MeshStandardMaterial({
    color: 0x4a3f33,
    roughness: 0.9,
    metalness: 0.05,
  });
  const inset = 0.05;
  for (const x of [TABLE_X_M[0] + inset, TABLE_X_M[1] - inset]) {
    for (const y of [TABLE_Y_M[0] + inset, TABLE_Y_M[1] - inset]) {
      const leg = new THREE.Mesh(
        new THREE.CylinderGeometry(legRadius, legRadius, legHeight, 10),
        legMaterial,
      );
      placeAtStruct(leg, [x, y, TABLE_Z_M - TABLE_THICKNESS_M - legHeight / 2]);
      group.add(leg);
    }
  }

  return group;
}

/** Lighting for a desk scene: a key that casts a real shadow so the mug sits
 * on the table rather than floating over it, plus fill so the far side of the
 * mug is readable. */
export function buildMugSceneLighting(scene: THREE.Scene): void {
  scene.add(new THREE.HemisphereLight(0xdce8ff, 0x20242c, 1.4));

  const key = new THREE.DirectionalLight(0xffffff, 2.0);
  key.position.set(1.2, 2.2, 1.4);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  const cam = key.shadow.camera as THREE.OrthographicCamera;
  cam.left = -1.2; cam.right = 1.2; cam.top = 1.2; cam.bottom = -1.2;
  cam.near = 0.1; cam.far = 6;
  scene.add(key);
}
