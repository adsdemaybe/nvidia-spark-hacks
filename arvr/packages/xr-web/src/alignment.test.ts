import { describe, expect, it } from "vitest";
import {
  IDENTITY_ALIGNMENT,
  applyAlignment,
  reprojectionErrorM,
  solveAlignment,
  type Alignment,
  type Anchor,
} from "./alignment";

describe("solveAlignment", () => {
  it("solves pure translation when tapped anchors are shifted with no rotation", () => {
    const anchorA: Anchor = { structPosition: [0, 0, 0], tappedPosition: [2, 3, 0] };
    const anchorB: Anchor = { structPosition: [1, 0, 0], tappedPosition: [3, 3, 0] };

    const alignment = solveAlignment(anchorA, anchorB);

    expect(alignment.yaw).toBeCloseTo(0, 6);
    expect(alignment.translation[0]).toBeCloseTo(2, 6);
    expect(alignment.translation[1]).toBeCloseTo(3, 6);
  });

  it("solves a 90 degree rotation", () => {
    const anchorA: Anchor = { structPosition: [0, 0, 0], tappedPosition: [0, 0, 0] };
    // struct +X maps to tapped +Y -> 90 degree yaw.
    const anchorB: Anchor = { structPosition: [1, 0, 0], tappedPosition: [0, 1, 0] };

    const alignment = solveAlignment(anchorA, anchorB);

    expect(alignment.yaw).toBeCloseTo(Math.PI / 2, 6);
  });

  it("both anchors reproject exactly when the taps are geometrically consistent", () => {
    // Tapped positions derived from a known transform (30 degrees, offset
    // [1, 0.5, 0]) applied to the struct positions, so B is guaranteed
    // consistent with A rather than made up by hand.
    const known: Alignment = { yaw: Math.PI / 6, translation: [1, 0.5, 0] };
    const structA: [number, number, number] = [0.15, -0.7, 0];
    const structB: [number, number, number] = [0.4, 0.0, 0];
    const anchorA: Anchor = { structPosition: structA, tappedPosition: applyAlignment(known, structA) };
    const anchorB: Anchor = { structPosition: structB, tappedPosition: applyAlignment(known, structB) };

    const alignment = solveAlignment(anchorA, anchorB);

    expect(reprojectionErrorM(alignment, anchorA)).toBeLessThan(1e-6);
    expect(reprojectionErrorM(alignment, anchorB)).toBeLessThan(1e-6);
  });

  it("reports real reprojection error when the tapped distance disagrees with struct_world", () => {
    const anchorA: Anchor = { structPosition: [0, 0, 0], tappedPosition: [0, 0, 0] };
    // struct distance A->B is 1m; tapped distance is 2m -- inconsistent (no
    // scale correction by design), so B must not reproject exactly.
    const anchorB: Anchor = { structPosition: [1, 0, 0], tappedPosition: [2, 0, 0] };

    const alignment = solveAlignment(anchorA, anchorB);

    expect(reprojectionErrorM(alignment, anchorB)).toBeCloseTo(1.0, 6);
  });
});

describe("applyAlignment", () => {
  it("is the identity when alignment is IDENTITY_ALIGNMENT", () => {
    const point: [number, number, number] = [1.23, -4.56, 0.78];
    expect(applyAlignment(IDENTITY_ALIGNMENT, point)).toEqual(point);
  });

  it("maps struct anchors to their tapped positions after solving", () => {
    const known: Alignment = { yaw: Math.PI / 6, translation: [1, 0.5, 0] };
    const structA: [number, number, number] = [0.15, -0.7, 0];
    const structB: [number, number, number] = [0.4, 0.0, 0];
    const anchorA: Anchor = { structPosition: structA, tappedPosition: applyAlignment(known, structA) };
    const anchorB: Anchor = { structPosition: structB, tappedPosition: applyAlignment(known, structB) };
    const alignment = solveAlignment(anchorA, anchorB);

    const mappedA = applyAlignment(alignment, anchorA.structPosition);
    const mappedB = applyAlignment(alignment, anchorB.structPosition);

    const [ax, ay, az] = mappedA;
    const [tax, tay, taz] = anchorA.tappedPosition;
    expect(ax).toBeCloseTo(tax, 5);
    expect(ay).toBeCloseTo(tay, 5);
    expect(az).toBeCloseTo(taz, 5);

    const [bx, by, bz] = mappedB;
    const [tbx, tby, tbz] = anchorB.tappedPosition;
    expect(bx).toBeCloseTo(tbx, 5);
    expect(by).toBeCloseTo(tby, 5);
    expect(bz).toBeCloseTo(tbz, 5);
  });

  it("carries a vertical (Z) offset through unchanged aside from the anchor A offset", () => {
    const anchorA: Anchor = { structPosition: [0, 0, 0.1], tappedPosition: [0, 0, 0.9] };
    const anchorB: Anchor = { structPosition: [1, 0, 0.1], tappedPosition: [1, 0, 0.9] };
    const alignment = solveAlignment(anchorA, anchorB);

    const mapped = applyAlignment(alignment, [5, 5, 0.4]);
    expect(mapped[2]).toBeCloseTo(1.2, 6); // +0.8 offset from anchor A's Z delta
  });
});
