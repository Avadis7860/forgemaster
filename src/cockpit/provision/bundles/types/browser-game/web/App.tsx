// App.tsx — composant racine d'amorçage. Consomme le modèle de domaine PARTAGÉ (`src/shared/schema`) pour
// prouver l'univers TypeScript unifié (décision verrouillée 1). Remplace-le par ta vraie UI de gestion (É6).
import type { Player } from "../src/shared/schema.js";

// Jetons de mission (remplis par le worker du projet) — gardés en littéraux de chaîne pour que le squelette
// reste tsc-vert AVANT leur remplacement.
const gameName = "{{game_name}}";
const theme = "{{theme}}";

const seed: Player = {
  id: "00000000-0000-0000-0000-000000000000",
  name: gameName,
  resources: [{ kind: "credits", amount: 0 }],
};

export function App() {
  return (
    <main>
      <h1>{gameName}</h1>
      <p>Squelette semé — thème : {theme}. Le serveur est l'autorité ; ce client n'est qu'une vue.</p>
      <ul>
        {seed.resources.map((r) => (
          <li key={r.kind}>
            {r.kind} : {r.amount}
          </li>
        ))}
      </ul>
    </main>
  );
}
