/**
 * An articulated arm driven by TwinState.robot.joint_positions.
 *
 * Without this, TWIN renders a static robot GLB and prints a joint angle as
 * text -- which is a number changing next to a model that never moves, and
 * proves nothing about whether the client is really following the simulation
 * (STRUCT_2.md 65 requires joint state to visibly update the robot).
 *
 * The kinematics mirror arxr-sim/scene_mjcf.py exactly. It is a second
 * description of one robot, which is a real risk; the guard is that both are
 * built from the same published link lengths and the reach envelope drawn from
 * them is what PLACE tests against.
 */

import * as THREE from "three";
import { structToWebxr } from "./adapter";
import type { Vec3 } from "./contracts";

/** Mirrors LINK in arxr-sim/scene_mjcf.py: column, upper, forearm, wrist. */
export const LINK = { column: 0.95, upper: 0.45, forearm: 0.36, wrist: 0.16 } as const;
export const BASE_HEIGHT = 0.1;

/** Furthest the tool can get from the shoulder, for the PLACE envelope. */
export const REACH_M = LINK.upper + LINK.forearm + LINK.wrist + 0.03;
export const SHOULDER_HEIGHT = BASE_HEIGHT + LINK.column;

const METAL = 0xc8ccd4;
const JOINT_TINT = 0x9aa3b2;
const TOOL_TINT = 0xe06c75;

/**
 * A link running from the joint origin along local +X, matching the MJCF
 * capsules' `fromto`. Baked into the geometry rather than set as an object
 * rotation, so a caller can still orient the whole segment (the shoulder
 * column points along +Z) without the two rotations fighting.
 */
function segment(length: number, radius: number, color: number): THREE.Mesh {
  const geometry = new THREE.CylinderGeometry(radius, radius, length, 18);
  geometry.translate(0, length / 2, 0); // grow from the origin, not about it
  geometry.rotateZ(-Math.PI / 2); // +Y -> +X
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.35 }),
  );
  mesh.castShadow = true;
  return mesh;
}

function knuckle(radius: number): THREE.Mesh {
  return new THREE.Mesh(
    new THREE.SphereGeometry(radius, 16, 12),
    new THREE.MeshStandardMaterial({ color: JOINT_TINT, roughness: 0.5, metalness: 0.3 }),
  );
}

/**
 * Built in struct_world axes (Z-up) inside `root`, and `root` carries the one
 * rotation into three.js Y-up -- so every joint axis below reads exactly as it
 * does in the MJCF.
 */
export class ArticulatedArm {
  readonly root = new THREE.Group();

  private readonly pan = new THREE.Group();
  private readonly lift = new THREE.Group();
  private readonly elbow = new THREE.Group();
  private readonly wrist1 = new THREE.Group();
  private readonly wrist2 = new THREE.Group();
  private readonly wrist3 = new THREE.Group();
  private readonly tool = new THREE.Object3D();

  constructor() {
    const frame = new THREE.Group();
    frame.quaternion.setFromRotationMatrix(
      new THREE.Matrix4().makeBasis(
        new THREE.Vector3(0, 0, -1),
        new THREE.Vector3(-1, 0, 0),
        new THREE.Vector3(0, 1, 0),
      ),
    );
    this.root.add(frame);

    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(0.15, 0.15, BASE_HEIGHT, 24),
      new THREE.MeshStandardMaterial({ color: METAL, roughness: 0.5, metalness: 0.3 }),
    );
    base.geometry.rotateX(Math.PI / 2);
    base.geometry.translate(0, 0, BASE_HEIGHT / 2);
    base.castShadow = true;
    frame.add(base);

    // shoulder_pan about Z
    this.pan.position.set(0, 0, BASE_HEIGHT);
    frame.add(this.pan);

    // The column is the one link that runs up (+Z) rather than out (+X).
    const column = segment(LINK.column, 0.05, METAL);
    column.rotation.y = -Math.PI / 2; // +X -> +Z
    this.pan.add(column, knuckle(0.06));

    // shoulder_lift about Y
    this.lift.position.set(0, 0, LINK.column);
    this.pan.add(this.lift);
    this.lift.add(segment(LINK.upper, 0.045, METAL), knuckle(0.055));

    // elbow about Y
    this.elbow.position.set(LINK.upper, 0, 0);
    this.lift.add(this.elbow);
    this.elbow.add(segment(LINK.forearm, 0.038, METAL), knuckle(0.046));

    // wrist_1 about Y
    this.wrist1.position.set(LINK.forearm, 0, 0);
    this.elbow.add(this.wrist1);
    this.wrist1.add(segment(LINK.wrist, 0.03, METAL), knuckle(0.036));

    // wrist_2 about X, wrist_3 about Z
    this.wrist2.position.set(LINK.wrist, 0, 0);
    this.wrist1.add(this.wrist2);
    this.wrist2.add(knuckle(0.028));
    this.wrist3.position.set(0.03, 0, 0);
    this.wrist2.add(this.wrist3);

    const toolMesh = new THREE.Mesh(
      new THREE.BoxGeometry(0.06, 0.04, 0.04),
      new THREE.MeshStandardMaterial({ color: TOOL_TINT, emissive: TOOL_TINT, emissiveIntensity: 0.2 }),
    );
    toolMesh.castShadow = true;
    this.wrist3.add(toolMesh, this.tool);
    this.tool.position.set(0.03, 0, 0);
  }

  /** Place the whole arm at a base position given in struct_world. */
  placeBase(position: Vec3): void {
    const [x, y, z] = structToWebxr.position(position);
    this.root.position.set(x, y, z);
  }

  /** Drive the six revolute joints, in the MJCF's order. */
  setJoints(q: readonly number[]): void {
    this.pan.rotation.z = q[0] ?? 0;
    this.lift.rotation.y = q[1] ?? 0;
    this.elbow.rotation.y = q[2] ?? 0;
    this.wrist1.rotation.y = q[3] ?? 0;
    this.wrist2.rotation.x = q[4] ?? 0;
    this.wrist3.rotation.z = q[5] ?? 0;
  }

  /** Tool tip in world space, for drawing what the arm is about to touch. */
  toolWorldPosition(target: THREE.Vector3): THREE.Vector3 {
    return this.tool.getWorldPosition(target);
  }
}

/**
 * Is a point inside the arm's reach envelope?
 *
 * Purely geometric -- a sphere about the shoulder. It is the honest version of
 * what PLACE can claim without running IK in the browser: outside this, the
 * pose is definitely unreachable; inside, it is plausible and the simulator has
 * the final say.
 */
export function reachStatus(
  basePosition: Vec3,
  point: Vec3,
): { reachable: boolean; distance: number; margin: number } {
  const shoulder: Vec3 = [basePosition[0], basePosition[1], basePosition[2] + SHOULDER_HEIGHT];
  const distance = Math.hypot(
    point[0] - shoulder[0],
    point[1] - shoulder[1],
    point[2] - shoulder[2],
  );
  return { reachable: distance <= REACH_M, distance, margin: REACH_M - distance };
}
