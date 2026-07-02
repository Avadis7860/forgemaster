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
9. **Suivi de dispatch : live par WS, at-rest par HTTP** (V3). Le POST dispatch **bloque** jusqu'à la fin du
   run et ne rend le `job_id` qu'alors → le front **découvre** le job en cours via `GET /api/dispatch/{p}/{f}/jobs`
   (baseline capturée au clic), il n'attend pas le POST. Un run **EN COURS** est streamé en **live** par
   `WS /ws/dispatch/{job}` (boucle `jobs.read_events`, offset/inode ; frame terminale `{type:'job'}`). Un run
   **TERMINÉ** est lu **at-rest** par `GET /api/jobs/{id}` (`jobs.tail`) — **jamais de socket ouvert pour un
   run fini**. Le contrat d'événement est **unique** (`jobs.normalize_line` : assistant `text`/`tools`,
   `tool_result`) : seule la source diffère, le rendu (log structuré, pas un PTY) est identique.
10. **Gate = un seul GET idempotent, GO = la seule mutation** (V4). `GET /api/gate/{p}/{f}` renvoie le
    statut BRUT (review Tier-1 counts/fresh/blocking, verify Tier-1.5 n_targets/n_failed, `head_sha`,
    `ui_touched`) **ET** la **décision composée** en *preview GO=false* (`decision`: hold/merge, `gate_green`,
    `blockers`, `reasons`, overrides) — via `merge.evaluate_gate` (source unique, réutilisée par `run_merge`),
    **sans jamais muter**. Le front rend « gate vert sans GO ⇒ **hold** » depuis ce seul GET (jamais de
    recomposition côté TS) ; le runner de boucle visuelle *goto-only* l'atteint sans risque. Le **POST
    /api/merge** (bouton **GO humain**) est la seule mutation, **fail-closed** : `allow = gate_green ET go`
    — un gate vert sans `go` renvoie `hold`, jamais un merge (le LLM ne merge jamais seul). Les overrides
    `t1_override`/`t15_override` (raison explicite, tracée) ne lèvent qu'un 🔴 reviewer ou un Tier-1.5 —
    **jamais** un veto Tier-0 / toolchain native déterministe.

## Vérification (par vague)

- **Déterministe** : `npm run build` (types + bundle) + `npm run lint` + `python tools/front_conformance.py`
  (design-system R1-R5) **bloquants** ; `vitest` (primitives/logique) lancé à la main, 🟡 hors Tier-0.
- **Boucle visuelle** (mandat) : `tools/ui_shot.py <route>` → **Read** le PNG → critique → edit → re-shoot.
  Itération sans verdict ; deep-link read-only pour une surface lazy (ouvre/rend, n'exécute jamais).
- **Tier-1.5 feature-verified** avant merge : la vue rend son **résultat métier FR** dans le DOM, lié au SHA.
- **CORS** : dev Vite (`:5173`) → daemon (`:8700`) ; prod same-origin (StaticFiles). Pas de credentials.
