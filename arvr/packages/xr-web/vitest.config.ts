import { defineConfig } from "vitest/config";

// Kept apart from vite.config.ts on purpose -- see the note there.
export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
