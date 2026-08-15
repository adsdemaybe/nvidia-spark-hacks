import { describe, expect, it } from "vitest";
import {
  SO101_ARM_DOF,
  orientationWeightForDof,
  teleopTargetFromWireFrame,
} from "./teleopTarget";
import type { WireHandFrame } from "./liveRetargetSession";

function wireFrame(joints: WireHandFrame["joints"]): WireHandFrame {
  return {
    schema_version: "1.0",
    timestamp_ns: 1234,
    source_device: "openxr",
    hand: "right",
    frame: "struct_world",
    joints,
  };
}

describe("orientationWeightForDof", () => {
  it("is zero for the SO-101, which cannot command orientation independently", () => {
    expect(orientationWeightForDof(SO101_ARM_DOF)).toBe(0);
  });

  it("is one for a 6-DOF arm", () => {
    expect(orientationWeightForDof(6)).toBe(1);
  });

  it("is zero for anything below six joints, not just five", () => {
    expect(orientationWeightForDof(4)).toBe(0);
    expect(orientationWeightForDof(3)).toBe(0);
  });
});

describe("teleopTargetFromWireFrame", () => {
  const frame = wireFrame({
    wrist: { position_m: [0.25, 0.05, 0.2], orientation_xyzw: [0, 0, 0.3827, 0.9239] },
  });

  it("takes the end-effector target from the wrist", () => {
    const target = teleopTargetFromWireFrame(frame, 0.5)!;
    expect(target.ee_position_m).toEqual([0.25, 0.05, 0.2]);
    expect(target.ee_orientation_xyzw).toEqual([0, 0, 0.3827, 0.9239]);
  });

  it("carries the frame's timestamp so the target lines up with the hand data", () => {
    expect(teleopTargetFromWireFrame(frame, 0)!.timestamp_ns).toBe(1234);
  });

  it("passes the pinch through as the gripper command", () => {
    expect(teleopTargetFromWireFrame(frame, 0.75)!.gripper).toBe(0.75);
  });

  it("clamps a gripper value outside [0,1] rather than commanding a grip the robot cannot make", () => {
    expect(teleopTargetFromWireFrame(frame, 1.4)!.gripper).toBe(1);
    expect(teleopTargetFromWireFrame(frame, -0.2)!.gripper).toBe(0);
    expect(teleopTargetFromWireFrame(frame, Number.NaN)!.gripper).toBe(0);
  });

  it("marks orientation as best-effort for the 5-DOF default", () => {
    expect(teleopTargetFromWireFrame(frame, 0)!.orientation_weight).toBe(0);
  });

  it("returns null when the wrist was not tracked", () => {
    const noWrist = wireFrame({
      "index-finger-tip": { position_m: [0, 0, 0], orientation_xyzw: [0, 0, 0, 1] },
    });
    expect(teleopTargetFromWireFrame(noWrist, 0)).toBeNull();
  });

  it("copies the wrist arrays rather than aliasing the frame", () => {
    // The recorder keeps both the hand frame and the target; sharing an
    // array would let a later mutation of one silently rewrite the other.
    const target = teleopTargetFromWireFrame(frame, 0)!;
    expect(target.ee_position_m).not.toBe(frame.joints["wrist"]!.position_m);
  });
});
