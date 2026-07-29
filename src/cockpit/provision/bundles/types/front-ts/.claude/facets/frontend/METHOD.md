# Méthode — facette Frontend

1. **Boucle visuelle** — tout changement d'écran : screenshot **puis Read** de la capture AVANT de livrer.
   Ambigu (« façon X ») → mockup A/B d'abord.
2. **Preuve de rendu (Tier-1.5)** — émets `.cockpit/verify-markers.json` = `{"markers":[…]}` : les chaînes FR
   **littérales** que ton écran rend (titres, labels de ton `acceptance`). La gate cherchera ces marqueurs dans
   le DOM du preview-deploy de ta feature — déclare le vrai, pas un vœu (un marqueur non rendu ⇒ gate rouge).
   **Route** : si ton écran vit sous un sous-chemin, ajoute `"path":"/ta-route"` au manifeste — sinon le gate
   sonde la **racine `/`** (défaut) et ne verra pas tes marqueurs.
3. **Design-system d'abord** — `frontmap where` (tokens / primitives / routes) avant de créer du neuf.
4. **Gate** — `eslint` → `tsc` → `vitest` vert. Corrige la cause.
5. **Fraîcheur** — front touché → `frontmap build` (+ `codemap build` pour la logique).
