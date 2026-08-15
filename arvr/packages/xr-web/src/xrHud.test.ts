import { describe, expect, it } from "vitest";
import { PokeTracker, layoutButtons, type PokeTarget } from "./xrHud";
import type { Vec3 } from "./contracts";

const BUTTON: PokeTarget = { id: "start", center: [0, 0, 0], halfExtents: [0.05, 0.02, 0.01] };
const OTHER: PokeTarget = { id: "finish", center: [0.2, 0, 0], halfExtents: [0.05, 0.02, 0.01] };

const inside: Vec3 = [0, 0, 0];
const outside: Vec3 = [0.5, 0.5, 0.5];

describe("PokeTracker", () => {
  it("fires once when the fingertip enters a button", () => {
    const tracker = new PokeTracker([BUTTON]);
    expect(tracker.update(inside)).toBe("start");
  });

  it("does not re-fire while the fingertip stays inside", () => {
    const tracker = new PokeTracker([BUTTON]);
    tracker.update(inside);
    expect(tracker.update(inside)).toBeNull();
    expect(tracker.update([0.01, 0, 0])).toBeNull();
  });

  it("fires again after the fingertip leaves and re-enters", () => {
    const tracker = new PokeTracker([BUTTON]);
    tracker.update(inside);
    tracker.update(outside);
    expect(tracker.update(inside)).toBe("start");
  });

  it("holds the latch through jitter smaller than the exit margin", () => {
    // A hand-tracked fingertip sitting on a button edge wobbles by
    // millimeters; without hysteresis that reads as a burst of presses.
    const tracker = new PokeTracker([BUTTON], 0.02);
    tracker.update(inside);
    const justOutside: Vec3 = [0.055, 0, 0]; // 5mm past the face, inside the margin
    expect(tracker.update(justOutside)).toBeNull();
    expect(tracker.update(inside)).toBeNull();
  });

  it("releases the latch once the fingertip clears the exit margin", () => {
    const tracker = new PokeTracker([BUTTON], 0.02);
    tracker.update(inside);
    tracker.update([0.08, 0, 0]); // 3cm past the face, beyond the margin
    expect(tracker.update(inside)).toBe("start");
  });

  it("returns null when the fingertip is nowhere near a button", () => {
    expect(new PokeTracker([BUTTON]).update(outside)).toBeNull();
  });

  it("treats a lost hand as a release rather than a stuck press", () => {
    const tracker = new PokeTracker([BUTTON]);
    tracker.update(inside);
    tracker.update(null);
    expect(tracker.hovered).toBeNull();
    expect(tracker.update(inside)).toBe("start");
  });

  it("fires each button independently", () => {
    const tracker = new PokeTracker([BUTTON, OTHER]);
    expect(tracker.update(inside)).toBe("start");
    expect(tracker.update([0.2, 0, 0])).toBe("finish");
  });

  it("reports what is hovered without firing", () => {
    const tracker = new PokeTracker([BUTTON]);
    expect(tracker.hovered).toBeNull();
    tracker.update(inside);
    expect(tracker.hovered).toBe("start");
    tracker.update(outside);
    expect(tracker.hovered).toBeNull();
  });

  it("clears the latch when the button set changes", () => {
    // Buttons are rebuilt whenever the flow advances (CALIBRATE -> START ->
    // FINISH). A latch carried across that rebuild would swallow the first
    // press of the new button.
    const tracker = new PokeTracker([BUTTON]);
    tracker.update(inside);
    tracker.setTargets([{ ...BUTTON, id: "next" }]);
    expect(tracker.update(inside)).toBe("next");
  });

  it("respects each axis of the box, not just distance", () => {
    const tracker = new PokeTracker([BUTTON]);
    // Within the wide X half-extent but well outside the shallow Z one.
    expect(tracker.update([0.04, 0, 0.2])).toBeNull();
  });
});

describe("layoutButtons", () => {
  it("centers a single button on the row", () => {
    const [placed] = layoutButtons(["only"], { width: 0.1, height: 0.04, gap: 0.02 });
    expect(placed!.center[0]).toBeCloseTo(0, 9);
  });

  it("spreads buttons symmetrically about the center", () => {
    const placed = layoutButtons(["a", "b"], { width: 0.1, height: 0.04, gap: 0.02 });
    expect(placed[0]!.center[0]).toBeCloseTo(-0.06, 9);
    expect(placed[1]!.center[0]).toBeCloseTo(0.06, 9);
  });

  it("gives every button the requested footprint", () => {
    const placed = layoutButtons(["a", "b", "c"], { width: 0.1, height: 0.04, gap: 0.02 });
    for (const button of placed) {
      expect(button.halfExtents[0]).toBeCloseTo(0.05, 9);
      expect(button.halfExtents[1]).toBeCloseTo(0.02, 9);
    }
    expect(placed).toHaveLength(3);
  });

  it("keeps the poke depth thick enough for a fingertip moving at hand-tracking framerate", () => {
    // A fingertip crossing an infinitely thin plate between two 72Hz samples
    // is simply never inside it. The button needs real depth to be pokeable.
    const [placed] = layoutButtons(["a"], { width: 0.1, height: 0.04, gap: 0.02 });
    expect(placed!.halfExtents[2]).toBeGreaterThanOrEqual(0.015);
  });
});
