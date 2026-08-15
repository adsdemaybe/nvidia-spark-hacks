/**
 * ShadowRobot — Shadow Robot Spatial Demonstration Pipeline spec section 29,
 * Phase 7. Animates the real SO-101 arm (Track A: swapped from an earlier
 * made-up placeholder to the actual, professional, open-source SO-101 --
 * github.com/TheRobotStudio/SO-ARM100, Apache-2.0) from q(t) (ArmRetargeter's
 * output). 6 joints, kinematic order: shoulder_pan, shoulder_lift,
 * elbow_flex, wrist_flex, wrist_roll, gripper -- 5 arm DOF + a real gripper
 * joint (the placeholder had none).
 *
 * JOINTS below mirrors fixtures/spatial-training/robots/so101/robot_ir.json
 * exactly -- origins computed from the vendored URDF's <origin rpy=".."/>
 * by tools/make_real_so101_bundle.py, copied here as literals (same
 * "single source of truth, hand-copied" precedent the placeholder chain
 * used). Unlike the placeholder, every joint here has a real rotated
 * origin (rpy), not just a translation -- so each joint is built as two
 * nested groups: a static `origin` group (position + origin_orientation_xyzw)
 * and a `joint` group inside it that setJoints() rotates. Every real joint's
 * axis is [0,0,1] *after* its origin rotation is applied (confirmed
 * directly against the URDF), so a plain local Z rotation on the inner
 * group is correct without needing a generic axis-angle setter.
 *
 * Origin position/orientation values are plugged in raw (no structToWebxr
 * conversion per-joint) -- the whole nested chain is authored in
 * struct_world's own local convention, and the *root* `frame` group below
 * is the one and only place the struct(Z-up) -> three.js(Y-up) basis
 * change happens. Nested local transforms compose correctly under that
 * root basis change automatically (standard scenegraph property); adding
 * a second conversion per-joint would double-convert and be wrong, the
 * same failure mode adapter.ts's own docstring warns about.
 *
 * Real STL meshes (vendored alongside the URDF) are loaded asynchronously
 * and best-effort -- a small sphere at every joint (arm.ts's `knuckle()`)
 * renders immediately and always, so the robot is visibly correct
 * (position/rotation-wise) even before, or if, real mesh loading fails.
 */

import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { structToWebxr } from "./adapter";
import { knuckle } from "./arm";
import type { Vec3 } from "./contracts";

/** Mirrors ar_datapipe.arm_retargeter.IkStatus. */
export type IkStatus = "ok" | "failed" | "joint_limit";

type Quat = [number, number, number, number];

interface JointSpec {
  name: string;
  originPositionM: Vec3;
  originOrientationXyzw: Quat;
}

// Kinematic order (base -> tip) -- NOT robot_ir.json's own array order
// (the vendored URDF's onshape-to-robot export lists joints tip-first).
// This is the same order ArmRetargeter/IkSolver produce q in (Pinocchio's
// model.names, confirmed directly: ['shoulder_pan', 'shoulder_lift',
// 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']).
const JOINTS: readonly JointSpec[] = [
  {
    name: "shoulder_pan",
    originPositionM: [0.0388353, -8.97657e-9, 0.0624],
    originOrientationXyzw: [1.326794896676365e-6, -0.9999999999982396, -1.326794896676365e-6, 1.760363785199545e-12],
  },
  {
    name: "shoulder_lift",
    originPositionM: [-0.0303992, -0.0182778, -0.0542],
    originOrientationXyzw: [-0.49999999999662686, -0.49999999999662686, -0.5000018366025516, 0.4999981633974483],
  },
  {
    name: "elbow_flex",
    originPositionM: [-0.11257, -0.028, 1.73763e-16],
    originOrientationXyzw: [-4.3766725580708164e-16, 1.8055643788175648e-16, 0.7071080798594735, 0.7071054825112363],
  },
  {
    name: "wrist_flex",
    originPositionM: [-0.1349, 0.0052, 3.62355e-17],
    originOrientationXyzw: [1.7295535595292469e-15, -1.116241234178666e-15, -0.7071080798594735, 0.7071054825112363],
  },
  {
    name: "wrist_roll",
    originPositionM: [5.55112e-17, -0.0611, 0.0181],
    originOrientationXyzw: [-0.017208133464804664, 0.7068986593349474, 0.7068960170901372, 0.01721007249293119],
  },
  {
    name: "gripper",
    originPositionM: [0.0202, 0.0188, -0.0234],
    originOrientationXyzw: [0.7071080798594733, -1.8536205040112397e-8, 1.8536272126587696e-8, 0.7071054825112361],
  },
] as const;

// The fixed tool frame (gripper_frame_joint in the URDF) -- past the jaw,
// this is what ArmRetargeter's IK target actually aims at.
const GRIPPER_FRAME: JointSpec = {
  name: "gripper_frame",
  originPositionM: [-0.0079, -0.000218121, -0.0981274],
  originOrientationXyzw: [0.0, 0.9999999999991198, 0.0, 1.3267948966775328e-6],
};

/** Which link's real meshes attach to which joint's local frame -- the
 * link is the joint's *child* link in the URDF. */
const LINK_FOR_JOINT: Readonly<Record<string, string>> = {
  shoulder_pan: "shoulder_link",
  shoulder_lift: "upper_arm_link",
  elbow_flex: "lower_arm_link",
  wrist_flex: "wrist_link",
  wrist_roll: "gripper_link",
  gripper: "moving_jaw_so101_v1_link",
};
const BASE_LINK = "base_link";

