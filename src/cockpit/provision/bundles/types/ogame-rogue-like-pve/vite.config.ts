import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite : racine = web/ (client React). Le dev-server proxifie /api vers le serveur Hono (port 8787) ; le build
// sort dans dist/. Univers TypeScript unifié : web/ et server/ partagent src/shared (hors racine web/).
// Tailwind v4 est branché via son plugin Vite (zéro-config : scan du contenu automatique).
export default defineConfig({
  root: "web",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8787",
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
