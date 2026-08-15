import { describe as suite, expect, it } from "vitest";
import { describe, wantsTransparentBackground } from "./xr";

suite("describe", () => {
  it("never calls a VR fallback session AR", () => {
    // The whole point of the AR -> VR degrade is that the human is told which
    // one they got. Labeling a VR session "AR" would make a demo look like
    // passthrough anchoring when there is no camera feed at all.
    expect(describe("immersive-vr", true)).toContain("NO PASSTHROUGH");
    expect(describe("immersive-vr", true)).not.toContain("PASSTHROUGH AR");
  });

  it("reports hand tracking only when the runtime granted it", () => {
    expect(describe("immersive-ar", true)).toContain("HAND TRACKING");
    expect(describe("immersive-ar", false)).not.toContain("HAND TRACKING");
  });

  it("says plainly when the session is not immersive at all", () => {
    expect(describe("flat", false)).toContain("NOT AR");
  });
});

suite("wantsTransparentBackground", () => {
  it("clears to transparent for passthrough AR", () => {
    expect(wantsTransparentBackground("immersive-ar")).toBe(true);
  });

  it("keeps the background in VR, which has no camera feed to reveal", () => {
    expect(wantsTransparentBackground("immersive-vr")).toBe(false);
  });

  it("keeps the background on a flat desktop", () => {
    expect(wantsTransparentBackground("flat")).toBe(false);
  });
});
