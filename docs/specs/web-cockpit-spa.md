# spec — cockpit SPA web (Phase 5)

> Décisions distillées (vault : `decisions/stack-choices/2026-06-09--cockpit-front-react.md`,
> `decisions/projects/2026-06-16--cockpit-frontend-architecture.md`, `…/2026-07-01--cockpit-visual-loop.md`).
> Cible : `web/` (+ `daemon/app.py` pour le service statique + CORS). Le web est une **vue** par-dessus le
> cœur CLI/daemon, **jamais** la spine.

## Problème tranché

La forge est headless-solide mais **invisible**. Une SPA doit rendre lisibles projet → roadmap → dispatch →
gate → merge → terminal, sans jamais devenir un chemin obligatoire du métier (tout reste faisable en CLI/API).

## Règles verrouillées (ne pas re-débattre)

1. **Stack figée** : Vite + React 19 + TS + **Tailwind v4** (`@theme` = véhicule UNIQUE des tokens) +
   **TanStack Query + TanStack Router** + Radix (a11y) + Zod (validation runtime) + xterm.js (PTY). SPA
   **buildée, servie en statique par le daemon** (`_mount_spa`, fallback index.html pour le routing client).
   Radix/xterm/react-virtual installés **quand une vague les consomme** (pas de dep « au cas où »).
2. **Ordre imposé** : `tokens → layout → primitives → écrans → raffinement`. Système de design AVANT les
   écrans ; « plus aucun écran sans passer par les primitives + tokens ».
3. **Front sous `web/src/`** — convention gravée dans `gate/verify.py` (`UI_PATH_HINTS`) : tout diff y
   touchant rend la preuve Tier-1.5 obligatoire au merge.
4. **Charte** : accent teal AVAgency `#2a9d8f` (accent-600 AA 4.9:1) ; élévation 2 rôles nommés
   (`shadow-raised`/`shadow-overlay`) ; z-index **échelle nommée** (`z-(--z-*)`, pas de namespace v4) ;
   teintes de statut = **source unique** `lib/statusTone.ts` (classes littérales, jamais construites) ;
   primitives typo `Eyebrow`/`Card`/`SectionTitle`. **V1 = dark-first** (un thème ; le light migrera vers
   `:root`/`.dark` — `@theme` interdit le nesting).
5. **IA = workspace projet + onglets** (option A, 2026-07-02) : header + rail de projets + espace de
   travail par projet à onglets `Roadmap · Dispatch · Gate · Terminal`. 1 projet = 1 contexte.
6. **Réimplémentation, pas fork** : la structure legacy (`aggregator/web/`) sert de **patron**, aucune ligne
   copiée. Pas de HashRouter, pas de tokens de rail legacy, pas de pipeline dist-committé, zéro Proxmox/ssh.
7. **Contrat API = SoT typé** (`lib/schemas.ts`, Zod) : miroir exact du daemon. Rappels — chemins
   asymétriques `/api/projects/{p}/…` vs `/api/features/{p}/{f}/…` ; `depends_on` déjà en liste ; POST
   dispatch **long bloquant** ; **GO humain** obligatoire (gate vert sans `go` ⇒ `hold`, jamais merge).
8. **L'état DAG vient du backend, jamais du front** (V2) : `GET /…/roadmap` renvoie les tasks **classées**
   (`resolver.classify` : READY/BLOCKED_DEPS/CYCLE/…) + le NEXT par feature. Le front ne recalcule aucun
   état (pas de 2ᵉ résolveur en TS qui dériverait) ; il ne fait que du **layering** géométrique (`lib/dag.ts`,
   pur/testé). `depends_on` est **intra-feature** → la roadmap se rend comme **un graphe node-link par
   feature** (couches topologiques + colonne cycle), pas un graphe inter-feature.

## Vérification (par vague)

- **Déterministe** : `npm run build` (types + bundle) + `npm run lint` + `python tools/front_conformance.py`
  (design-system R1-R5) **bloquants** ; `vitest` (primitives/logique) lancé à la main, 🟡 hors Tier-0.
- **Boucle visuelle** (mandat) : `tools/ui_shot.py <route>` → **Read** le PNG → critique → edit → re-shoot.
  Itération sans verdict ; deep-link read-only pour une surface lazy (ouvre/rend, n'exécute jamais).
- **Tier-1.5 feature-verified** avant merge : la vue rend son **résultat métier FR** dans le DOM, lié au SHA.
- **CORS** : dev Vite (`:5173`) → daemon (`:8700`) ; prod same-origin (StaticFiles). Pas de credentials.
