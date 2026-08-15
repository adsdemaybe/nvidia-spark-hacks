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
// `dom-overlay` is what keeps the page's own HTML controls visible inside an
// AR session. It is AR-only and not universally granted, which is exactly why
// xrHud.ts draws an in-scene fallback: the flow has to be drivable from inside
// the headset whether or not this feature shows up.
const AR_OPTIONAL = ["hand-tracking", "dom-overlay", "hit-test", "anchors", "plane-detection"];
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
  /** True when the page's DOM is composited over the session. */
  domOverlay: boolean;
  /** Everything the runtime reported granting, verbatim. Empty when the
   * runtime does not implement `enabledFeatures` at all -- which is not the
   * same as "granted nothing", so callers must not read emptiness as denial. */
  enabledFeatures: string[];
}

/**
 * Request the best available immersive session.
 *
 * Optional features are requested optionally on purpose: a runtime that does
 * not know `hand-tracking` must still give us a session rather than rejecting
 * the request outright.
 *
 * `domOverlayRoot` is the element to composite over an AR session, when the
 * runtime supports it. Passing one is a request, never an assumption -- read
 * the returned `domOverlay` to find out whether it was honored.
 */
export async function startBestSession(
  domOverlayRoot?: Element,
  prefer: "ar" | "vr" = "ar",
): Promise<StartedSession> {
  const xr = navigator.xr;
  if (!xr) throw new Error("WebXR unavailable");

  const caps = await detectCapabilities();
  if (caps.best === "flat") throw new Error("no immersive session available");

  // AR-first is the master plan's locked decision for the robot work, where
  // seeing the real desk the robot stands on is the point. A room-scale scene
  // with its own floor and horizon wants the opposite: passthrough would show
  // the actual room *through* a floor that is supposed to be under your feet,
  // and on a Quest 2 that passthrough is low-resolution greyscale anyway.
  // So the caller says which it wants, and this still degrades to whatever is
  // actually available rather than failing.
  const kind: Exclude<SessionKind, "flat"> =
    prefer === "vr" ? (caps.vr ? "immersive-vr" : "immersive-ar") : caps.best;
  const init: XRSessionInit = {
    requiredFeatures: kind === "immersive-ar" ? AR_REQUIRED : VR_REQUIRED,
    optionalFeatures: kind === "immersive-ar" ? AR_OPTIONAL : VR_OPTIONAL,
  };
  if (kind === "immersive-ar" && domOverlayRoot) {
    (init as XRSessionInit & { domOverlay?: { root: Element } }).domOverlay = {
      root: domOverlayRoot,
    };
  }
  const session = await xr.requestSession(kind, init);

  const enabledFeatures =
    (session as XRSession & { enabledFeatures?: string[] }).enabledFeatures ?? [];

  return {
    session,
    kind,
    handTracking: enabledFeatures.includes("hand-tracking"),
    domOverlay: enabledFeatures.includes("dom-overlay"),
    enabledFeatures: [...enabledFeatures],
  };
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

/**
 * Whether the renderer should clear to transparent for this session.
 *
 * An opaque scene background in `immersive-ar` paints over the passthrough
 * feed, so the human gets a grey void where their room should be -- the
 * headset is compositing correctly and the app is hiding the result. The
 * session kind, not the device, decides: a Quest that falls back to
 * `immersive-vr` has no camera feed to reveal and does want its background.
 */
export function wantsTransparentBackground(kind: SessionKind): boolean {
  return kind === "immersive-ar";
}
