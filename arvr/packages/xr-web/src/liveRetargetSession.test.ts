import { describe, expect, it } from "vitest";
import type { Alignment } from "./alignment";
import type { HandFrame } from "./hands";
import { toWireHandFrame } from "./liveRetargetSession";

describe("toWireHandFrame", () => {
  it("converts WebXR-space joints to struct_world, inverse of mockHand's conversion", () => {
    const hand: HandFrame = {
      handedness: "right",
      joints: {
        wrist: { position: [0, 0, -1], orientation: [0, 0, 0, 1], radius: null },
      },
      pinchApertureM: null,
      gripper: 0,
    };
    const wire = toWireHandFrame(hand, 42);
    expect(wire.hand).toBe("right");
    expect(wire.frame).toBe("struct_world");
    // WebXR (0,0,-1) -> struct_world +X (forward), the inverse of
    // mockHand.test.ts's forward-direction check.
    expect(wire.joints["wrist"]!.position_m).toEqual([1, 0, 0]);
  });

  it("carries every tracked joint, not a hardcoded subset", () => {
    const hand: HandFrame = {
      handedness: "left",
      joints: {
        wrist: { position: [0, 0, 0], orientation: [0, 0, 0, 1], radius: null },
        "thumb-tip": { position: [0.01, 0, 0], orientation: [0, 0, 0, 1], radius: null },
      },
      pinchApertureM: 0.02,
      gripper: 0.8,
    };
    const wire = toWireHandFrame(hand, 0);
    expect(Object.keys(wire.joints).sort()).toEqual(["thumb-tip", "wrist"]);
  });

  it("leaves coordinates untouched when no calibration is supplied", () => {
    // Every non-headset provider authors struct_world directly. Applying a
    // transform to those frames would break the three already-working paths.
    const hand = handAt([0, 0, -1]);
    expect(toWireHandFrame(hand, 0).joints["wrist"]!.position_m).toEqual([1, 0, 0]);
  });

  it("applies the workspace calibration when one is supplied", () => {
    const hand = handAt([0, 0, -1]); // struct_world [1, 0, 0]
    const roomToStruct: Alignment = { yaw: Math.PI / 2, translation: [0.5, 0, 0.25] };

    const wire = toWireHandFrame(hand, 0, "openxr", roomToStruct);

    // A quarter turn takes struct +X onto +Y, then the offset applies.
    const [x, y, z] = wire.joints["wrist"]!.position_m;
    expect(x).toBeCloseTo(0.5, 9);
    expect(y).toBeCloseTo(1.0, 9);
    expect(z).toBeCloseTo(0.25, 9);
  });

  it("rotates the joint orientation by the same yaw as the position", () => {
    // A pose whose position moved but whose orientation did not would tell
    // the retargeter the hand is somewhere it is not facing.
    const hand = handAt([0, 0, -1]);
    const roomToStruct: Alignment = { yaw: Math.PI / 2, translation: [0, 0, 0] };

    const q = toWireHandFrame(hand, 0, "openxr", roomToStruct).joints["wrist"]!.orientation_xyzw;

    expect(Math.hypot(...q)).toBeCloseTo(1, 9);
    expect(q[2]).toBeCloseTo(Math.sin(Math.PI / 4), 9); // yaw about struct +Z
  });
});

function handAt(position: [number, number, number]): HandFrame {
  return {
    handedness: "right",
    joints: { wrist: { position, orientation: [0, 0, 0, 1], radius: null } },
    pinchApertureM: null,
    gripper: 0,
  };
}
