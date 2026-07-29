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
5. **a11y — invariants du socle (BaseLayout)**, verrouillés par `web/src/layouts/layout.test.ts` (semé) : ÉTENDS
   cette garde, ne l'affaiblis jamais. Un seul `<h1>` par page ; landmarks **uniques** (`header/main/footer`),
   navigations **nommées et distinctes** ; skip-link RÉEL (premier focusable, vise l'id que `<main>` porte,
   `tabindex="-1"` sur `<main>`, redevient visible au focus) ; un header `sticky`/`fixed` ⇒ `scroll-margin-top`/
   `scroll-padding-top` (sinon la cible d'ancre est masquée sous l'entête) ; **un seul** `aria-current="page"` par
   destination (jamais sur le mot-marque ET le lien de nav « accueil » — la marque de page appartient au lien de
   nav) ; aucun texte LISIBLE peint en dégradé (`text-gradient-*` a un contraste variable qui tombe sous le
   plancher — un aplat au-dessus de 3:1/4.5:1 ; le dégradé reste OK en décor `bg-gradient-*` `aria-hidden`) ;
   cibles tactiles ≥44px (`min-h-11`), `:focus-visible`, `alt` signifiant. Patrons : `query(type=tech, scope=wai-aria-apg)`.
6. **Couverture des gardes** — une garde (a11y, anti-tiers, contraste, SEO) doit balayer **tous** les endroits
   atteignables, `astro.config.mjs` compris — pas seulement `web/src/**`. Une garde à portée trouée est une
   **fausse couverture** : elle rassure à tort, pire que pas de garde. Un domaine/tiers vit souvent DANS la config.
7. **Gate composite** — `astro check` → `tsc --noEmit` → `vitest run` → `astro build` vert. Corrige la cause.
8. **Fraîcheur** — front touché → `frontmap build` (+ `codemap build` pour la logique).
