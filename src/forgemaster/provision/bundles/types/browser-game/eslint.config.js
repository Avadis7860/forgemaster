// eslint.config.js — flat config (ESLint 9) : 1er maillon du gate Tier-0 `eslint → tsc → vitest`. Base JS
// recommandée + TypeScript recommandé (non type-checked : rapide, sans service de projet) + règles des Hooks
// React. `no-undef` est OFF sur le TS (le compilateur gère déjà `document`/`process`/`setInterval`) ; les
// `.d.ts` sont ignorés (triple-slash de Vite légitime). Étends les règles au fil du projet ; garde le seed
// lint-clean — un gate qui ment (déclaré mais absent) est pire qu'un gate modeste honnête.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "**/*.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "no-undef": "off",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
);
