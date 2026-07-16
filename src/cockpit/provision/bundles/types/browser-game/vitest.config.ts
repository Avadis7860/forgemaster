import { defineConfig } from "vitest/config";

// Vitest : tests unitaires PURS (Node, aucun DOM au départ — la résolution se teste sans UI). Bascule
// `environment` à "jsdom" quand tu testeras des composants React (É6).
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
