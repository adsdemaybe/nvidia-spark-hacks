import { describe, expect, it } from "vitest";
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
    const wire = toWireHandFrame(hand, 42) as {
      hand: string;
      frame: string;
      joints: { wrist: { position_m: [number, number, number] } };
    };
    expect(wire.hand).toBe("right");
    expect(wire.frame).toBe("struct_world");
    // WebXR (0,0,-1) -> struct_world +X (forward), the inverse of
    // mockHand.test.ts's forward-direction check.
    expect(wire.joints.wrist.position_m).toEqual([1, 0, 0]);
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
    const wire = toWireHandFrame(hand, 0) as { joints: Record<string, unknown> };
    expect(Object.keys(wire.joints).sort()).toEqual(["thumb-tip", "wrist"]);
  });
});
