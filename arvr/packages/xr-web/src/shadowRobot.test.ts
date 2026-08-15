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
    // maps to three.js's Y) -- if this were wired to the wrong axis (as
    // arm.ts's different robot uses for some of these joints), the height
    // would change instead.
    expect(Math.abs(after.y - before.y)).toBeLessThan(1e-6);
  });

  it("accepts a partial joint array without throwing", () => {
    const robot = new ShadowRobot();
    expect(() => robot.setJoints([0.1, 0.2])).not.toThrow();
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
