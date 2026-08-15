import { resolve } from "node:path";
import basicSsl from "@vitejs/plugin-basic-ssl";
import { defineConfig } from "vite";

// WebXR requires a secure context. localhost is exempt, but a headset reaching
// this over the LAN is not -- so the dev server runs HTTPS with a self-signed
// cert and `host: true` binds all interfaces. On the headset, accept the
// certificate warning once. (setup_spark.sh already installs mkcert if you
// would rather issue a trusted cert.)
//
// Vitest config lives in vitest.config.ts: vitest bundles its own copy of vite,
// and importing defineConfig from "vitest/config" here makes the two vite
// versions' Plugin types collide under exactOptionalPropertyTypes.
// VITE_NO_SSL=1 drops the self-signed cert. http://localhost is already a
// secure context, so getUserMedia and WebXR still work there -- this exists so
// headless browsers and CI can drive the app without a cert exception. Never
// use it for the LAN address a headset connects to.
const https = !process.env["VITE_NO_SSL"];

// Where ar_backend is actually running. 127.0.0.1 on purpose: this is the
// dev server reaching it on the same machine, not the headset reaching it
// across a network -- the headset only ever talks to this server.
const BACKEND = process.env["STRUCT_API_BASE"] ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: https ? [basicSsl()] : [],
  publicDir: "../../fixtures",
  server: {
    host: true,
    port: 5273,
    // Vite 6 rejects requests whose Host header it does not recognise, which
    // is what a tunnel (cloudflared, ngrok) always presents. A headset that
    // cannot reach this machine on the LAN -- client-isolated hotspot,
    // locked-down campus wifi -- reaches it through a tunnel instead, and
    // that is the one path that needs no firewall rule, no Developer Mode
    // and no certificate exception. Dev server only; `vite build` output is
    // unaffected.
    allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ".ngrok.io", ".loca.lt"],
    // Proxy ar_backend through the dev server so the page and the API share
    // one origin.
    //
    // Without this, a headset on the LAN loads the page over https (WebXR
    // requires a secure context) and then calls http://<lan-ip>:8000 -- which
    // every browser blocks as mixed content, and blocks ws:// from an https
    // page for the same reason. The symptom is a demo where hand tracking
    // works, the balls move, and the robot never does, with the failure only
    // visible in a console nobody can open inside a headset.
    //
    // Same-origin also means the CORS middleware stops being load-bearing for
    // this path, and there is no second TLS certificate to issue for the API.
    proxy: Object.fromEntries(
      // Every ar_backend router prefix. `ws: true` matters for /spatial (the
      // live retarget stream) and /twin.
      //
      // Anchored to a path boundary, not a bare prefix. Vite matches proxy
      // keys as prefixes, so a plain "/spatial" also swallows
      // "/spatial-training/..." -- which is where the fixture robot meshes and
      // assets live. That sent every fixture request to the API, which
      // correctly 404'd it, and the robot silently failed to load with the
      // only clue being a 404 for a file that plainly exists on disk.
      ["robots", "assets", "scenes", "spatial", "twin", "xr"].map((name) => [
        `^/${name}(/|$)`,
        { target: BACKEND, changeOrigin: true, ws: true, secure: false },
      ]),
    ),
  },
  build: {
    rollupOptions: {
      input: {
        // One app: the can data-collection page, plus the headset capability
        // probe (the only way to diagnose a Quest, and a few lines).
        canPickup: resolve(__dirname, "can-pickup.html"),
        probe: resolve(__dirname, "probe.html"),
      },
    },
  },
});
