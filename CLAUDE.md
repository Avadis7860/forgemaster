# CLAUDE.md — cockpit (forge/orchestrateur local de projets)

> Lu au début de **chaque** session opérant dans ce repo. Persona `tool-builder` active. Cadre
> **verrouillé** ci-dessous — ne pas re-débattre ; livrer. Produit du framework *cockpit* (outils frères :
> `code-map`, `mcp-catalogs`).

## 1. Mission

Une **forge/orchestrateur local** qui enchaîne `projet → roadmap (features + tasks DAG) → dispatch d'un
worker `claude` local en worktree isolé → gate → merge`, le tout via **CLI + daemon**, en **WSL local,
sans Proxmox/GPU/CT**. Le web (terminal PTY + panneaux) est une **vue** par-dessus le cœur, pas la spine.

**Succès (binaire)** : `pip install -e .` puis la boucle CLI tourne end-to-end en local — créer un projet,
définir feature + tasks, dispatcher un worker dans un worktree isolé, passer le gate, merger la feature et
nettoyer le worktree. (Phase structure : `cockpit --help` répond, le socle résout, la base SQLite se crée.)

## 2. Framework VERROUILLÉ (ne pas re-choisir)

- **Python ≥ 3.11**, package installable (src-layout, `pyproject` hatchling), **un CLI** `cockpit` + un
  **daemon FastAPI**. C'est un **SERVICE** : deps runtime assumées (`fastapi`, `uvicorn`, `pyyaml`) ; le
  socle (`config`/`core`/`db`) reste stdlib-pur, imports serveur **paresseux** (le package s'importe sans).
- **Réimplémentation propre, PAS un fork** de l'orchestrateur legacy : on importe les décisions distillées
  comme specs (`docs/specs/`), on ne copie aucune ligne. Le registre `docs/weak-points.md` liste les
  **dettes refusées** (god-module `import server`, couplage proxmox/ssh, monolithe 1650-LOC) et le refactor.
- **Spine = CLI + cœur déterministe** ; le daemon et le web sont des vues. **Déterministe-d'abord** :
  toute I/O (exécution locale, git) est injectable → testable hors-live.
- **Modèle cœur = feature-groupe-des-tasks** : feature = branche = **worktree** (le mutex) ; tasks =
  unités de dispatch **séquentielles** intra-feature ; merge quand la feature est complète ; multi-worktree
  = plusieurs features en parallèle.
- **Git internal-first** : interface `GitBackend` + adapter `InternalGit` (bare repo local, zéro réseau)
  en V1 ; `GitHubGit` différé P6. Le seam transport est **local** (`core.run`), jamais ssh/proxmox.
- **Transport local, zéro hôte distant** : `core.run(cmd, cwd)` remplace `ssh dev@ip` ; aucune notion de
  CT, IP, clé ssh, `/home/dev`. Racines par **config** (`COCKPIT_HOME`, `COCKPIT_PROJECTS_ROOT`).

## 3. Comment travailler ici

- **Les docs sont la spec** : lis `docs/architecture.md`, `docs/schema-contract.md`, `docs/weak-points.md`,
  `docs/multi-os.md` et surtout `docs/specs/*.md` (les 6 décisions distillées : contraintes verrouillées +
  invariants de test) **avant** de coder. Les schémas (SQLite / `roadmap.yaml` / API HTTP) sont un
  **contrat FIGÉ** — on change une *implémentation*, pas un *schéma* (sinon : bump + changelog).
- **Réimplémentation couche par couche** : chaque couche non portée lève `NotImplementedError("port:
  <source> — #N")`. Porter = appliquer le refactor `#N` correspondant (`docs/weak-points.md`) via le skill
  `port-tool`. Le **socle `config`/`core`/`db`** est déjà fonctionnel (pas un stub).
- **Anti-boucle** : avant une API non triviale (`fastapi`, `sqlite3`, `git`), consulte la doc / le code —
  n'invente pas de signature. MCP `vault-catalogs` best-effort **s'il est branché**.
- **Qualité = gate** : `ruff` + `mypy` + `pytest` + **smoke réponse** (CLI `--help`, daemon importable
  sans fastapi, socle résout, DB se crée) **verts** avant tout commit (skill `quality-gate`). Un gate qui
  touche à l'irréversible (merge/destroy) est **fail-closed** et exige un feu vert humain (spec
  feature-verified). Fixtures minuscules, **noms fictifs** (jamais un vrai basename de projet).
- **Git** : branche `feature/<sujet>`, jamais de commit direct sur `main`/`dev`.

## 4. Anti-patterns (à ne jamais faire)

- ❌ Ré-introduire un **god-module** (`import server` tapé partout) au lieu d'une **injection explicite**
  (correctif #1) — les couches reçoivent `settings` + leurs deps en argument.
- ❌ Ré-introduire un **couplage transport distant** (ssh/proxmox/CT-id, `/home/dev` en dur) au lieu de
  `core.run` local + racines par config (correctifs #2/#4).
- ❌ Reconstruire un **monolithe** : les routers sont découpés par domaine (correctif #3).
- ❌ Changer un **schéma** (SQLite / roadmap.yaml / API) sans bump + changelog (contrat inter-couches).
- ❌ **Cap silencieux** : toute borne (log tail, diff, limit) se **signale** ; un partiel qui se dit complet = bug.
- ❌ Un gate qui **blanchit** un échec d'exécution (node/browser/timeout → jamais « vert ») ou qui juge la
  fraîcheur au **mtime** (toujours par SHA de HEAD pour un verdict).
- ❌ Merger/détruire **en autonomie** : acte outward irréversible ⇒ feu vert humain, fail-closed.
- ❌ Inventer une signature d'API « de mémoire » au lieu de lire la doc / le code.
