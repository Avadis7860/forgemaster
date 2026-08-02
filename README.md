# cockpit

> Cockpit *lightweight* (WSL) pour cartographier des projets en roadmaps et **dispatcher des workers IA isolés** sur des tasks définies en amont.

**Statut : privé · pré-opérationnel (V1 en construction).**

Fait partie d'un framework en 8 repos :
- **`cockpit`** — l'orchestrateur (ce repo).
- **`code-map`** — index de code déterministe (Python + TSX), injecté dans chaque projet géré.
- **`front-map`** — index du design-system d'un `web/` (tokens, primitives, routes, usages).
- **`docs-map`** — carte de la prose `docs/` (headings interrogeables par intention).
- **`task-map`** — les liaisons STAMP d'une task (axe, épic, blueprint) et leur cohérence.
- **`mcp-catalogs`** — serveur MCP servant la doc tierce (`tech`) et le capital distillé
  (`blueprint`, `templates`) aux workers.
- **`mcp-catalogs-data`** — la donnée servie par ce serveur (seul foyer d'authoring du corpus).
- **`Vault-V1`** — la mémoire long-terme (décisions locales, missions, outillage de garde).

## Modèle cœur

```
projet (registre) → roadmap in-repo (features → tasks DAG)
  → [gate : pas de task ⇒ pas de dispatch] → dispatch worker (claude headless, local)
  → worktree git isolé (feature = branche = worktree, le mutex)
  → tasks séquentielles intra-feature → gate (tests + review) → merge → cleanup worktree
```

Multi-worktree = plusieurs features en parallèle. Backend git **internal-first** (bare repo local, zéro réseau) ; adapter GitHub en phase 2.

## Stack

- **Spine** : CLI `cockpit` + daemon FastAPI partageant un cœur (déterministe-d'abord, headless/scriptable).
- **Persistance** : un seul SQLite (projets, features, tasks, jobs de dispatch).
- **Front** (vue par-dessus) : Vite + React 19 + TanStack (Query/Virtual/Router) + xterm.js — terminal PTY parlant la CLI, panneaux DAG / worktrees / logs live.

## Installation (self-hosted)

Héberge ta propre instance — guide complet : [`docs/install.md`](docs/install.md).

```bash
# le plus simple : wheel packagé, l'UI ET code-map (onglet Flow) inclus (aucun Node requis)
pip install cockpit-0.1.0-py3-none-any.whl
cockpit serve                 # http://127.0.0.1:8700 → wizard /setup au 1er démarrage
```

Depuis les sources : `pip install -e .` puis `cockpit setup` (build l'UI + câble code-map depuis un clone
sibling `../code-map` ; Node requis). Service systemd :
`cockpit install-service`.

## Développement

Réimplémentation propre de l'orchestrateur legacy (pas un fork) : les décisions distillées sont importées
comme **specs** (`docs/specs/`), le registre `docs/weak-points.md` liste les dettes refusées + le refactor.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cockpit setup           # build l'UI (Node) — sinon daemon API-only
cockpit --help          # la spine répond
ruff check src tests && mypy && pytest -q   # gate qualité (cf. .claude/skills/quality-gate)
```

**Phase structure livrée** : squelette src-layout + socle fonctionnel (`config`/`core`/`db`/`cli`) + docs
complètes + stubs documentés (un par couche, pointeur de port + refactor `#N`). La boucle CLI (P0→P4) est
le MVP opérationnel ; le web (P5) est une surface par-dessus. Voir [`PORTING.md`](./PORTING.md) pour l'état.

## Licence

**AGPL-3.0-or-later** — voir [`LICENSE`](./LICENSE).

C'est un **service réseau** : quiconque expose une version **modifiée** à des utilisateurs à travers un
réseau doit leur en offrir le code source correspondant (§13).

Le wheel **embarque** `codemap/` et `taskmap/`, sous **Apache-2.0** : leur attribution est portée par
[`NOTICE`](./NOTICE) et le texte de leur licence par [`LICENSES/Apache-2.0.txt`](./LICENSES/Apache-2.0.txt).

Une **licence commerciale** est disponible pour les usages que l'AGPL n'accorde pas — notamment la
redistribution sous forme fermée : contact@avagency.pro.