const MESH_BASE_URL = "/spatial-training/robots/so101/meshes/";
const VISUAL_MESHES_URL = "/spatial-training/robots/so101/visual_meshes.json";

const METAL = 0xb8c0cc;
const TOOL_TINT = 0x61afef;

interface VisualMeshEntry {
  mesh: string;
  origin_position_m: Vec3;
  origin_orientation_xyzw: Quat;
}
interface VisualMeshesManifest {
  links: Record<string, VisualMeshEntry[]>;
}

export class ShadowRobot {
  readonly root = new THREE.Group();

  private readonly jointGroups: THREE.Group[] = [];
  private readonly linkGroups = new Map<string, THREE.Group>();
  private readonly tool = new THREE.Object3D();
  private readonly toolMesh: THREE.Mesh;
  private readonly ikMaterials: THREE.MeshStandardMaterial[] = [];

  constructor() {
    // Same struct_world -> three.js Y-up basis change as arm.ts, applied
    // exactly once, at the root -- see module docstring.
    const frame = new THREE.Group();
    frame.quaternion.setFromRotationMatrix(
      new THREE.Matrix4().makeBasis(
        new THREE.Vector3(0, 0, -1),
        new THREE.Vector3(-1, 0, 0),
        new THREE.Vector3(0, 1, 0),
      ),
    );
    this.root.add(frame);

    const baseGroup = new THREE.Group();
    frame.add(baseGroup);
    baseGroup.add(knuckle(0.02));
    this.linkGroups.set(BASE_LINK, baseGroup);

    let parent: THREE.Group = baseGroup;
    // gripper_frame_joint's parent in the URDF is gripper_link (wrist_roll's
    // child), a *sibling* of the gripper joint's own moving-jaw branch --
    // not the gripper joint itself. Track it separately so the tool frame
    // doesn't swing with the jaw when it opens/closes.
    let gripperLinkGroup: THREE.Group = baseGroup;
    for (const joint of JOINTS) {
      const origin = new THREE.Group();
      origin.position.set(...joint.originPositionM);
      origin.quaternion.set(...joint.originOrientationXyzw);
      parent.add(origin);

      const jointGroup = new THREE.Group();
      origin.add(jointGroup);
      jointGroup.add(knuckle(joint.name === "gripper" ? 0.008 : 0.015));
      this.jointGroups.push(jointGroup);
      this.linkGroups.set(LINK_FOR_JOINT[joint.name]!, jointGroup);

      if (joint.name === "wrist_roll") gripperLinkGroup = jointGroup;
      parent = jointGroup;
    }

    const gripperFrameOrigin = new THREE.Group();
    gripperFrameOrigin.position.set(...GRIPPER_FRAME.originPositionM);
    gripperFrameOrigin.quaternion.set(...GRIPPER_FRAME.originOrientationXyzw);
    gripperLinkGroup.add(gripperFrameOrigin);

    this.toolMesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.01, 12, 8),
      new THREE.MeshStandardMaterial({ color: TOOL_TINT, emissive: TOOL_TINT, emissiveIntensity: 0.3 }),
    );
    gripperFrameOrigin.add(this.toolMesh, this.tool);
    this.ikMaterials = [this.toolMesh.material as THREE.MeshStandardMaterial];

    // Fire-and-forget: the marker skeleton above is already a complete,
    // kinematically-correct robot on its own. Real meshes only ever add
    // to it, and never block or fail construction.
    void this.loadRealMeshes();
  }

  private async loadRealMeshes(): Promise<void> {
    // No DOM/page origin outside a real browser (e.g. under vitest's node
    // environment) -- a relative fetch URL has nothing to resolve against
    // there. The marker skeleton is already complete on its own; skip
    // straight to it rather than let every unit test print a fetch error.
    if (typeof location === "undefined") return;

    let manifest: VisualMeshesManifest;
    try {
      manifest = (await fetch(VISUAL_MESHES_URL).then((r) => r.json())) as VisualMeshesManifest;
    } catch (error) {
      console.warn("ShadowRobot: visual_meshes.json unavailable, using marker-only skeleton:", error);
      return;
    }

    const loader = new STLLoader();
    const material = new THREE.MeshStandardMaterial({ color: METAL, roughness: 0.5, metalness: 0.3 });
    for (const [linkName, entries] of Object.entries(manifest.links)) {
      const group = this.linkGroups.get(linkName);
      if (!group) continue;
      for (const entry of entries) {
        try {
          const geometry = await loader.loadAsync(MESH_BASE_URL + entry.mesh);
          const mesh = new THREE.Mesh(geometry, material);
          mesh.position.set(...entry.origin_position_m);
          mesh.quaternion.set(...entry.origin_orientation_xyzw);
          mesh.castShadow = true;
          group.add(mesh);
        } catch (error) {
          console.warn(`ShadowRobot: failed to load mesh ${entry.mesh} for link ${linkName}:`, error);
        }
      }
    }
  }

  /** Place the whole robot's base at a position given in struct_world. */
  placeBase(position: Vec3): void {
    const [x, y, z] = structToWebxr.position(position);
    this.root.position.set(x, y, z);
  }

  /** Drive all 6 joints, robot_ir.json's kinematic order (shoulder_pan,
   * shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper). */
  setJoints(q: readonly number[]): void {
    for (let i = 0; i < this.jointGroups.length; i += 1) {
      this.jointGroups[i]!.rotation.z = q[i] ?? 0;
    }
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
