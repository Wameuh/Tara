import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default [
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ["src/**/*.{ts,tsx}"], languageOptions: { globals: globals.browser } },
  {
    files: ["e2e/**/*.ts", "playwright.config.ts"],
    languageOptions: { globals: globals.node },
  },
  {
    ignores: [
      "dist",
      "playwright-report",
      "test-results",
      "src/api/generated.ts",
    ],
  },
];
