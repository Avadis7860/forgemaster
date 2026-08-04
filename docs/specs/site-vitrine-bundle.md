# spec — bundle `site-vitrine` (P0 : cadrage — décisions + architecture)

> Phase P0 de l'épic vault `cockpit-vitrine-e2e`. Ce document **verrouille** le cadrage du 2ᵉ E2E de création
> de bundle (après `browser-game`/`ogame-rogue-like-pve`), sur un projet **non-jeu présentationnel**. But méta :
> apprendre à créer/ingérer des bundles. **Rien n'est construit ici** — P0 est la décision + l'architecture ;
> le bundle se bâtit en P1, le forgemaster construit le site en P2, le déploiement VPS est en P3, la distillation
> capital (blueprint/templates) en P4. Précédent de format : `docs/specs/ogame-rogue-like-pve-bundle.md`.

## Problème tranché

Aucun bundle **présentationnel** n'existe (vérifié 2026-07-26 via MCP : `query(type=blueprint, "site vitrine …")`
→ `empty:true` ; aucun template présentational ; 1 seul template UI servi, `browser-game-pve`). Une **vitrine du
framework** (le forgemaster + son protocole) est le **besoin réel** qui matérialise proprement la classe `site-vitrine`
sans forward-feature. On crée donc un **nouveau type de bundle générique** `site-vitrine` — pas taillé pour ce
contenu précis, réutilisable pour toute vitrine/landing.

## Décisions verrouillées (confirme l'interview 2026-07-23)

1. **Nouveau type `site-vitrine`** — enregistrement **zéro-code** : déposer `src/forgemaster/provision/bundles/types/
   site-vitrine/` suffit (`discover_types()`, `provision/__init__.py:39-45`). Aucun enum, aucune migration DB.
2. **Stack** : **Astro** (SSG → perf/SEO natifs) + **Tailwind** + **îlots React** + **motion** (motion.dev, WOW)
   + **MDX** (contenu). **i18n natif Astro**, **EN par défaut**, FR, DE.
3. **Gate composite** (dans le `package.json` du bundle) : `astro check → tsc --noEmit → vitest run → astro build`.
   ⚠ Piège toolchain : `.astro` **n'est pas** un node-suffix (`gate/toolchain.py:47`) — le groupe `front` ne se
   déclenche qu'au **contact d'un chemin `web/`**. Le scaffold DOIT donc porter un fichier sous `web/` (la probe)
   et un `package.json` racine au `gate` composite pour que le gate se monte réellement.
4. **Bundle générique** — *pas* over-fitté au contenu forgemaster, *pas* taillé pour l'observation. **Le contenu vit
   dans le PROJET** `forgemaster-vitrine` (P2) ; côté bundle, **placeholder neutre**. Le suivi logs / auto-amélioration
   est une **couche externe** (l'instrument de l'E2E), pas le bundle.
5. **Le forgemaster pilote la complétion** (dispatch → gate → itère → GO). **On ne hand-code pas la vitrine** — c'est
   tout le sens de l'E2E (et de la doctrine capital-jeton : le worker construit, on distille).
