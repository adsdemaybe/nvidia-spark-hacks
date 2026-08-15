import { describe, expect, it } from "vitest";
import {
  DesktopMockAdapter,
  XRControllerAdapter,
  structToWebxr,
  webxrToStruct,
} from "./adapter";
import type { Quat, Vec3 } from "./contracts";

const IDENTITY: Quat = [0, 0, 0, 1];

describe("webxrToStruct", () => {
  it("maps WebXR Y-up into STRUCT Z-up", () => {
    // WebXR: X right, Y up, -Z forward. STRUCT: X forward, Z up.
    expect(webxrToStruct.position([1, 2, 3])).toEqual([-3, -1, 2]);
  });

  it("sends WebXR up (+Y) to STRUCT up (+Z)", () => {
    expect(webxrToStruct.position([0, 1, 0])).toEqual([0, 0, 1]);
  });

  it("sends WebXR forward (-Z) to STRUCT forward (+X)", () => {
    expect(webxrToStruct.position([0, 0, -1])).toEqual([1, 0, 0]);
  });

  it("turns a yaw about WebXR up into a yaw about STRUCT up", () => {
    const yawAboutY: Quat = [0, Math.SQRT1_2, 0, Math.SQRT1_2];

    const [x, y, z, w] = webxrToStruct.quaternion(yawAboutY);

    expect(x).toBeCloseTo(0, 12);
    expect(y).toBeCloseTo(0, 12);
    expect(z).toBeCloseTo(Math.SQRT1_2, 12);
    expect(w).toBeCloseTo(Math.SQRT1_2, 12);
  });

  it("keeps quaternions unit-norm", () => {
    const q: Quat = [0.18, 0.44, 0.61, 0.63];
    expect(Math.hypot(...webxrToStruct.quaternion(q))).toBeCloseTo(Math.hypot(...q), 12);
  });

  it("round-trips back to where it started", () => {
    const p: Vec3 = [0.31, 0.18, 0.42];
    const back = structToWebxr.position(webxrToStruct.position(p));
    back.forEach((v, i) => expect(v).toBeCloseTo(p[i]!, 12));
  });
});

describe("XRControllerAdapter", () => {
  it("emits a SpatialFrame in struct_world, not the device frame", () => {
    const adapter = new XRControllerAdapter();

    const frame = adapter.toSpatialFrame(
      { position: [0, 1, 0], orientation: IDENTITY, trigger: 0 },
      1700000000000000000,
    );

    expect(frame.frame).toBe("struct_world");
    expect(frame.source.device_type).toBe("xr_controller");
    expect(frame.position_m).toEqual([0, 0, 1]);
  });

  it("maps the trigger onto the gripper", () => {
    const adapter = new XRControllerAdapter();

    const open = adapter.toSpatialFrame(
      { position: [0, 0, 0], orientation: IDENTITY, trigger: 0 },
      1,
    );
    const closed = adapter.toSpatialFrame(
      { position: [0, 0, 0], orientation: IDENTITY, trigger: 1 },
      2,
    );

    expect(open.gripper).toBe(0);
    expect(closed.gripper).toBe(1);
  });
});

describe("DesktopMockAdapter", () => {
  it("produces the same contract as a real device", () => {
    // The whole point of the adapter split (ar-xr-plan.md 5): a developer with no
    // headset must exercise exactly the downstream path a headset would.
    const adapter = new DesktopMockAdapter();

    const frame = adapter.toSpatialFrame(
      { position: [0, 1, 0], orientation: IDENTITY, trigger: 1 },
      42,
    );

    expect(frame.frame).toBe("struct_world");
    expect(frame.source.device_type).toBe("desktop_mock");
    expect(frame.position_m).toEqual([0, 0, 1]);
    expect(frame.gripper).toBe(1);
    expect(frame.timestamp_ns).toBe(42);
  });
});
