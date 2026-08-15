import tseslint from "typescript-eslint"
import pcb from "./lint/pcb-plugin.mjs"

export default [
  {
    // HDL: electronics rules on every board file, wherever it lives.
    files: ["examples/**/*.tsx", "runs/**/*.tsx", "boards/**/*.tsx"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { pcb },
    rules: {
      "pcb/known-elements": "error",
      "pcb/trace-selectors": "error",
      "pcb/decoupling-length": "error",
      "pcb/crystal-length": "error",
      "pcb/no-pcb-rotation": "error",
      "pcb/unit-strings": "error",
      "pcb/unique-names": "error",
      "pcb/chip-pin-attributes": "warn",
    },
  },
  {
    // Pipeline source: ordinary TypeScript hygiene.
    files: ["src/**/*.ts"],
    languageOptions: { parser: tseslint.parser },
    plugins: { "@typescript-eslint": tseslint.plugin },
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "no-fallthrough": "error",
      eqeqeq: ["error", "smart"],
    },
  },
]
