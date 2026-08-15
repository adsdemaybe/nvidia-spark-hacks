import { describe, expect, it } from "vitest";
import { GRASP_RADIUS_M, GraspController, type GraspCandidate } from "./grasp";
import type { Quat, Vec3 } from "./contracts";

const IDENTITY: Quat = [0, 0, 0, 1];
/** A quarter turn about struct +Z. */
const YAW_90: Quat = [0, 0, Math.SQRT1_2, Math.SQRT1_2];

const balls: GraspCandidate[] = [
  { id: "red_ball_0", position: [0.2, 0.05, 0.17] },
  { id: "blue_ball_0", position: [0.2, -0.05, 0.17] },
];

function controller(): GraspController {
  return new GraspController();
}

describe("GraspController acquisition", () => {
  it("grabs the ball under a pinching hand", () => {
    const grasp = controller();
    const result = grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(result.heldId).toBe("red_ball_0");
    expect(result.grasped).toBe("red_ball_0");
  });

  it("grabs nothing when the hand is open", () => {
    const grasp = controller();
    const result = grasp.update({
      pinchActive: false,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(result.heldId).toBeNull();
    expect(result.grasped).toBeUndefined();
  });

  it("grabs nothing when the nearest ball is out of range", () => {
    const grasp = controller();
    const far: Vec3 = [0.2, 0.05 + GRASP_RADIUS_M * 3, 0.17];
    const result = grasp.update({
      pinchActive: true,
      pinchCenter: far,
      handOrientation: IDENTITY,
      balls,
    });

    expect(result.heldId).toBeNull();
  });

  it("grabs the nearer of two candidates", () => {
    const grasp = controller();
    const result = grasp.update({
      pinchActive: true,
      // Slightly closer to the blue ball.
      pinchCenter: [0.2, -0.04, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(result.heldId).toBe("blue_ball_0");
  });

  it("holds only one ball at a time", () => {
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });
    const second = grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, -0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(second.heldId).toBe("red_ball_0");
    expect(second.grasped).toBeUndefined();
  });
});

describe("GraspController carrying", () => {
  it("carries the ball with the hand", () => {
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    const moved = grasp.update({
      pinchActive: true,
      pinchCenter: [0.28, 0.17, 0.25],
      handOrientation: IDENTITY,
      balls,
    });

    expect(moved.ballPosition![0]).toBeCloseTo(0.28, 9);
    expect(moved.ballPosition![1]).toBeCloseTo(0.17, 9);
    expect(moved.ballPosition![2]).toBeCloseTo(0.25, 9);
  });

  it("preserves the offset the ball was grabbed at", () => {
    // Pinching 2cm to the side of a ball's center should not teleport the
    // ball into the fingers -- it should hang where it was caught.
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.03, 0.17], // 2cm from red_ball_0's center
      handOrientation: IDENTITY,
      balls,
    });

    const moved = grasp.update({
      pinchActive: true,
      pinchCenter: [0.3, 0.03, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(moved.ballPosition![0]).toBeCloseTo(0.3, 9);
    expect(moved.ballPosition![1]).toBeCloseTo(0.05, 9); // offset carried
  });

  it("rotates the grab offset with the wrist", () => {
    // The offset is stored in the hand's own frame, so turning the wrist
    // swings the ball around the pinch rather than leaving it behind in
    // world space.
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.03, 0.17], // offset (0, +0.02, 0) in world
      handOrientation: IDENTITY,
      balls,
    });

    const turned = grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.03, 0.17],
      handOrientation: YAW_90,
      balls,
    });

    // +Y offset rotated 90 degrees about +Z becomes -X.
    expect(turned.ballPosition![0]).toBeCloseTo(0.18, 6);
    expect(turned.ballPosition![1]).toBeCloseTo(0.03, 6);
  });
});

describe("GraspController release", () => {
  it("releases when the hand opens", () => {
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    const released = grasp.update({
      pinchActive: false,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(released.heldId).toBeNull();
    expect(released.released).toBe("red_ball_0");
  });

  it("releases when hand tracking is lost, rather than carrying a ball forever", () => {
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    const lost = grasp.update({
      pinchActive: true,
      pinchCenter: null,
      handOrientation: IDENTITY,
      balls,
    });

    expect(lost.heldId).toBeNull();
    expect(lost.released).toBe("red_ball_0");
  });

  it("reports release exactly once", () => {
    const grasp = controller();
    grasp.update({
      pinchActive: true,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });
    grasp.update({
      pinchActive: false,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });
    const after = grasp.update({
      pinchActive: false,
      pinchCenter: [0.2, 0.05, 0.17],
      handOrientation: IDENTITY,
      balls,
    });

    expect(after.released).toBeUndefined();
  });

  it("can pick a ball up again after releasing it", () => {
    const grasp = controller();
    const at: Vec3 = [0.2, 0.05, 0.17];
    grasp.update({ pinchActive: true, pinchCenter: at, handOrientation: IDENTITY, balls });
    grasp.update({ pinchActive: false, pinchCenter: at, handOrientation: IDENTITY, balls });
    const again = grasp.update({
      pinchActive: true,
      pinchCenter: at,
      handOrientation: IDENTITY,
      balls,
    });

    expect(again.grasped).toBe("red_ball_0");
  });
});
