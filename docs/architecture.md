# Architecture — forgemaster

Le forgemaster est une **spine CLI + cœur déterministe**, avec un **daemon FastAPI** et un **web** comme vues
par-dessus. Réimplémentation propre de l'orchestrateur legacy (`services/aggregator/`) : le code utile
était déjà pur (backing modules stdlib) mais enseveli sous un god-module et un couplage Proxmox/ssh ; on
reconstruit couche par couche en appliquant les refactors de `weak-points.md`.

## Les couches (de bas en haut)

```
                       ┌───────────────────────────────────────────────┐
   web (P5, Vite)  ───▶│  daemon/  (FastAPI, DI explicite, routers/     │  vues
   CLI `forgemaster`   ───▶│           découpés par domaine)  ·  terminal/  │
                       └───────────────────────────────────────────────┘
                                          │  (délègue au cœur)
        ┌─────────────┬───────────────────┼───────────────────┬─────────────┐
        ▼             ▼                    ▼                   ▼             ▼
   projects/      roadmap/            dispatch/            gate/          (git/)
   registry     model · resolver   worktree·worker·jobs  review·verify·merge
        │             │                    │                   │             │
        └─────────────┴──────────┬─────────┴─────────┬─────────┘             │
                                 ▼                   ▼                       ▼
                          ┌──────────────┐    ┌──────────────┐      ┌──────────────┐
                          │     db/      │    │    core/     │      │  git/backend │
                          │ schema·store │    │ run·ids·fs   │      │  (interface) │
                          │  (SQLite)    │    │ (subprocess) │      │ internal/github
                          └──────────────┘    └──────────────┘      └──────────────┘
                                 │                   │                       │
                                 └───────────────────┴───────────────────────┘
                                              config  (racines résolues, injectées)
```

- **`config`** — le socle : résolveur générique des racines (`FORGEMASTER_HOME`, `FORGEMASTER_PROJECTS_ROOT`),
  gelé, injecté aux couches. Aucune notion de vault/proxmox/ssh.
- **`core`** — patterns purs : `run` (exécution locale subprocess = le seam transport réécrit), `ids`
  (slugs kebab validés), `fs` (accès borné anti-traversal + JSONL).
- **`db`** — persistance SQLite unique (projects/features/tasks/dispatch_jobs) ; `schema` = contrat figé.
- **`git`** — frontière `GitBackend` (interface) + `InternalGit` (bare local, V1) / `GitHubGit` (P6).
- **`projects` · `roadmap` · `dispatch` · `gate`** — le métier : registre, roadmap+résolveur DAG, dispatch
  worker en worktree, gates + merge. Chacun reçoit `settings` + ses deps (DI), ne tape aucun module-global.
- **`daemon` · `terminal`** — vues : FastAPI (routers par domaine, DI explicite) + PTY local. Le web (P5)
  parle la CLI.

## Frontières délibérées — ce que forgemaster n'est PAS

- **Pas de Proxmox / GPU / CT / ssh.** Édition lightweight WSL : les workers sont des **process locaux**
  (`claude -p` dans un worktree), l'exécution passe par `core.run(cmd, cwd)`, jamais `ssh dev@ip`. Les 6
  routers infra du legacy (gpu/proxmox/spawn/host/signals/qa) sont **jetés**, pas portés.
- **Pas de dépendance BWS obligatoire.** Un secret de push est une **référence** résolue en amont par
  l'appelant ; le forgemaster ne connaît pas BWS (spec merge-writeback).
- **Pas une forge externe (Forgejo/GitLab).** Le forgemaster **EST** la forge (native-IA-worker) ; GitHub est
  transport/miroir best-effort swappable (spec forge-sot-local).
- **Pas un god-module.** Aucune couche n'importe un `server` global mutable ; tout passe par injection.
- **Pas de web dans la spine.** CLI + cœur d'abord (headless, testable) ; le daemon/web sont ajoutés par
  dessus et restent optionnels pour la boucle end-to-end.
