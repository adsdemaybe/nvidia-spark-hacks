/**
 * Capability probe (see probe.html).
 *
 * Exists because the headset on hand is a Quest 1, not the Quest 3 the plan
 * recommends, and its browser is frozen at an old version. Rather than assert
 * what it supports, load this on the device and read the answer. Whether
 * `immersive-ar` is available decides whether F5's passthrough anchoring is
 * achievable on this hardware at all.
 */

import { detectCapabilities } from "./xr";

const reportEl = document.querySelector("#report tbody")!;
const logEl = document.getElementById("log")!;

function row(label: string, value: string, cls = ""): void {
  const tr = document.createElement("tr");
  const k = document.createElement("td");
  k.textContent = label;
  const v = document.createElement("td");
  v.textContent = value;
  if (cls) v.className = cls;
  tr.append(k, v);
  reportEl.appendChild(tr);
}

function log(message: string): void {
  logEl.textContent = `${logEl.textContent === "session log will appear here" ? "" : logEl.textContent + "\n"}${message}`;
}

async function main(): Promise<void> {
  reportEl.replaceChildren();

  row("user agent", navigator.userAgent);

  const caps = await detectCapabilities();
  row("navigator.xr", caps.webxr ? "present" : "MISSING", caps.webxr ? "yes" : "no");
  row("immersive-ar (passthrough)", caps.ar ? "supported" : "NOT supported", caps.ar ? "yes" : "no");
  row("immersive-vr", caps.vr ? "supported" : "NOT supported", caps.vr ? "yes" : "no");
  row("best available", caps.best, caps.best === "immersive-ar" ? "yes" : "warn");

  for (const note of caps.notes) row("note", note, "warn");

  if (!caps.ar) {
    row(
      "consequence",
      "F5 passthrough anchoring (master plan §9) cannot be demonstrated on this device. " +
        "A Quest 3 is required for the AR-first path.",
      "warn",
    );
  }
}

async function trySession(mode: XRSessionMode): Promise<void> {
  log(`requesting ${mode}…`);
  try {
    const session = await navigator.xr!.requestSession(mode, {
      requiredFeatures: ["local-floor"],
      optionalFeatures: ["hand-tracking", "hit-test", "anchors", "plane-detection"],
    });
    const granted =
      (session as XRSession & { enabledFeatures?: string[] }).enabledFeatures ?? [];
    log(`  granted: ${granted.length ? granted.join(", ") : "(runtime did not report enabledFeatures)"}`);
    log(`  input sources: ${session.inputSources.length}`);
    for (const source of session.inputSources) {
      log(`    ${source.handedness} target=${source.targetRayMode} hand=${source.hand ? "yes" : "no"}`);
    }
    log("  ending session in 3s…");
    setTimeout(() => void session.end().then(() => log("  ended cleanly")), 3000);
  } catch (error) {
    log(`  FAILED: ${String(error)}`);
  }
}

document.getElementById("try-ar")!.addEventListener("click", () => void trySession("immersive-ar"));
document.getElementById("try-vr")!.addEventListener("click", () => void trySession("immersive-vr"));

void main();
