# docs/ — la spec du cockpit (à lire AVANT de coder)

| Fichier | Rôle |
|---|---|
| [`architecture.md`](architecture.md) | Les couches (config → core/db → git/projects/roadmap/dispatch/gate → daemon) + « ce que cockpit n'est PAS ». |
| [`schema-contract.md`](schema-contract.md) | Les 3 schémas **figés** : SQLite, `roadmap.yaml` in-repo, API HTTP + politique de versionnage. |
| [`weak-points.md`](weak-points.md) | Le **registre de refactor** : dettes du legacy refusées → refactor décidé (la spec du portage, `#N`). |
| [`multi-os.md`](multi-os.md) | Portabilité WSL-first / Debian / macOS + checklist. |
| [`specs/`](specs/) | Les **6 décisions distillées** du vault en contraintes de design (règles verrouillées + invariants de test). |

## Les 6 specs (`specs/`)

- `worktree-cleanup-at-merge` — cleanup worktree AVANT `branch -D` ; port↔worktree couplé.
- `merge-writeback-injected-creds-identity` — creds+identité injectés le temps du writeback (réf, pas secret).
- `task-next-resolver-dag` — DAG `phases:`+`depends_on`, dérivé en un point, classification figée.
- `sot-local-worker-vs-clone-split` — worktree attaché au SoT partagé ; flock sur le `.git`.
- `feature-verified-gate` — gate déterministe fail-closed, ancré SHA, jamais blanchi, N/A-safe.
- `forge-code-merge-sot-local` — cockpit EST la forge ; SoT local ; reset=respawn ; miroir best-effort.
