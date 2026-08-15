import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { ShadowRobot } from "./shadowRobot";

// Accessors reach into the private joint Groups via the object's own
// rotation state after setJoints -- there's no public per-joint getter
// (setJoints/toolWorldPosition are the only outputs a caller needs), so
// these tests drive the robot then read back through toolWorldPosition and
// the material color setIkStatus drives, which are the two things a caller
// actually depends on.

describe("ShadowRobot.setJoints", () => {
  it("moves the tool tip when driven away from the zero pose", () => {
    const robot = new ShadowRobot();
    const atZero = robot.toolWorldPosition(new THREE.Vector3());
    robot.setJoints([0.3, 0.4, -0.3, 0.2, 0.1, 0.5]);
    const atPose = robot.toolWorldPosition(new THREE.Vector3());
    expect(atZero.distanceTo(atPose)).toBeGreaterThan(0.01);
  });

  it("shoulder_pan (q[0]) rotates about Z, not some other axis", () => {
    const robot = new ShadowRobot();
    const before = robot.toolWorldPosition(new THREE.Vector3());
    robot.setJoints([Math.PI / 2, 0, 0, 0, 0, 0]);
    const after = robot.toolWorldPosition(new THREE.Vector3());
    // A pure Z-axis rotation in struct_world changes X/Y but not Z (the
    // three.js scene is Y-up after the basis change, so struct_world's Z
    // maps to three.js's Y). shoulder_pan's real URDF origin is *almost*
    // exactly a 180-degree flip about Y (not perfectly, per the vendored
    // CAD export's own floating-point origin -- a few micrometers of
    // height drift is real, physically-accurate robot geometry, not a
    // bug); a wrong axis entirely would move the tool by centimeters, not
    // micrometers, so this tolerance is loose enough to absorb the real
    // near-zero epsilon while still catching a genuine axis mix-up.
    expect(Math.abs(after.y - before.y)).toBeLessThan(1e-5);
  });

  it("accepts a partial joint array without throwing", () => {
    const robot = new ShadowRobot();
    expect(() => robot.setJoints([0.1, 0.2])).not.toThrow();
  });

  it("gripper (q[5]) does not move the tool tip -- it's a sibling frame off gripper_link, not downstream of the jaw", () => {
    // Regression test: gripper_frame_joint's URDF parent is gripper_link
    // (wrist_roll's child), not the gripper joint's own moving-jaw branch.
    // An earlier version attached it to whatever the joint-building loop's
    // `parent` last was (the gripper joint itself), which would make the
    // tool tip incorrectly swing every time the jaw opened or closed.
    const robot = new ShadowRobot();
    const closed = robot.toolWorldPosition(new THREE.Vector3());
    robot.setJoints([0, 0, 0, 0, 0, 1.5]);
    const open = robot.toolWorldPosition(new THREE.Vector3());
    expect(closed.distanceTo(open)).toBeLessThan(1e-9);
  });

  it("wrist_roll (q[4]), unlike gripper, does move the tool tip", () => {
    const robot = new ShadowRobot();
    const before = robot.toolWorldPosition(new THREE.Vector3());
    robot.setJoints([0, 0, 0, 0, 1.0, 0]);
    const after = robot.toolWorldPosition(new THREE.Vector3());
    expect(before.distanceTo(after)).toBeGreaterThan(0.001);
  });
});

describe("ShadowRobot.setIkStatus", () => {
  it("drives a distinct color per status (never hides IK failure)", () => {
    const robot = new ShadowRobot();
    const colorFor = (status: "ok" | "failed" | "joint_limit"): number => {
      robot.setIkStatus(status);
      const toolMesh = (
        robot as unknown as { toolMesh: { material: { color: { getHex(): number } } } }
      ).toolMesh;
      return toolMesh.material.color.getHex();
    };
    const ok = colorFor("ok");
    const failed = colorFor("failed");
    const limit = colorFor("joint_limit");
    expect(new Set([ok, failed, limit]).size).toBe(3);
  });
});

describe("ShadowRobot.placeBase", () => {
  it("moves the whole robot root, not just one joint", () => {
    const robot = new ShadowRobot();
    robot.placeBase([1, 2, 3]);
    expect(robot.root.position.length()).toBeGreaterThan(0);
  });
});
