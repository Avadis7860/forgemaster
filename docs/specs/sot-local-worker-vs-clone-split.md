# spec — sot-local-worker-vs-clone-split

> Contrainte distillée (vault `decisions/projects/2026-06-30--worktree-per-feature-hybrid.md`,
> `2026-06-29--forge-local-sot-and-reset.md`). Cible : `dispatch/worktree.py`, `git/internal.py`.
> Refactor #7/#12.

## Problème tranché

Split read/write sur deux `.git` disjoints : le worker poussait sa branche dans un **clone WORKER distinct**
du clone-SoT que le reviewer/gate/merge sondent → le reviewer « se kill sans commencer »
(`head-sha-introuvable`) car la branche est invisible de son côté.

## Règles verrouillées

- Le worker opère dans un **`git worktree` attaché au SoT, pas un clone distinct** : `git worktree add`
  partage le `.git` → la branche EST une ref du SoT, immédiatement visible par reviewer/gate/merge.
  **Traiter la cause (unifier le `.git`)**, jamais ajouter un 3e checkout read-mirror.
- **N branches en vol sur une même SoT ⇒ un worktree par branche, pas un clone par branche.**
- **Dans un 2e worktree, ne JAMAIS `git checkout -B <base>`** (la base est déjà sortie dans le worktree
  principal → « 'dev' is already used by worktree… »). Ancrer directement :
  `git worktree add -B <branche-cible> <wt> origin/<base>`.
- **Concurrence sérialisée par `flock` sur un fichier du `.git` PARTAGÉ** (`.git/cockpit-worktree.lock`,
  fd dédié), **pas** un lock in-process (qui ne couvre pas des relais/process concurrents — refactor #12).
- **Routage pur d'abord** (`route(decision) → {kind, branch, slug}`), wiring minimal ensuite.

## Invariants de test (à encoder dans cockpit)

- Repro du split : pousser dans un `.git` que le consommateur ne sonde pas → `head-sha-introuvable` ; le
  worktree partagé rend la branche visible (`reviewed_sha == HEAD`).
- `git worktree add -B` dans un 2e worktree où la base est déjà sortie **réussit** ; un `checkout -B <base>`
  doit planter (garde-fou anti-régression).
- **Trou de couverture à combler** (le legacy ne l'a jamais crash-testé live) : **deux `worktree add`
  concurrents** sérialisés par le flock, sans corruption.
