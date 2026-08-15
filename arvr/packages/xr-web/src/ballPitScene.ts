/**
 * The room-scale ball pit, rendered.
 *
 * View layer only: it reads task state and draws it, and decides nothing
 * about scoring or grasping. Same split as `sortScene.ts`, for the same
 * reason -- the predicate has to be testable without a GPU.
 *
 * What makes this feel like a *place* rather than objects floating in a void
 * is mostly the floor and the horizon. In an immersive VR session there is no
 * passthrough to ground you, so without a visible floor at your actual foot
 * level the scene reads as a diorama you are looking at rather than a room
 * you are standing in. Everything below is in service of that.
 */

import * as THREE from "three";
import { placeAtStruct } from "./scene";
import {
  BALLS,
  BALL_RADIUS_M,
  BINS,
  BIN_HEIGHT_M,
  BIN_INTERIOR_M,
  BIN_WALL_THICKNESS_M,
  FLOOR_Z_M,
  PIT_X_M,
  PIT_Y_M,
  type BallColor,
} from "./ballPitLayout";
import type { Vec3 } from "./contracts";

const COLORS: Record<BallColor, number> = { red: 0xe0574f, blue: 0x4a9fe8 };

/** How much bigger a ball looks when a hand is close enough to grab it.
 * Scale rather than colour: at arm's length in a headset, a size change is
 * far easier to read than a tint, and it survives being seen peripherally. */
const HIGHLIGHT_SCALE = 1.18;

export interface BallPitVisualState {
  ballPosition(id: string): Vec3;
}

export class BallPitScene {
  readonly root = new THREE.Group();
  private readonly meshes = new Map<string, THREE.Mesh>();
  private readonly materials = new Map<string, THREE.MeshStandardMaterial>();

  constructor() {
    this.root.add(buildFloor());
    this.root.add(buildPitMarking());
    for (const bin of BINS) this.root.add(buildBin(bin.center, COLORS[bin.color]));

    for (const ball of BALLS) {
      const material = new THREE.MeshStandardMaterial({
        color: COLORS[ball.color],
        roughness: 0.28,
        metalness: 0.04,
      });
      // Enough segments that an 18cm sphere held 30cm from your eye still
      // reads as round; a low-poly ball is obvious at that distance.
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(BALL_RADIUS_M, 32, 20), material);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.name = ball.id;
      placeAtStruct(mesh, ball.start);
      this.meshes.set(ball.id, mesh);
      this.materials.set(ball.id, material);
      this.root.add(mesh);
    }
  }

  /**
   * Redraw from task state.
   *
   * `highlighted` is the set of balls either hand could grab right now,
   * computed by the grasp layer rather than here -- so what lights up and
   * what can actually be caught can never disagree.
   */
  update(state: BallPitVisualState, highlighted: ReadonlySet<string>): void {
    for (const ball of BALLS) {
      const mesh = this.meshes.get(ball.id)!;
      placeAtStruct(mesh, state.ballPosition(ball.id));

      const material = this.materials.get(ball.id)!;
      const lit = highlighted.has(ball.id);
      material.emissive.setHex(lit ? 0xffffff : 0x000000);
      material.emissiveIntensity = lit ? 0.4 : 0;
      const scale = lit ? HIGHLIGHT_SCALE : 1;
      mesh.scale.setScalar(scale);
    }
  }
}

/**
 * The floor.
 *
 * Large enough to reach the horizon in every direction, with a grid fine
 * enough to give parallax when you move your head. The grid is what actually
 * sells "I am standing in a room" -- a flat untextured plane gives the eye
 * nothing to track against and reads as fog.
 */
function buildFloor(): THREE.Object3D {
  const group = new THREE.Group();

  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(30, 30),
    new THREE.MeshStandardMaterial({ color: 0x161a21, roughness: 1, metalness: 0 }),
  );
  plane.rotation.x = -Math.PI / 2;
  plane.position.y = FLOOR_Z_M - 0.002; // just under the grid, no z-fighting
  plane.receiveShadow = true;
  group.add(plane);

  const grid = new THREE.GridHelper(30, 60, 0x2f3947, 0x232a34);
  grid.position.y = FLOOR_Z_M;
  group.add(grid);

  return group;
}

