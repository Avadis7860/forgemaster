# CLAUDE.md — cockpit (forge/orchestrateur local de projets)

> Lu au début de **chaque** session dans ce repo. Persona `tool-builder`.
> Ce fichier = **règles + index + outils**, PAS la spec. Le détail (mission, cadre verrouillé,
> schémas, décisions distillées, points faibles) vit dans `docs/` — **interroge-le** (`docsmap where`), ne le recopie pas ici.

## Règles (non négociables)

- **Boucle de travail** : tout changement passe par le skill **`work-loop`** — worktree `feature/<sujet>`
  créée **depuis `dev`**, gate vert, puis `dev` en ff-only. **`main` ne se travaille jamais** : il n'avance
  que promu depuis un `dev` vert. Jamais de commit direct sur `main`/`dev`. (Ce repo **produit** la forge
  qui automatise cette boucle → on la **dogfoode** en la suivant manuellement pour se développer lui-même.)
- **Gate avant merge** : `ruff` + `mypy` + `pytest` + **smoke** (CLI `--help`, daemon importable sans
  fastapi, socle résout, DB se crée) — pour tout `web/`, aussi le **gate front** (`npm run gate`) + la
  **boucle visuelle** (screenshot + Read). **Verts** (skill `quality-gate`). Un acte irréversible
  (merge/destroy) = **feu vert humain, fail-closed**.
- **Anti-boucle** : pas de signature d'API inventée (`fastapi`, `sqlite3`, `git`) — lis la doc / le code.
  MCP `vault-catalogs` best-effort s'il est branché.
- **Anti-archéologie** : interroge les index au lieu de fouiller/lire à l'aveugle — `codemap`
  (`where`/`callers`/`imports`) pour le **code**, `frontmap` pour `web/`, `docsmap where` pour la **prose**
  de `docs/` (jamais lue en bloc pour s'orienter).
- **Invariants de la forge** (détail + correctif `#N` dans `docs/weak-points.md`, specs dans `docs/specs/`) :
  **spine = CLI + cœur déterministe** (daemon et web sont des vues ; toute I/O — exécution locale, git —
  **injectable**) · **injection explicite** (`settings` + deps en argument, **jamais** de god-module
  `import server`) · **transport local** (`core.run`, **zéro** ssh/proxmox/CT/`/home/dev`) · **modèle cœur
  feature-groupe-des-tasks** (feature = branche = worktree = mutex ; tasks séquentielles ; merge à feature
  complète) · **git internal-first** (`GitBackend` + `InternalGit`, `GitHubGit` différé P6) · **schéma
  (SQLite / `roadmap.yaml` / API) = contrat figé** (bump + changelog) · **jamais de cap silencieux** ·
  **fraîcheur par SHA de HEAD**, jamais mtime · **merge/destroy jamais en autonomie** (fail-closed).
- Fixtures minuscules, **noms fictifs** (jamais un vrai basename de projet).

## Index (interroge, ne lis pas en bloc)

La spec vit dans `docs/` (dont `docs/specs/`). **Ne la lis pas en bloc pour t'orienter** — `docs-map`
(injecté, zéro-dép) répond à l'intention ; lis ensuite **seulement** la tranche `fichier:lignes` renvoyée :

```
docsmap where "<intention>"     # → docs/…:lignes de la section pertinente
docsmap sections                # table des matières
```

Ce que couvre chaque doc (cibles de `docsmap where`) :

- `docs/architecture.md` — la spine (cœur / daemon / web), les couches, frontières.
- `docs/specs/*.md` — les **décisions distillées** portées comme specs (contraintes verrouillées +
  invariants de test) : forge-merge, worktree-cleanup, writeback-creds, task-next DAG, sot-local split,
  feature-verified, tier0-native-toolchain, review-readiness-gate, web-cockpit-spa, runtime-seed-deploy-config,
  bundle-crash-test, template-ui-application-lifecycle, ws-origin-token-boundary,
  ogame-rogue-like-pve-bundle (superseded — style servi, plus un bundle-type).
- `docs/schema-contract.md` — SQLite / `roadmap.yaml` / API HTTP, **figés** inter-couches.
- `docs/weak-points.md` — dettes legacy **refusées** (god-module, couplage proxmox/ssh, monolithe) + refactor.
- `docs/multi-os.md` — déterminisme WSL / Debian / macOS.

## Outils à disposition (embarqués dans ce repo)

- **Skills** (`.claude/skills/`) : `work-loop` (boucle de travail sûre, lightweight, sans forge externe) ·
  `quality-gate` (ruff + mypy + pytest + smoke CLI/daemon/DB [+ gate front]) · `port-tool` (réimplémentation
  propre depuis la spec, pas un fork).
- **Hook** (`.claude/hooks/post-edit-check.py`) : `py_compile` + `ruff` sur le `.py` touché à chaque édition.
- **Persona** (`.claude/output-styles/tool-builder.md`) : posture outilleur déterministe.
- **Index de code** : `codemap` (py + tsx) et `frontmap` (design-system `web/`) — injectables, auto-interrogeables.
- **Carte de doc** : `docsmap where/sections/read/check` sur la prose `docs/` (+`docs/specs/`) de ce repo (injecté, zéro-dép).
- **Doc tierce** : MCP `vault-catalogs` (`query_catalog` scopé, `read_doc`) s'il est branché.

## Ce repo EST la forge (rapport aux autres)

Le cockpit **automatise** `work-loop` : dispatch d'un worker `claude` local → worktree feature (mutex) →
gate multi-tier → merge internal-first → GO humain fail-closed, **multi-projet**, avec DB + web. Chaque repo
frère (`code-map`, `forgemaster-catalogs`, `front-map`) embarque le **même** `work-loop` en **manuel** et reste donc
**auto-travaillable seul**, sans cette forge. La forge est un **orchestrateur optionnel** par-dessus des
repos déjà autonomes — **mêmes invariants** partout (dev + worktree feature, gate vert, `main` protégé, GO humain).
