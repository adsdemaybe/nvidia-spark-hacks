import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { fitBackground } from "./camera";

function texture(): THREE.Texture {
  return new THREE.Texture();
}

describe("fitBackground", () => {
  it("leaves a matched aspect untouched", () => {
    const t = texture();
    fitBackground(t, 16 / 9, 16 / 9);

    expect(t.repeat.x).toBeCloseTo(1, 6);
    expect(t.repeat.y).toBeCloseTo(1, 6);
    expect(t.offset.x).toBeCloseTo(0, 6);
    expect(t.offset.y).toBeCloseTo(0, 6);
  });

  it("crops vertically when the viewport is wider than the feed", () => {
    const t = texture();
    fitBackground(t, 4 / 3, 21 / 9);

    expect(t.repeat.x).toBe(1);
    expect(t.repeat.y).toBeLessThan(1);
  });

  it("crops horizontally when the viewport is taller than the feed", () => {
    // A phone held upright against a landscape webcam feed.
    const t = texture();
    fitBackground(t, 16 / 9, 9 / 16);

    expect(t.repeat.y).toBe(1);
    expect(t.repeat.x).toBeLessThan(1);
  });

  it("keeps the crop centred, so the middle of the room stays in view", () => {
    const t = texture();
    fitBackground(t, 4 / 3, 21 / 9);

    expect(t.offset.y).toBeCloseTo((1 - t.repeat.y) / 2, 12);
  });

  it("never scales up past the frame, which would show empty edges", () => {
    for (const [feed, viewport] of [
      [16 / 9, 4 / 3],
      [1, 3],
      [3, 1],
    ] as const) {
      const t = texture();
      fitBackground(t, feed, viewport);
      expect(t.repeat.x).toBeLessThanOrEqual(1 + 1e-9);
      expect(t.repeat.y).toBeLessThanOrEqual(1 + 1e-9);
    }
  });
});
