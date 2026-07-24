// App.tsx — composant racine d'amorçage. Consomme le modèle de domaine PARTAGÉ (`src/shared`) pour prouver
// l'univers TypeScript unifié (décision 1). Vue minimale des ressources ; la vraie UI de gestion (panneaux
// bâtiments/recherche/chantier/galaxie, connexion WS temps-réel) est bâtie en F4 (2e). Le client ne calcule
// jamais l'état de jeu (anti-triche, décision 2).
import type { GameState } from "../src/shared/schema.js";
import { initialGameState } from "../src/shared/tick.js";

// Jetons de mission (remplis par le worker du projet) — littéraux pour rester tsc-vert avant remplacement.
const gameName = "{{game_name}}";
const theme = "{{theme}}";

const seed: GameState = initialGameState();

export function App() {
  return (
    <main>
      <h1>{gameName}</h1>
      <p>Squelette ogame semé — thème : {theme}. Le serveur est l'autorité ; ce client n'est qu'une vue.</p>
      <ul>
        <li>Métal : {seed.resources.metal}</li>
        <li>Cristal : {seed.resources.crystal}</li>
        <li>Deutérium : {seed.resources.deuterium}</li>
      </ul>
    </main>
  );
}
