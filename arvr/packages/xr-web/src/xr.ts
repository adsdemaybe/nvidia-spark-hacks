/**
 * WebXR session capability + entry (master plan §0, §8).
 *
 * The locked decision is AR-first: `immersive-ar` passthrough is the primary
 * mode for both F4 and F5, `immersive-vr` is secondary, and "the same app runs
 * flat on desktop for development".
 *
 * Everything here is feature-detected rather than assumed, because the headset
 * actually on hand is a Quest 1, not the Quest 3 the plan recommends. Quest 1
 * passthrough is low-resolution greyscale and its browser is frozen
 * (discontinued), so whether it exposes `immersive-ar` at all is an open
 * question this code answers at runtime instead of guessing. If AR is
 * unavailable the app degrades to VR, then to flat — never to a broken page.
 *
 * `probe.html` prints the same report for a human.
 */

export type SessionKind = "immersive-ar" | "immersive-vr" | "flat";

export interface XrCapabilities {
  /** navigator.xr exists at all. */
  webxr: boolean;
  ar: boolean;
  vr: boolean;
  /** Optional features the runtime accepted, discovered by trial request. */
  handTracking: boolean;
  /** Best session we can actually start. */
  best: SessionKind;
  notes: string[];
}

const AR_REQUIRED = ["local-floor"];
const AR_OPTIONAL = ["hand-tracking", "hit-test", "anchors", "plane-detection"];
const VR_REQUIRED = ["local-floor"];
const VR_OPTIONAL = ["hand-tracking"];

export async function detectCapabilities(): Promise<XrCapabilities> {
  const notes: string[] = [];
  const xr = navigator.xr;

  if (!xr) {
    return {
      webxr: false, ar: false, vr: false, handTracking: false, best: "flat",
      notes: ["navigator.xr is undefined — no WebXR in this browser"],
    };
  }

  const ar = await supported(xr, "immersive-ar", notes);
  const vr = await supported(xr, "immersive-vr", notes);

  if (!ar && vr) {
    notes.push(
      "immersive-ar unsupported but immersive-vr is — typical of pre-Quest-2 " +
        "hardware. F5's passthrough anchoring cannot be demonstrated on this device.",
    );
  }

  return {
    webxr: true,
    ar,
    vr,
    // Only knowable once a session starts; the session reports what it granted.
    handTracking: false,
    best: ar ? "immersive-ar" : vr ? "immersive-vr" : "flat",
    notes,
  };
}

async function supported(
  xr: XRSystem,
  mode: XRSessionMode,
  notes: string[],
): Promise<boolean> {
  try {
    return await xr.isSessionSupported(mode);
  } catch (error) {
    notes.push(`isSessionSupported(${mode}) threw: ${String(error)}`);
    return false;
  }
}

export interface StartedSession {
  session: XRSession;
  kind: Exclude<SessionKind, "flat">;
  /** True when the runtime actually granted hand input. */
  handTracking: boolean;
}

/**
 * Request the best available immersive session.
 *
 * Optional features are requested optionally on purpose: a runtime that does
 * not know `hand-tracking` must still give us a session rather than rejecting
 * the request outright.
 */
export async function startBestSession(): Promise<StartedSession> {
  const xr = navigator.xr;
  if (!xr) throw new Error("WebXR unavailable");

  const caps = await detectCapabilities();
  if (caps.best === "flat") throw new Error("no immersive session available");

  const kind = caps.best;
  const session = await xr.requestSession(kind, {
    requiredFeatures: kind === "immersive-ar" ? AR_REQUIRED : VR_REQUIRED,
    optionalFeatures: kind === "immersive-ar" ? AR_OPTIONAL : VR_OPTIONAL,
  });

  const granted = (session as XRSession & { enabledFeatures?: string[] }).enabledFeatures;
  const handTracking = granted?.includes("hand-tracking") ?? false;

  return { session, kind, handTracking };
}

/** One-line summary for the HUD, honest about which world the user is seeing. */
export function describe(kind: SessionKind, handTracking: boolean): string {
  const hands = handTracking ? " + HAND TRACKING" : "";
  switch (kind) {
    case "immersive-ar":
      return `PASSTHROUGH AR${hands}`;
    case "immersive-vr":
      return `IMMERSIVE VR (NO PASSTHROUGH)${hands}`;
    default:
      return "FLAT DESKTOP — NOT AR";
  }
}
