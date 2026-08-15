import { defineConfig } from "vitest/config";

// The fixture pack (STRUCT_2.md 57) is served straight off disk so the client
// develops against exactly the bytes the Python tests validate -- no copies to
// drift out of sync.
export default defineConfig({
  publicDir: "../../fixtures",
  server: { port: 5273 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
