import { describe, expect, it } from "vitest";
import { applyAlignment, solveAlignment, type Alignment, type Anchor } from "./alignment";
import { webxrToStruct } from "./adapter";
import type { HandFrame } from "./hands";
import {
  ANCHOR_ERROR_LIMIT_M,
  CALIBRATION_ANCHORS,
  XrCalibration,
  anchorSeparationM,
  composeQuaternions,
  invertAlignment,
  yawQuaternion,
  type CalibrationAnchor,
} from "./xrCalibration";
import type { Quat, Vec3 } from "./contracts";

const KNOWN: Alignment = { yaw: Math.PI / 5, translation: [1.2, -0.4, 0.75] };

function handAt(position: Vec3, orientation: Quat = [0, 0, 0, 1]): HandFrame {
  return {
    handedness: "right",
    joints: {
      wrist: { position, orientation, radius: null },
      "index-finger-tip": { position, orientation, radius: null },
    },
    pinchApertureM: 0.02,
    gripper: 0.9,
  };
}

describe("invertAlignment", () => {
  it("round-trips any point back to itself", () => {
    const point: Vec3 = [0.37, -0.12, 0.55];
    const there = applyAlignment(KNOWN, point);
    const back = applyAlignment(invertAlignment(KNOWN), there);

    expect(back[0]).toBeCloseTo(point[0], 9);
    expect(back[1]).toBeCloseTo(point[1], 9);
    expect(back[2]).toBeCloseTo(point[2], 9);
  });

  it("negates the yaw", () => {
    expect(invertAlignment(KNOWN).yaw).toBeCloseTo(-KNOWN.yaw, 9);
  });

  it("is its own inverse", () => {
    const twice = invertAlignment(invertAlignment(KNOWN));
    expect(twice.yaw).toBeCloseTo(KNOWN.yaw, 9);
    expect(twice.translation[0]).toBeCloseTo(KNOWN.translation[0], 9);
    expect(twice.translation[1]).toBeCloseTo(KNOWN.translation[1], 9);
    expect(twice.translation[2]).toBeCloseTo(KNOWN.translation[2], 9);
  });
});

describe("yawQuaternion / composeQuaternions", () => {
  it("yawQuaternion(0) is identity", () => {
    expect(yawQuaternion(0)).toEqual([0, 0, 0, 1]);
  });

  it("composing a quaternion with identity leaves it unchanged", () => {
    const q: Quat = [0.1, 0.2, 0.3, Math.sqrt(1 - 0.14)];
    const composed = composeQuaternions([0, 0, 0, 1], q);
    for (let i = 0; i < 4; i += 1) expect(composed[i]).toBeCloseTo(q[i]!, 9);
  });

  it("composing a yaw with its negation cancels", () => {
    const composed = composeQuaternions(yawQuaternion(0.7), yawQuaternion(-0.7));
    expect(composed[0]).toBeCloseTo(0, 9);
    expect(composed[1]).toBeCloseTo(0, 9);
    expect(composed[2]).toBeCloseTo(0, 9);
    expect(Math.abs(composed[3])).toBeCloseTo(1, 9);
  });

  it("yaw quaternions add their angles", () => {
    const composed = composeQuaternions(yawQuaternion(0.3), yawQuaternion(0.4));
    const expected = yawQuaternion(0.7);
    for (let i = 0; i < 4; i += 1) expect(composed[i]).toBeCloseTo(expected[i]!, 9);
  });
});

