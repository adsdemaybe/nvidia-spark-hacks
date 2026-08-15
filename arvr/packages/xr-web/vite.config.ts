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

export default defineConfig({
  plugins: https ? [basicSsl()] : [],
  publicDir: "../../fixtures",
  server: { host: true, port: 5273 },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        probe: resolve(__dirname, "probe.html"),
      },
    },
  },
});
