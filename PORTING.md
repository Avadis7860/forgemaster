# PORTING.md — journal de réimplémentation du cockpit

Réimplémentation propre de l'orchestrateur legacy (`services/aggregator/`), couche par couche via le skill
`port-tool`. **Pendant vivant** de `docs/weak-points.md` (weak-points = *quoi corriger* ; ici = *où on en
est*). On importe les décisions distillées comme specs (`docs/specs/`) — aucune ligne copiée à l'aveugle.

**Légende** : ⬜ stub · 🟡 porté (test partiel) · ✅ porté + testé + gate vert · ⏸ différé.

## Socle (fait — fonctionnel dès la structure)

| Module | Rôle | Statut |
|---|---|---|
| `config.py` | résolveur générique des racines (COCKPIT_HOME / PROJECTS_ROOT) | ✅ |
| `core/run.py` | exécution locale subprocess (seam transport réécrit, #2) | ✅ |
| `core/ids.py` | slugs kebab validés + uuid | ✅ |
| `core/fs.py` | `safe_path` borné (#4) + JSONL | ✅ |
| `db/schema.py` | schéma SQLite (contrat figé, 4 tables) | ✅ |
| `db/store.py` | connexion + migration idempotente | ✅ |
| `cli.py` | câblage argparse complet + figé (handlers → stubs, imports paresseux) | ✅ |

## Couches (à porter)

| Module | Source vault | Refactor | Statut |
|---|---|---|---|
| `git/backend.py` | (interface neuve) | #1 (frontière DI) | ✅ signatures figées |
| `git/internal.py` | `git_ops.py` | #2 / #6 / #7 / #12 | ✅ |
| `git/github.py` | — | (P6) | ⏸ |
| `projects/registry.py` | `routers/projects.py` | #1 / #4 | ⬜ |
| `roadmap/model.py` | (schéma roadmap.yaml) | #9 | ⬜ |
| `roadmap/resolver.py` | `lib/vault_tasks.py` | #9 | ⬜ |
| `dispatch/worktree.py` | `routers/devserver.py` (broker) + `worktree_dispatch.py` | #7 / #12 | ⬜ |
| `dispatch/worker.py` | `routers/orchestrator.py` (dispatch) | #2 / #3 | ⬜ |
| `dispatch/jobs.py` | `routers/dispatch_ws.py` | #5 | ⬜ |
| `gate/review.py` | `loops/review_state.py` | #13 | ⬜ |
| `gate/verify.py` | `loops/feature_verify.py` | #10 / #13 | ⬜ |
| `gate/merge.py` | `loops/worker_merge_gate.py` + `orchestrator.py` (merge) | #3 / #8 | ⬜ |
| `daemon/app.py` | `server.py` (`build_app`) | #1 / #3 | ⬜ |
| `daemon/routes/*` | `routers/*` | #3 | ⬜ |
| `terminal/pty.py` | `terminal.py` (pty_bridge) | #2 / #4 | ⬜ |

## Definition of Done (repo cockpit)

- [ ] `pip install -e .` puis boucle CLI end-to-end : `project create → roadmap add-feature → task add →
      dispatch → worktree isolé → gate → merge → cleanup worktree`.
- [ ] Gate « pas de task, pas de dispatch » vérifié (dispatch refusé si feature sans task).
- [ ] Multi-worktree : ≥2 features en parallèle, chacune son worktree, isolation prouvée (flock).
- [ ] Merge : creds+identité injectés, cleanup worktree AVANT `branch -D`, writeback prouvé par signaux.
- [ ] Gates : review Tier-1 lié SHA (non-overridable) + feature-verified Tier-1.5 (fail-closed, N/A-safe).
- [ ] Daemon FastAPI : DI explicite (aucun god-module), routers découpés par domaine.
- [ ] `ruff` + `mypy` + `pytest` + smoke réponse **verts**. Portabilité WSL prouvée (Debian best-effort).

### Structure (fait)
- [x] Squelette src-layout + socle fonctionnel (config/core/db/cli).
- [x] `docs/` complètes (architecture / schema-contract / weak-points / multi-os / specs×6).
- [x] `.claude/` vendoré (CLAUDE.md, persona, skills, hook, templates, settings) + `pyproject` + tests-fumée.
