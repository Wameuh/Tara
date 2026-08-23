import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    testTimeout: 10_000,
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
