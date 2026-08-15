/**
 * The ball-sorting scene, rendered.
 *
 * View layer only. It reads `SortTask` and draws what that says; it never
 * decides whether a ball is in a basket, and it holds no task state of its
 * own. Keeping the predicate out of the renderer is what lets the same task
 * run headless in a test and in a headset without two answers.
 *
 * Everything is authored in struct_world (Z-up, meters) and placed through
 * scene.ts's `placeAtStruct`, the one conversion into three.js's Y-up frame.
 */

import * as THREE from "three";
import { placeAtStruct } from "./scene";
import {
  BALLS,
  BALL_RADIUS_M,
  BASKETS,
  BASKET_INTERIOR_M,
  BASKET_WALL_HEIGHT_M,
  BASKET_WALL_THICKNESS_M,
  TABLE_Z_M,
  WORKSPACE_X_M,
  WORKSPACE_Y_M,
  type BallColor,
} from "./sortLayout";
import type { SortTask } from "./sortTask";
import type { Vec3 } from "./contracts";

const COLORS: Record<BallColor, number> = { red: 0xe06c75, blue: 0x61afef };
/** What a ball turns when the hand is close enough to catch it. Feedback
 * before the grasp, so a human learns the reach instead of guessing. */
const REACHABLE_TINT = 0xffffff;

export class SortScene {
  readonly root = new THREE.Group();
  private readonly ballMeshes = new Map<string, THREE.Mesh>();
  private readonly ballMaterials = new Map<string, THREE.MeshStandardMaterial>();

  constructor() {
    this.root.add(buildTable());
    for (const basket of BASKETS) this.root.add(buildBasket(basket.center, COLORS[basket.color]));

    for (const ball of BALLS) {
      const material = new THREE.MeshStandardMaterial({
        color: COLORS[ball.color],
        roughness: 0.35,
        metalness: 0.05,
      });
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(BALL_RADIUS_M, 24, 16), material);
      mesh.castShadow = true;
      mesh.name = ball.id;
      placeAtStruct(mesh, ball.start);
      this.ballMeshes.set(ball.id, mesh);
      this.ballMaterials.set(ball.id, material);
      this.root.add(mesh);
    }
  }

  /**
   * Redraw from task state.
   *
   * `highlightId` is whatever the hand could grab or is already holding --
   * passed in rather than recomputed here, so the grasp rule stays in
   * grasp.ts and cannot drift between what is highlighted and what is
   * actually catchable.
   */
  update(task: SortTask, highlightId: string | null): void {
    for (const ball of BALLS) {
      const mesh = this.ballMeshes.get(ball.id)!;
      placeAtStruct(mesh, task.ballPosition(ball.id));

      const material = this.ballMaterials.get(ball.id)!;
      const highlighted = ball.id === highlightId;
      material.emissive.setHex(highlighted ? REACHABLE_TINT : 0x000000);
      material.emissiveIntensity = highlighted ? 0.35 : 0;
    }
  }
}

function buildTable(): THREE.Object3D {
  const width = WORKSPACE_Y_M[1] - WORKSPACE_Y_M[0];
  const depth = WORKSPACE_X_M[1] - WORKSPACE_X_M[0];
  const centerX = (WORKSPACE_X_M[0] + WORKSPACE_X_M[1]) / 2;
  const centerY = (WORKSPACE_Y_M[0] + WORKSPACE_Y_M[1]) / 2;

  // A slab rather than a plane: in passthrough AR a zero-thickness surface
  // disappears edge-on, which makes the whole workspace look like it is
  // floating.
  const thickness = 0.012;
  const table = new THREE.Mesh(
    // struct (depth=x, width=y, thickness=z) -> three.js (x=width, y=thickness, z=depth)
    new THREE.BoxGeometry(width, thickness, depth),
    new THREE.MeshStandardMaterial({ color: 0x3b4252, roughness: 0.9, metalness: 0.02 }),
  );
  table.receiveShadow = true;
  placeAtStruct(table, [centerX, centerY, TABLE_Z_M - thickness / 2]);
  return table;
}

/** An open-topped box: floor plus four walls, so a ball dropped in is
 * visibly contained rather than resting on a decorative outline. */
function buildBasket(center: Vec3, color: number): THREE.Object3D {
  const group = new THREE.Group();
  const material = new THREE.MeshStandardMaterial({
    color,
    roughness: 0.6,
    metalness: 0.05,
    transparent: true,
    opacity: 0.55,
    side: THREE.DoubleSide,
  });

  const inner = BASKET_INTERIOR_M;
  const wall = BASKET_WALL_THICKNESS_M;
  const height = BASKET_WALL_HEIGHT_M;

  const floor = new THREE.Mesh(new THREE.BoxGeometry(inner, wall, inner), material);
  placeAtStruct(floor, [center[0], center[1], center[2] - wall / 2]);
  group.add(floor);

  // Each wall's extents in struct_world (X = depth, Y = width, Z = up), then
  // handed to BoxGeometry in three.js's order. A wall on the +X face is thin
  // in X and spans the full width in Y -- getting that pair the wrong way
  // round builds two crossed sheets instead of a box, which is exactly what
  // the first browser render showed.
  const half = inner / 2;
  const walls: Array<{ dx: number; dy: number; spanX: number; spanY: number }> = [
    { dx: half, dy: 0, spanX: wall, spanY: inner },
    { dx: -half, dy: 0, spanX: wall, spanY: inner },
    { dx: 0, dy: half, spanX: inner, spanY: wall },
    { dx: 0, dy: -half, spanX: inner, spanY: wall },
  ];
  for (const { dx, dy, spanX, spanY } of walls) {
    // three.js (x, y, z) <- struct (y, z, x)
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(spanY, height, spanX), material);
    placeAtStruct(mesh, [center[0] + dx, center[1] + dy, center[2] + height / 2]);
    group.add(mesh);
  }
  return group;
}
