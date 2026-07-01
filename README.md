# cockpit

> Cockpit *lightweight* (WSL) pour cartographier des projets en roadmaps et **dispatcher des workers IA isolés** sur des tasks définies en amont.

**Statut : privé · pré-opérationnel (V1 en construction).**

Fait partie d'un framework en 3 repos :
- **`cockpit`** — l'orchestrateur (ce repo).
- **`code-map`** — index de code déterministe (Python + TSX), injecté dans chaque projet géré.
- **`catalog-mcp`** — serveur MCP servant la doc (catalogs) et les décisions aux workers.

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

## Développement

_À compléter au fil de P1._ La boucle CLI (P0→P4) est le MVP opérationnel ; le web (P5) est une surface par-dessus.

## Licence

Propriétaire — voir [`LICENSE`](./LICENSE). Tous droits réservés.
