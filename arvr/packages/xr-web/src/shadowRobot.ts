/**
 * ShadowRobot — Shadow Robot Spatial Demonstration Pipeline spec section 29,
 * Phase 7. Animates the fixture SO-101 arm from q(t) (ArmRetargeter's
 * output, spec section 6). Distinct geometry from arm.ts's ArticulatedArm on
 * purpose: that class encodes ar_sim/scene_mjcf.py's *different* placeholder
 * robot (different link lengths, joint order, AND joint axes — wrist1 is Z
 * here vs. Y there, wrist2 is Y vs. X, wrist3 is X vs. Z). Reusing
 * ArticulatedArm directly would silently drive the wrong joints on the
 * wrong axes. Built from fixtures/spatial-training/robots/so101/robot_ir.json
 * (mirrored exactly against fixtures/robot/test_arm.urdf — see
 * tools/make_robot_bundle.py's ROBOT_IR_JOINTS table, the single source of
 * truth this file's constants are copied from).
 */

import * as THREE from "three";
import { structToWebxr } from "./adapter";
import { knuckle, segment } from "./arm";
import type { Vec3 } from "./contracts";

/** Mirrors ar_datapipe.arm_retargeter.IkStatus. */
export type IkStatus = "ok" | "failed" | "joint_limit";

// fixtures/spatial-training/robots/so101/robot_ir.json's joint origins
// (meters, along local +X unless noted). wrist2->wrist3 has zero offset --
// no segment there, just a knuckle.
const RISER_M = 0.1; // base_link -> shoulder_pan
const SHOULDER_LIFT_OFFSET_M = 0.05; // shoulder_pan -> shoulder_lift, along Z
const UPPER_ARM_M = 0.4; // shoulder_lift -> elbow
const FOREARM_M = 0.4; // elbow -> wrist1
const WRIST1_TO_WRIST2_M = 0.1;
const WRIST3_TO_EE_M = 0.08;

const BASE_HEIGHT_M = 0.05;

const METAL = 0xb8c0cc;
const TOOL_TINT = 0x61afef;

export class ShadowRobot {
  readonly root = new THREE.Group();

  private readonly shoulderPan = new THREE.Group();
  private readonly shoulderLift = new THREE.Group();
  private readonly elbow = new THREE.Group();
  private readonly wrist1 = new THREE.Group();
  private readonly wrist2 = new THREE.Group();
  private readonly wrist3 = new THREE.Group();
  private readonly tool = new THREE.Object3D();
  private readonly toolMesh: THREE.Mesh;
  private readonly ikMaterials: THREE.MeshStandardMaterial[] = [];

  constructor() {
    // Same struct_world -> three.js Y-up basis change as arm.ts, so every
    // joint axis below reads exactly as it does in robot_ir.json.
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
      new THREE.CylinderGeometry(0.1, 0.1, BASE_HEIGHT_M, 24),
      new THREE.MeshStandardMaterial({ color: METAL, roughness: 0.5, metalness: 0.3 }),
    );
    base.geometry.rotateX(Math.PI / 2);
    base.geometry.translate(0, 0, BASE_HEIGHT_M / 2);
    frame.add(base);

    // shoulder_pan, axis Z, at (0,0,0.1) from base_link
    this.shoulderPan.position.set(0, 0, RISER_M);
    frame.add(this.shoulderPan);
    const riser = segment(RISER_M, 0.045, METAL);
    riser.rotation.y = -Math.PI / 2; // local +X -> +Z, matches arm.ts's "column" trick
    frame.add(riser); // fixed structure, does not rotate with shoulder_pan

    // shoulder_lift, axis Y, at (0,0,0.05) from shoulder_pan
    this.shoulderLift.position.set(0, 0, SHOULDER_LIFT_OFFSET_M);
    this.shoulderPan.add(this.shoulderLift);
    const shoulderRiser = segment(SHOULDER_LIFT_OFFSET_M, 0.04, METAL);
    shoulderRiser.rotation.y = -Math.PI / 2;
    this.shoulderPan.add(shoulderRiser, knuckle(0.05));

    // elbow, axis Y, at (0.4,0,0) from shoulder_lift
    this.elbow.position.set(UPPER_ARM_M, 0, 0);
    this.shoulderLift.add(this.elbow);
    this.shoulderLift.add(segment(UPPER_ARM_M, 0.04, METAL), knuckle(0.05));

    // wrist1, axis Z (not Y -- see module docstring), at (0.4,0,0) from elbow
    this.wrist1.position.set(FOREARM_M, 0, 0);
    this.elbow.add(this.wrist1);
    this.elbow.add(segment(FOREARM_M, 0.035, METAL), knuckle(0.045));

    // wrist2, axis Y (not X), at (0.1,0,0) from wrist1
    this.wrist2.position.set(WRIST1_TO_WRIST2_M, 0, 0);
    this.wrist1.add(this.wrist2);
    this.wrist1.add(segment(WRIST1_TO_WRIST2_M, 0.03, METAL), knuckle(0.035));

    // wrist3, axis X (not Z), at (0,0,0) from wrist2 -- zero offset, no segment.
    this.wrist2.add(this.wrist3);
    this.wrist2.add(knuckle(0.03));

    this.toolMesh = new THREE.Mesh(
      new THREE.BoxGeometry(0.05, 0.035, 0.035),
      new THREE.MeshStandardMaterial({ color: TOOL_TINT, emissive: TOOL_TINT, emissiveIntensity: 0.3 }),
    );
    this.toolMesh.position.set(WRIST3_TO_EE_M / 2, 0, 0);
    this.wrist3.add(segment(WRIST3_TO_EE_M, 0.025, METAL), this.toolMesh, this.tool);
    this.tool.position.set(WRIST3_TO_EE_M, 0, 0);

    this.ikMaterials = [this.toolMesh.material as THREE.MeshStandardMaterial];
  }

  /** Place the whole robot's base at a position given in struct_world. */
  placeBase(position: Vec3): void {
    const [x, y, z] = structToWebxr.position(position);
    this.root.position.set(x, y, z);
  }

  /** Drive the six revolute joints, in robot_ir.json's order. */
  setJoints(q: readonly number[]): void {
    this.shoulderPan.rotation.z = q[0] ?? 0;
    this.shoulderLift.rotation.y = q[1] ?? 0;
    this.elbow.rotation.y = q[2] ?? 0;
    this.wrist1.rotation.z = q[3] ?? 0;
    this.wrist2.rotation.y = q[4] ?? 0;
    this.wrist3.rotation.x = q[5] ?? 0;
  }

  /** Spec section 29: "the robot ghost must clearly display impossible
   * states" -- never fabricate successful motion when the robot cannot
   * reach. Green = ok, orange = reachable but outside a declared limit,
   * red = IK did not converge at all. */
  setIkStatus(status: IkStatus): void {
    const color = status === "ok" ? 0x98c379 : status === "joint_limit" ? 0xe5c07b : 0xe06c75;
    for (const material of this.ikMaterials) {
      material.color.setHex(color);
      material.emissive.setHex(color);
    }
  }

  /** Tool tip in world space, for measuring EE error against the retarget target. */
  toolWorldPosition(target: THREE.Vector3): THREE.Vector3 {
    return this.tool.getWorldPosition(target);
  }
}
