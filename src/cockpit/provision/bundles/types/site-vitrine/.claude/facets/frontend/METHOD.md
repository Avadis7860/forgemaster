# Méthode — facette Frontend

1. **Boucle visuelle** — tout changement d'écran : screenshot **puis Read** de la capture AVANT de livrer.
   Ambigu (« façon X ») → mockup A/B d'abord.
2. **Preuve de rendu (Tier-1.5) — OBLIGATOIRE si tu touches `web/`** — émets `.cockpit/verify-markers.json` =
   `{"markers":[…]}` : les chaînes **littérales** que ton écran rend (titres, labels de ton `acceptance` ; dans
   la locale par défaut EN, ou celle de la page). Le gate **preview-déploie** ta feature et cherche ces marqueurs
   dans le DOM servi — déclare le vrai, pas un vœu (un marqueur non rendu ⇒ gate rouge, non-refixable). Une
   feature qui rend une page/un composant SANS ce manifeste est **immergeable** (Tier-1.5 échoue).
   **Route** : si ton écran vit sous un sous-chemin (ex. un showcase sur `/design-system`), ajoute
   `"path":"/design-system"` au manifeste — sinon le gate sonde la **racine `/`** et ne verra pas tes marqueurs.
3. **Zéro-JS par défaut** — pas d'îlot sans interaction réelle. Chaque `client:load|visible|idle` est un choix
   justifié ; préfère `client:visible`/`client:idle` à `client:load`.
4. **Design tokens d'abord** — `frontmap where` (tokens / primitives) avant d'écrire du CSS neuf ; anime avec
   `motion` sous `prefers-reduced-motion` (`useReducedMotion`).
5. **a11y** — un seul `<h1>` par page, landmarks (`header/nav/main/footer`), skip-link, `:focus-visible`, cibles
   tactiles ≥44px, `alt` signifiant. Patrons : `query(type=tech, scope=wai-aria-apg)`.
6. **Gate composite** — `astro check` → `tsc --noEmit` → `vitest run` → `astro build` vert. Corrige la cause.
7. **Fraîcheur** — front touché → `frontmap build` (+ `codemap build` pour la logique).