describe("XrCalibration anchor capture", () => {
  it("starts uncalibrated with no anchors", () => {
    const cal = new XrCalibration();
    expect(cal.isCalibrated).toBe(false);
    expect(cal.anchorsCaptured).toBe(0);
    expect(cal.nextAnchor).toEqual(CALIBRATION_ANCHORS[0]);
  });

  it("stays uncalibrated after only the first anchor", () => {
    const cal = new XrCalibration();
    cal.captureAnchor([0, 0, 0]);
    expect(cal.anchorsCaptured).toBe(1);
    expect(cal.isCalibrated).toBe(false);
    expect(cal.nextAnchor).toEqual(CALIBRATION_ANCHORS[1]);
  });

  it("calibrates once both anchors are captured at the right separation", () => {
    // Pinch points laid out in the room by pushing each anchor's struct
    // position through a known room placement -- a physically consistent
    // layout, so the solve has to reproduce it exactly.
    const cal = new XrCalibration();
    for (const anchor of CALIBRATION_ANCHORS) {
      cal.captureAnchor(structPointToWebxrRoom(anchor.structPosition, KNOWN));
    }

    expect(cal.isCalibrated).toBe(true);
    expect(cal.anchorErrorM).toBeLessThan(1e-6);
    expect(cal.nextAnchor).toBeUndefined();
  });

  it("rejects a layout whose anchor separation disagrees with the robot's real scale", () => {
    const cal = new XrCalibration();
    const [a, b] = CALIBRATION_ANCHORS;
    const roomA = structPointToWebxrRoom(a!.structPosition, KNOWN);
    // Same direction, but twice as far apart as the robot's own geometry --
    // there is no scale correction, so this must surface as real error.
    const roomB = structPointToWebxrRoom(b!.structPosition, KNOWN);
    const stretched: Vec3 = [
      roomA[0] + (roomB[0] - roomA[0]) * 2,
      roomA[1] + (roomB[1] - roomA[1]) * 2,
      roomA[2] + (roomB[2] - roomA[2]) * 2,
    ];
    cal.captureAnchor(roomA);
    cal.captureAnchor(stretched);

    expect(cal.anchorErrorM).toBeGreaterThan(ANCHOR_ERROR_LIMIT_M);
    expect(cal.isCalibrated).toBe(false);
  });

  it("reset drops every captured anchor", () => {
    const cal = new XrCalibration();
    for (const anchor of CALIBRATION_ANCHORS) {
      cal.captureAnchor(structPointToWebxrRoom(anchor.structPosition, KNOWN));
    }
    cal.reset();

    expect(cal.isCalibrated).toBe(false);
    expect(cal.anchorsCaptured).toBe(0);
    expect(cal.anchorErrorM).toBeUndefined();
  });
});

describe("XrCalibration with scene-specific anchors", () => {
  const CUSTOM: CalibrationAnchor[] = [
    { structPosition: [0, 0, 0], label: "BASE", prompt: "base" },
    { structPosition: [0.28, 0.17, 0.14], label: "RED BASKET", prompt: "red basket" },
  ];

  it("prompts for the anchors it was given, not the default ones", () => {
    const cal = new XrCalibration(CUSTOM);
    expect(cal.nextAnchor?.label).toBe("BASE");
    cal.captureAnchor([0, 0, 0]);
    expect(cal.nextAnchor?.label).toBe("RED BASKET");
  });

  it("solves against the custom anchors", () => {
    const cal = new XrCalibration(CUSTOM);
    for (const anchor of CUSTOM) {
      cal.captureAnchor(structPointToWebxrRoom(anchor.structPosition, KNOWN));
    }

    expect(cal.isCalibrated).toBe(true);
    expect(cal.anchorErrorM).toBeLessThan(1e-6);
  });

  it("reports the custom pair's real separation", () => {
    // A scene that reused the button task's 38cm would tell the human to
    // place two points at the wrong distance and then reject them for it.
    expect(anchorSeparationM(CUSTOM)).toBeCloseTo(Math.hypot(0.28, 0.17, 0.14), 9);
    expect(anchorSeparationM(CUSTOM)).not.toBeCloseTo(anchorSeparationM(), 3);
  });

  it("refuses a set that is not exactly two anchors", () => {
    expect(() => new XrCalibration([CUSTOM[0]!])).toThrow(/exactly two/);
    expect(() => new XrCalibration([...CUSTOM, CUSTOM[0]!])).toThrow(/exactly two/);
  });
});

