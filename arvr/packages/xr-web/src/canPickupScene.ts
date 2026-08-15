/**
 * The can-pickup scene, rendered: a table, a can, and the arm.
 *
 * Nothing else is in here. Every additional prop is one more thing a
 * demonstrator can knock over by accident and one more object a policy has to
 * learn to ignore, so the scene contains only the surface, the target, and
 * the robot the data is for.
 *
 * View layer only -- it reads `CanPickupTask` and draws it. Whether the can
 * counts as picked up is decided there, in code a test can pin down without a
 * renderer.
 */

import * as THREE from "three";
import { placeAtStruct } from "./scene";
import {
  CAN_HEIGHT_M,
  CAN_RADIUS_M,
  TABLE_DROP_M,
  TABLE_THICKNESS_M,
  TABLE_X_M,
  TABLE_Y_M,
  TABLE_Z_M,
} from "./canPickupLayout";
import type { Vec3 } from "./contracts";

/** What the can turns when a hand is close enough to take it. Feedback before
 * the grasp, so a demonstrator learns the catch distance instead of guessing
 * and producing a take full of failed grabs. */
const REACHABLE_TINT = 0xffd479;

export interface CanVisualState {
  canPosition(): Vec3;
  readonly isHeld: boolean;
}

export class CanPickupScene {
  readonly root = new THREE.Group();
  private readonly can: THREE.Mesh;
  private readonly canMaterial: THREE.MeshStandardMaterial;

  constructor() {
    this.root.add(buildTable());

    this.canMaterial = new THREE.MeshStandardMaterial({
      color: 0xc0392b,
      roughness: 0.32,
      metalness: 0.55,
    });
    // A cylinder, upright. three.js builds cylinders along its own +Y, which
    // is struct +Z once placeAtStruct's basis change is applied -- so an
    // unrotated cylinder already stands up in this scene.
    this.can = new THREE.Mesh(
      new THREE.CylinderGeometry(CAN_RADIUS_M, CAN_RADIUS_M, CAN_HEIGHT_M, 28),
      this.canMaterial,
    );
    this.can.castShadow = true;
    this.can.name = "soda_can";
    this.root.add(this.can);
  }

  update(state: CanVisualState, reachable: boolean): void {
    placeAtStruct(this.can, state.canPosition());

    const lit = reachable || state.isHeld;
    this.canMaterial.emissive.setHex(lit ? REACHABLE_TINT : 0x000000);
    this.canMaterial.emissiveIntensity = lit ? 0.45 : 0;
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

/** Lighting for a desk scene: a key that casts a real shadow so the can sits
 * on the table rather than floating over it, plus fill so the far side of the
 * can is readable. */
export function buildCanSceneLighting(scene: THREE.Scene): void {
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
