import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/__tests__/**/*.test.ts"],
    environment: "node",
    coverage: {
      // CI gate (#30): the shared package is the high-leverage code
      // every host imports, so the issue specifies a 80% line floor.
      // Provider v8 ships with vitest and produces both summary + lcov.
      provider: "v8",
      reporter: ["text", "json", "lcov"],
      include: ["src/**/*.ts"],
      exclude: ["src/**/__tests__/**", "src/index.ts"],
      thresholds: {
        lines: 80,
      },
    },
  },
});