describe("XrCalibration hand mapping", () => {
  function calibrated(): XrCalibration {
    const cal = new XrCalibration();
    for (const anchor of CALIBRATION_ANCHORS) {
      cal.captureAnchor(structPointToWebxrRoom(anchor.structPosition, KNOWN));
    }
    return cal;
  }

  it("maps a hand held at the robot base back onto the robot base in struct_world", () => {
    const cal = calibrated();
    const base = CALIBRATION_ANCHORS[0]!.structPosition;
    const hand = handAt(structPointToWebxrRoom(base, KNOWN));

    const mapped = cal.handToStruct(hand)!;

    expect(mapped.joints["wrist"]!.position_m[0]).toBeCloseTo(base[0], 6);
    expect(mapped.joints["wrist"]!.position_m[1]).toBeCloseTo(base[1], 6);
    expect(mapped.joints["wrist"]!.position_m[2]).toBeCloseTo(base[2], 6);
  });

  it("maps a hand held at the asset back onto the asset in struct_world", () => {
    const cal = calibrated();
    const asset = CALIBRATION_ANCHORS[1]!.structPosition;
    const hand = handAt(structPointToWebxrRoom(asset, KNOWN));

    const mapped = cal.handToStruct(hand)!;

    expect(mapped.joints["wrist"]!.position_m[0]).toBeCloseTo(asset[0], 6);
    expect(mapped.joints["wrist"]!.position_m[1]).toBeCloseTo(asset[1], 6);
    expect(mapped.joints["wrist"]!.position_m[2]).toBeCloseTo(asset[2], 6);
  });

  it("preserves metric scale -- a 10cm hand move is a 10cm move in struct_world", () => {
    const cal = calibrated();
    const base = CALIBRATION_ANCHORS[0]!.structPosition;
    const from = structPointToWebxrRoom(base, KNOWN);
    const to: Vec3 = [from[0] + 0.06, from[1] + 0.08, from[2]];

    const a = cal.handToStruct(handAt(from))!.joints["wrist"]!.position_m;
    const b = cal.handToStruct(handAt(to))!.joints["wrist"]!.position_m;
    const moved = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);

    expect(moved).toBeCloseTo(0.1, 6);
  });

  it("returns a normalized quaternion after the yaw is applied", () => {
    const cal = calibrated();
    const hand = handAt([0.2, 1.1, -0.4], [0, 0.3826834, 0, 0.9238795]);

    const q = cal.handToStruct(hand)!.joints["wrist"]!.orientation_xyzw;

    expect(Math.hypot(...q)).toBeCloseTo(1, 9);
  });

  it("returns null for an uncalibrated session rather than silently passing raw device coords", () => {
    const cal = new XrCalibration();
    expect(cal.handToStruct(handAt([0, 1, 0]))).toBeNull();
  });

  it("carries the wire shape's provenance fields through", () => {
    const cal = calibrated();
    const mapped = cal.handToStruct(handAt([0, 1, 0]), 42, "openxr")!;

    expect(mapped.schema_version).toBe("1.0");
    expect(mapped.timestamp_ns).toBe(42);
    expect(mapped.source_device).toBe("openxr");
    expect(mapped.frame).toBe("struct_world");
    expect(mapped.hand).toBe("right");
  });
});

describe("XrCalibration scene placement", () => {
  it("places the scene root where the human actually pinched the robot base", () => {
    const cal = new XrCalibration();
    for (const anchor of CALIBRATION_ANCHORS) {
      cal.captureAnchor(structPointToWebxrRoom(anchor.structPosition, KNOWN));
    }

    const placement = cal.scenePlacement()!;
    const base = CALIBRATION_ANCHORS[0]!.structPosition;
    // The scene root's own transform applied to the robot base's WebXR-space
    // position must land on the pinch the human actually made for it.
    const expected = structPointToWebxrRoom(base, KNOWN);
    const rendered = applyPlacement(placement, structToWebxrPoint(base));

    expect(rendered[0]).toBeCloseTo(expected[0], 6);
    expect(rendered[1]).toBeCloseTo(expected[1], 6);
    expect(rendered[2]).toBeCloseTo(expected[2], 6);
  });

  it("has no placement before calibration", () => {
    expect(new XrCalibration().scenePlacement()).toBeUndefined();
  });
});

// ------------------------------------------------------------- test helpers --

/** struct_world point -> the WebXR-space point a human would pinch for it,
 * given a known room placement. The inverse of what XrCalibration solves. */
function structPointToWebxrRoom(point: Vec3, placement: Alignment): Vec3 {
  return structToWebxrPoint(applyAlignment(placement, point));
}

function structToWebxrPoint([x, y, z]: Vec3): Vec3 {
  return [-y, z, -x];
}

/** What three.js does with the scene root: rotate about WebXR +Y, then translate. */
function applyPlacement(
  placement: { yaw: number; position: Vec3 },
  point: Vec3,
): Vec3 {
  const cos = Math.cos(placement.yaw);
  const sin = Math.sin(placement.yaw);
  return [
    cos * point[0] + sin * point[2] + placement.position[0],
    point[1] + placement.position[1],
    -sin * point[0] + cos * point[2] + placement.position[2],
  ];
}

describe("solveAlignment is still the one solver in use", () => {
  it("XrCalibration's result matches a direct solveAlignment call", () => {
    const cal = new XrCalibration();
    const rooms = CALIBRATION_ANCHORS.map((a) =>
      structPointToWebxrRoom(a.structPosition, KNOWN),
    );
    for (const room of rooms) cal.captureAnchor(room);

    const anchors: Anchor[] = CALIBRATION_ANCHORS.map((a, i) => ({
      structPosition: a.structPosition,
      tappedPosition: webxrToStruct.position(rooms[i]!),
    }));
    const direct = solveAlignment(anchors[0]!, anchors[1]!);

    expect(cal.alignment!.yaw).toBeCloseTo(direct.yaw, 9);
  });
});