6. **Déploiement VPS** (P3, **différé** — accès existe, pas l'urgence) : Docker + reverse-proxy multi-tenant
   (Traefik **ou** Coolify — tranché en P3) + TLS, DNS `forgemaster.avagency.pro`. Esquissé, non construit ici.

## IA de contenu — 5 piliers → one-pager scrollytelling (le PROJET, pas le bundle)

Direction visuelle : **bold editorial + motion**, one-pager scrollytelling, animation **riche mais maîtrisée**
(Lighthouse préservé). EN primaire · FR · DE. Les 5 piliers (spécifiés dans le tracker vault, ré-ancrés ici) :

1. **Distribution self-hosted** — une forge distribuable, chez soi.
2. **Tout tourne autour des décisions** — le cliquet capital-jeton (mémoire qui compound par distillation).
3. **Auto-managé par les Claudes** posés sur chaque outil/projet (worker + work-loop + gate).
4. **Claude Code only** — assumé/transparent ; **terminal Claude Code intégré** = porte de sortie.
5. **Le « pourquoi »** (hero) — né de la douleur de la **mémoire long-terme** ; résultat : Claude Code + les outils
   **reprennent un projet à froid, comme si Claude ne l'avait jamais vu**.

> **Frontière** : ces piliers sont l'IA de contenu **du projet** `forgemaster-vitrine` (authoré en P2). Le **bundle**
> ne les porte PAS — il sème une structure one-pager i18n **neutre** (placeholders). Ne jamais figer le contenu
> forgemaster dans le bundle générique.

## QueryPlan tech (silos que les workers P2 interrogeront — TOUS vérifiés servis 2026-07-26)

| Besoin | Silo `tech` (scope) | État |
|---|---|---|
| Framework SSG + i18n + islands | `astro` | full (416 p.) |
| Styling | `tailwind` | full |
| Animation WOW | `motion` | partial (108 c.) |
| Contenu riche | `mdx` | full |
| Îlots interactifs | `react` | full |
| Accessibilité | `wai-aria-apg` | full |
| Types / tests | `typescript` · `vitest` | partial / full |
| **Déploiement (P3)** | `docker` · `docker-compose` · `nginx` · `coolify` · `ufw` | full |

**Zéro silo à inventer.** i18n couvert par le silo `astro` (natif). Anti-boucle : les workers interrogent
`query(type=tech, scope=<silo>)` avant tout import non trivial (jamais de signature « de mémoire »).

## Architecture du bundle P1 (fichier-par-fichier — calquée sur le contrat prouvé `front-ts`)

Contrat auto-imposé : `tests/test_provision.py` est **paramétré sur `discover_types()`** → dès que le dossier
existe, `pytest` **exige** le contrat ci-dessous (durcir `validate_bundle` serait redondant — trust-by-boundary).

```
src/forgemaster/provision/bundles/types/site-vitrine/
├── .forgemaster/bundle.toml         # RE-déclarer [bundle.mcp] corpus=true (override whole-file, sinon perdu) ;
│                                #   project_type="site-vitrine" ; facets=["frontend","i18n","deploy","doc"] ;
│                                #   default_facet="frontend" ; archetype="app"
├── CLAUDE.md                    # 6 sections canoniques : §2 persona VITRINE senior · §3 stack · §4 silos en
│                                #   prose (query(type=tech, scope=<silo>) + `forgemaster mcp wire`, PAS de
│                                #   scope=browser-game mort) · `GO humain` · `docsmap where`
├── docs/architecture.md         # non-stub : `## Comment ce projet se travaille`, ≤1 `À renseigner`
├── src/README.md                # ancre par-dossier (type src-bearing → ajouter à _SRC_README_TYPES)
├── package.json                 # gate composite : "astro check && tsc --noEmit && vitest run && astro build"
├── astro.config.mjs             # i18n {defaultLocale:"en", locales:["en","fr","de"]}, intégrations react+mdx+tailwind
├── tsconfig.json                # strict, jsx react-jsx
├── src/pages/index.astro        # one-pager neutre (placeholder) + layout scrollytelling
├── src/layouts/… src/i18n/…     # squelette i18n (dictionnaires en/fr/de neutres)
├── web/Probe.tsx                # PROBE toolchain sous web/ → déclenche le groupe `front` (gate montable)
├── Dockerfile                   # multi-stage : astro build → nginx servant sur :8000
├── compose.yaml                 # web: build "." ; "${FORGEMASTER_PORT:?…}:8000" ; PAS de volumes:/networks:
├── .dockerignore
└── .claude/facets/{frontend,i18n,deploy}/{PERSONA.md,METHOD.md,settings.local.json}
                                 # (le facet `doc` est hérité de bundles/base — ne pas le redéposer)
```

**Câblage tests (à faire au build P1, non couvert par le filet auto)** — dans `tests/test_provision.py` :
ajouter `site-vitrine` à `_SERVICE_TYPES` (déployable), `_APP_STUB` (l'entrée servie), `_TYPE_TOOLCHAIN_PROBES`
(une probe **sous `web/`**, ex. `["web/Probe.tsx"]`), `_SRC_README_TYPES`. Le stub servi (`compose`/`Dockerfile`)
suit le contrat runtime prouvé (`front-ts` : port interne 8000, `build "."`, pas de `volumes:`/`networks:`).

**Correction d'un postulat périmé** (audit épic 2026-07-24) : « `derive.py:152` hardcode `browser-game-pve` → ne
pas créer `derive/site-vitrine/` » **n'est plus vrai à `e698b71`** — `derive.py` est **paramétré par `values.toml`**
et le dossier `derive/` n'existe même plus. Créer `derive/site-vitrine/` est **optionnel** (opt-in à la dérivation
build-time), pas interdit ; par défaut le bundle est authoré verbatim (aucune dérivation au seed).

## Frontière bundle / projet / worker (préserve le sens de l'E2E)

- **Bundle** (générique, capital construit à la main en P1) : la structure Astro i18n neutre + le gate + le seed
  deploy + le `CLAUDE.md` type-level (stack, silos, persona).
- **Projet** `forgemaster-vitrine` (P2, construit par le forgemaster) : le contenu réel (5 piliers), le design visuel,
  les 3 langues.
- **Worker** : lit le `CLAUDE.md` du bundle + interroge les silos `tech` ; construit ; passe le gate ; GO humain.

## Roadmap capital (le « intégrer dans blueprints/templates/bundles » — SÉQUENCÉ par la doctrine capital-jeton)

Le capital grandit par **distillation**, jamais construit en amont « pour voir » (brut au centre = anti-capital).

- **P1 — le BUNDLE** (`bundles/types/site-vitrine/`) : seul artefact capital **construit à la main**, générique.
  Les décisions type-level (stack, gate, silos, persona) vivent dans **son `CLAUDE.md`** — c'est là que le 1er
  build puise, pas dans un blueprint central (le worker ne lit pas le blueprint).
- **P2 — le forgemaster CONSTRUIT** `forgemaster-vitrine` depuis le bundle (vrai drain → **peuple `merge_outcomes` →
  alimente la campagne de fiabilité E3**). Les manques du bundle révélés ici → patch **générique**.
- **P4 — DISTILLER** le pattern prouvé → graduer un **blueprint** `site-vitrine` + **templates** dans
  `mcp-catalogs-data`, **si réutilisabilité prouvée**. **Jamais en amont.**

> **Précédent canonique** (`ogame-rogue-like-pve-bundle`, superseded 2026-07-24) : le spécialisé ne se **hand-code
> pas** en bundle-type — il vit en **capital servi** (blueprint + templates) et l'interview du **générique**
> l'énumère comme **STYLE**. `site-vitrine` est donc le **type générique** ; toute vitrine à identité visuelle
> forte (un « STYLE » de vitrine) graduera en blueprint/templates, énumérée par l'interview `site-vitrine` — le
> worker construit. Ne pas refaire l'erreur du bundle-type spécialisé hand-codé.

## Phases (GO humain par phase — fail-closed)

- **P0 — cadrage** *(ce document + le tracker vault = P0)*. ✅ à la validation de ce doc.
- **P1 — bundle générique** (repo forgemaster, skill `work-loop`) : scaffold + facets + gate + seed deploy + câblage
  tests. Gate vert → GO → merge.
- **P2 — le forgemaster pilote la complétion** de `forgemaster-vitrine` sur la VM 9310 fraîche (E2E). Monitorer les logs,
  remonter les manques du bundle (patch générique).
- **P3 — déploiement VPS multi-tenant** (Docker + Traefik/Coolify + TLS + DNS `forgemaster.avagency.pro`). Différé.
- **P4 — distillation & capital** : enseignements ingestion/création de bundles → docs/specs ; graduation
  blueprint + templates `site-vitrine` si réutilisable prouvé ; post-mortem vault.

## Sources (contrat vérifié 2026-07-26, forgemaster @e698b71)

- Découverte zéro-code : `provision/__init__.py:39-45` ; composition base|overlay whole-file `:79-90`.
- Contrat de type : `tests/test_provision.py` (paramétré sur `discover_types()`) — CLAUDE.md 6 sections
  (`:427-434`), `[bundle.mcp] corpus=true` survit à l'override (`:149-159`), `docs/architecture.md` non-stub
  (`:461-468`), facets adossées (`:310-322`), probes toolchain `_TYPE_TOOLCHAIN_PROBES` (`:328-333`).
- Déployabilité : `_SERVICE_TYPES`/`_APP_STUB` (`:560-577`), contrat compose (`:589-613`) ; réf `front-ts/
  {Dockerfile,compose.yaml,server.mjs}`.
- Toolchain groups : `gate/toolchain.py` (`web/`→`front` `:44` ; node-suffix hors `web/`→`backend-node` `:47`).
- Capital servi (MCP, 2026-07-26) : `tech` full pour la stack ; `blueprint`/`templates` `site-vitrine` vides.
