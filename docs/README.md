# docs/ — la spec du cockpit (à lire AVANT de coder)

| Fichier | Rôle |
|---|---|
| [`roadmap.md`](roadmap.md) | **Roadmap produit** : vision (cœur léger + extensions), chantiers V1 livrés, décisions de conception verrouillées, horizons ouverts. |
| [`install.md`](install.md) | **Installer le cockpit** (self-hosted) : wheel packagé (aucun Node) ou sources, wizard 1er démarrage, service systemd, coffre de secrets. |
| [`architecture.md`](architecture.md) | Les couches (config → core/db → git/projects/roadmap/dispatch/gate → daemon) + « ce que cockpit n'est PAS ». |
| [`schema-contract.md`](schema-contract.md) | Les 3 schémas **figés** : SQLite, `roadmap.yaml` in-repo, API HTTP + politique de versionnage. |
| [`weak-points.md`](weak-points.md) | Le **registre de refactor** : dettes du legacy refusées → refactor décidé (la spec du portage, `#N`). |
| [`multi-os.md`](multi-os.md) | Portabilité WSL-first / Debian / macOS + checklist. |
| [`specs/`](specs/) | Les **décisions distillées** en contraintes de design (règles verrouillées + invariants de test). |

## Les specs (`specs/`)

- `worktree-cleanup-at-merge` — cleanup worktree AVANT `branch -D` ; port↔worktree couplé.
- `merge-writeback-injected-creds-identity` — creds+identité injectés le temps du writeback (réf, pas secret).
- `task-next-resolver-dag` — DAG `phases:`+`depends_on`, dérivé en un point, classification figée.
- `sot-local-worker-vs-clone-split` — worktree attaché au SoT partagé ; flock sur le `.git`.
- `feature-verified-gate` — gate déterministe fail-closed, ancré SHA, jamais blanchi, N/A-safe.
- `forge-code-merge-sot-local` — cockpit EST la forge ; SoT local ; reset=respawn ; miroir best-effort.
- `tier0-native-toolchain-gate` — Tier-0 natif (front `npm run gate` + back ruff/mypy/pytest), non-overridable.
- `review-readiness-gate` — **quand** dispatcher le reviewer Tier-1 : readiness (feature finie) → dispatch fail-closed → verdict SHA-bound ; gate à la source (pas de filtre a posteriori), générique par type.
- `web-cockpit-spa` — SPA embarquée (Vite/TanStack), servie Node-less depuis le wheel.
- `runtime-compose-backend` — moteur de run compose ; namespace `cockpit-<slug>-<branch>` = isolation ; pool deploy 5250-5329.
- `runtime-seed-deploy-config` — config de run semée par type (compose+Dockerfile+stub) ; projet frais déployable sans édition ; non-service refusé.
- `runtime-antipollution` — env compose en allowlist (0 secret daemon) ; ACL secrets par projet ; FS/réseau/ports isolés vérifiés.
- `runtime-observability` — santé live (reconcile séparé du GET pur) ; logs tail bornés read-only ; liens health-gated ; onglet Runtime, aucun faux-vert.
- `runtime-e2e-verification` — harnais d'acceptance rejouable (podman réel) : déploiement main+dev, 2 projets simultanés, non-pollution, feature-verified SHA-bound ; clôt l'épic.
- `bundle-crash-test` — câblage MCP réel dans un worker + crash-test void-runner (create browser-game → dispatch `claude -p` sans crash → commit propre, JWT hors historique) ; clôt l'épic bundle-system.
- `template-ui-application-lifecycle` — le dirigeant applique un template UI de référence (`inspire`) ; graine `docs/design/<slug>/` posée par la forge (feature `design-<slug>`, merge GO) ; le worker la relit (`_design_block`) et CUSTOMISE ; MCP différé (N=1/0-app).
- `ws-origin-token-boundary` — frontière client WS = **Origin + token par-instance côté serveur** (avant `accept`), PAS le réseau ; ferme le CSWSH ; CORS≠garde-WS ; prérequis de distribution (`--host 0.0.0.0`).
- `ogame-rogue-like-pve-bundle` — split `browser-game` (générique neutre) vs **bundle crash-test** `ogame-rogue-like-pve` (jeu ogame **fini né-avec**, formules sourcées, roguelike-PvE seedé déterministe) ; deux types indépendants ; de-hardcode `derive.py` (ref blueprint → `values.toml`).
