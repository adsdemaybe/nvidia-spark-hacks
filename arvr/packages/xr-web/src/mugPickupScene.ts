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

/**
 * Where the hand is over the table, and how high.
 *
 * Perspective and shadows tell you *roughly* where something is, but a
 * reaching task needs better than roughly: you have to know whether your hand
 * is short of the mug or past it, and a shadow under a diffuse light is too
 * soft to read that from. A hard marker on the table directly beneath the
 * hand separates the two questions -- the ring answers "where on the table",
 * the drop line answers "how high" -- and both are exact.
 */

export class MugPickupScene {
  readonly root = new THREE.Group();
  /** The placeholder first, then the loaded asset. Both are base-origin, so
   * `update` can place either without knowing which it holds. */
  private mug: THREE.Object3D;
  private readonly mugMaterial: THREE.MeshStandardMaterial;
  /** Ring on the tabletop under the hand, and a line from it up to the hand. */
  private readonly handMarker: THREE.Mesh;
  private readonly dropLine: THREE.Line;
  /** The same ring under the mug, so the two can be compared directly rather
   * than judged separately against a background. */
  private readonly mugMarker: THREE.Mesh;

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

    // Markers live at the tabletop, flat. Rendered without depth-testing
    // against the table so they read as projections onto it rather than
    // z-fighting decals.
    this.mugMarker = makeMarker(0xffd479, 0.055);
    this.root.add(this.mugMarker);

    this.handMarker = makeMarker(0x61afef, 0.045);
    this.handMarker.visible = false;
    this.root.add(this.handMarker);

    this.dropLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color: 0x61afef, dashSize: 0.015, gapSize: 0.012 }),
    );
    this.dropLine.visible = false;
    this.root.add(this.dropLine);

    void this.loadMugAsset();
  }

  /**
   * Show where the hand is over the table. Pass null when it is untracked --
   * a stale marker is worse than none, because it reads as a hand that is
   * still there.
   */
  updateHand(handStruct: Vec3 | null): void {
    if (!handStruct) {
      this.handMarker.visible = false;
      this.dropLine.visible = false;
      return;
    }

    this.handMarker.visible = true;
    placeAtStruct(this.handMarker, [handStruct[0], handStruct[1], TABLE_Z_M + 0.0015]);

    const from = structToWebxrPoint([handStruct[0], handStruct[1], TABLE_Z_M]);
    const to = structToWebxrPoint(handStruct);
    this.dropLine.geometry.setFromPoints([
      new THREE.Vector3(...from),
      new THREE.Vector3(...to),
    ]);
    this.dropLine.computeLineDistances();
    this.dropLine.visible = true;
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
    const mugAt = state.mugPosition();
    placeAtStruct(this.mug, mugAt);
    placeAtStruct(this.mugMarker, [mugAt[0], mugAt[1], TABLE_Z_M + 0.001]);

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

/** A flat ring lying on the tabletop. Drawn as a ring rather than a disc so
 * it never hides the surface texture it is meant to be projected onto. */
function makeMarker(color: number, radius: number): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.RingGeometry(radius * 0.72, radius, 32),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.75,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  // The ring is authored in the XY plane; the tabletop is the XY plane in
  // struct_world, so orienting it the same way every other struct-authored
  // object is oriented lays it flat.
  orientToStruct(mesh);
  return mesh;
}

/** struct_world -> three.js, for raw points that are not object placements.
 * Mirrors adapter.ts's structToWebxr; kept local because this is the only
 * place in this module that needs a bare point rather than an object move. */
function structToWebxrPoint([x, y, z]: Vec3): [number, number, number] {
  return [-y, z, -x];
}
