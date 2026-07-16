// main.tsx — point de montage du client React (Vite). Le client est une VUE + des commandes : aucune logique
// de jeu ici (anti-triche, décision verrouillée 2). Étends l'UI en É6 (panneaux ressources/map/flotte).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App.js";

const root = document.getElementById("root");
if (!root) throw new Error("élément #root introuvable dans index.html");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