/** A soft mat under the scattered balls, so the pit reads as a defined area
 * to work in rather than balls that happen to be lying about. */
function buildPitMarking(): THREE.Object3D {
  const width = PIT_Y_M[1] - PIT_Y_M[0] + BALL_RADIUS_M * 4;
  const depth = PIT_X_M[1] - PIT_X_M[0] + BALL_RADIUS_M * 4;
  const mat = new THREE.Mesh(
    new THREE.PlaneGeometry(width, depth),
    new THREE.MeshStandardMaterial({
      color: 0x1e2733,
      roughness: 1,
      transparent: true,
      opacity: 0.85,
    }),
  );
  mat.rotation.x = -Math.PI / 2;
  placeAtStruct(mat, [
    (PIT_X_M[0] + PIT_X_M[1]) / 2,
    (PIT_Y_M[0] + PIT_Y_M[1]) / 2,
    FLOOR_Z_M + 0.001,
  ]);
  mat.receiveShadow = true;
  return mat;
}

/**
 * A waist-height open bin: floor, four walls, and a bright rim.
 *
 * The rim matters more than it sounds. Judging the height of an opening in VR
 * is genuinely hard, and a coloured lip gives a clear visual target to release
 * a ball over -- without it people drop balls against the outside wall and
 * cannot see why nothing scored.
 */
function buildBin(center: Vec3, color: number): THREE.Object3D {
  const group = new THREE.Group();
  const inner = BIN_INTERIOR_M;
  const wall = BIN_WALL_THICKNESS_M;
  const height = BIN_HEIGHT_M;

  const shell = new THREE.MeshStandardMaterial({
    color,
    roughness: 0.55,
    metalness: 0.05,
    transparent: true,
    opacity: 0.42,
    side: THREE.DoubleSide,
  });

  // three.js (x, y, z) <- struct (y, z, x). Getting this pair the wrong way
  // round builds crossed sheets instead of a box; that bug already happened
  // once in sortScene.ts, so the mapping is spelled out rather than inlined.
  const floor = new THREE.Mesh(new THREE.BoxGeometry(inner, wall, inner), shell);
  placeAtStruct(floor, [center[0], center[1], center[2] - wall / 2]);
  group.add(floor);

  const half = inner / 2;
  const walls = [
    { dx: half, dy: 0, spanX: wall, spanY: inner },
    { dx: -half, dy: 0, spanX: wall, spanY: inner },
    { dx: 0, dy: half, spanX: inner, spanY: wall },
    { dx: 0, dy: -half, spanX: inner, spanY: wall },
  ];
  for (const { dx, dy, spanX, spanY } of walls) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(spanY, height, spanX), shell);
    placeAtStruct(mesh, [center[0] + dx, center[1] + dy, center[2] + height / 2]);
    group.add(mesh);
  }

  const rim = new THREE.Mesh(
    new THREE.TorusGeometry(inner * 0.72, 0.018, 10, 40),
    new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.5 }),
  );
  rim.rotation.x = Math.PI / 2;
  placeAtStruct(rim, [center[0], center[1], center[2] + height]);
  group.add(rim);

  return group;
}

/** Lighting for a room you stand in: a bright key that casts real shadows so
 * balls sit *on* the floor rather than hovering over it, plus enough ambient
 * fill that the far side of a ball is not black. */
export function buildBallPitLighting(scene: THREE.Scene): void {
  scene.add(new THREE.HemisphereLight(0xdce8ff, 0x1a1d23, 1.5));

  const key = new THREE.DirectionalLight(0xffffff, 2.1);
  key.position.set(2.5, 4.5, 2.0);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  // Tight ortho frustum around the play area: a default-sized shadow camera
  // spread over 30m of floor gives shadows too coarse to read.
  const cam = key.shadow.camera as THREE.OrthographicCamera;
  cam.left = -4; cam.right = 4; cam.top = 4; cam.bottom = -4;
  cam.near = 0.1; cam.far = 14;
  scene.add(key);
}
