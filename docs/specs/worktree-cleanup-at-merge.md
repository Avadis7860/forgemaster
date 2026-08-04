# spec — worktree-cleanup-at-merge

> Contrainte de design distillée (vault `decisions/projects/2026-06-30--worktree-per-feature-hybrid.md`).
> Règles verrouillées : ne pas re-débattre. Cible : `dispatch/worktree.py`, `gate/merge.py`.

## Problème tranché

Séquencement destructif au merge : `git branch -D` sur une branche encore *checked out* dans un worktree
échoue (« checked out at … ») ; et les worktrees/ports **fuient** si aucun chemin ne les balaie.

## Règles verrouillées

- Le **cleanup du worktree se fait AVANT** `git branch -D`, jamais après (`remove_worktree` puis
  `delete_branch` — ces deux méthodes sont distinctes dans `GitBackend` pour rendre l'ordre explicite).
- **Cycle port ↔ worktree couplé** : 1 port réservé par worktree (réservation idempotente épinglée),
  injecté au worker (`export PORT`), **relâché au merge ET au reset**.
- **Deux chemins doivent balayer** les worktrees : le merge **et** le reset/respawn. Le cleanup n'est
  jamais mono-chemin (`worktree.release` est appelé par les deux).
- Un **audit d'orphelins** existe et doit rester vert (`worktree.audit`).

## Invariants de test (à encoder dans forgemaster)

- Merge d'une feature dont la branche est sortie en worktree → l'ordre remove-then-delete réussit ;
  **inverser** l'ordre doit reproduire « checked out at » (test de non-régression).
- Après merge ET après reset, `audit` retourne **0 orphelin** sur 3 classes : worktree-sans-port,
  port-fantôme, worktree-sur-task-close.
- Un port réservé pour un worktree n'est **jamais encore réservé** après merge/reset (pas de fuite).
