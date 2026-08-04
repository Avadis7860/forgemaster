# weak-points — registre de refactor (la spec du portage)

Le forgemaster **réimplémente** l'orchestrateur legacy (`services/aggregator/`), il ne le copie pas. La
cartographie (3 agents Explore, 2026-07-02) a établi que le code **utile est déjà pur** (backing modules
stdlib) mais enseveli sous des dettes structurelles. Ce fichier liste chaque **dette refusée** et le
**refactor décidé** — chaque stub (`raise NotImplementedError("port: <source> — #N")`) pointe une ligne
`#N` d'ici. C'est le pendant « quoi corriger » de `PORTING.md` (« où on en est »).

| # | Dette du legacy | Origine | Refactor décidé (forgemaster) | Emplacement cible |
|---|---|---|---|---|
| **1** | **God-module `import server`** : chaque router tape `server.proxmox_api`/`server.git_ops`/`server.audit` (couplage call-time, quasi-circulaire — `server` importe les routers). | `server.py` + tous les `routers/*` | **Injection de dépendances explicite** : chaque couche reçoit `settings` (+ deps) en argument ; aucun module-global mutable. | `daemon/app.py` (`build_app`), toutes les couches |
| **2** | **Couplage transport distant** : `resolve_ctid → ProxmoxAPI.lxc_ip → ip`, puis `devserver_run(ip,cmd) = ssh dev@ip`. | `server.py`, `routers/*` | **Un seul seam local** `core.run(argv, cwd)` (subprocess, argv liste sans shell). Plus de CT/IP/clé ssh. | `core/run.py`, `git/internal.py`, `terminal/pty.py` |
| **3** | **Monolithe** `routers/orchestrator.py` : 1650 LOC, `make_orchestrator_router` à **14 deps injectées**, mêle GET plan / POST dispatch / POST merge. | `routers/orchestrator.py` | **Découpé en 3 domaines** : plan (roadmap) / dispatch / merge, routers séparés montés par DI. | `daemon/routes/`, `dispatch/`, `gate/merge.py` |
| **4** | **Chemins d'hôte en dur** : `/home/dev` (`DEV_HOME`), `SSH_KEY_PATH`, `SSH_KNOWN_HOSTS`. | `terminal.py`, `server.py` | Racines par **config** (`FORGEMASTER_HOME`/`FORGEMASTER_PROJECTS_ROOT`) ; `safe_path(root=…)` paramétré. | `config.py`, `core/fs.py` |
| **5** | **Suivi de logs fragile** : one-liner bash `find … \| tail -F` (poll 120×1s) sur transcript distant. | `routers/dispatch_ws.py` | **Lecture incrémentale robuste** d'un log **local** (offset persistant, pas de sous-shell). | `dispatch/jobs.py` |
| **6** | **`pull` destructif** : `reset --hard` + `clean -fd` (écrase le travail local). | `routers/git.py` | Pull **non destructif** (ff-only / rebase sûr ; jamais de `reset --hard` implicite). | `git/internal.py` |
| **7** | **Split clone-worker vs clone-SoT** : le worker poussait dans un clone distinct → branche invisible du reviewer (`head-sha-introuvable`). | dispatch legacy | **Worktree attaché au SoT partagé** (`worktree add -B <cible> origin/<base>`, jamais `checkout -B base` dans un 2e wt). | `dispatch/worktree.py`, `git/internal.py` |
| **8** | **Writeback sans creds/identité** : miroir provisionné read-only + sans identité git → `push 403` + « empty ident ». *La* cause racine du merge-go, invisible aux tests à I/O fake. | `worker_merge_gate` | **Injection ciblée** `GIT_CONFIG_*`/`GIT_AUTHOR_*` le temps du writeback (jamais persisté) ; on passe la **réf** BWS, pas le secret ; couture **unique** partagée. | `git/internal.py` (`merge_writeback`), `gate/merge.py` |
| **9** | **Séquencement en prose** : la séquence de phases ne vivait qu'en checklist/`next:` non parsée → faux-`next` (phase tardive « READY » avant que la précédente soit done). | `lib/vault_tasks` | **DAG `phases:` (inter) + `depends_on` (intra) en union**, dérivé en UN point ; classification à ordre figé ; `eff_prio` transitive ; triggers = grammaire fermée fail-soft. | `roadmap/resolver.py` |
| **10** | **Gate sans preuve de rendu métier** : deploy 200 / hash OK / endpoint vert ne prouvent pas que le RÉSULTAT s'affiche (faux-vert récurrent). | (leçon transverse) | **Gate feature-verified déterministe** : marqueurs dans le DOM + screenshot, scope diff déclaré, **fail-closed**, ancré SHA, **jamais blanchi**, N/A-safe. | `gate/verify.py` |
| **11** | **Reset PR-based qui boucle** : purge ledger / close PR / `git clean` manuels ; mélange revue de code et plomberie d'état, hérite des gardes de branche. | reset legacy | **Reset = respawn vers un seed** (dev→seed local + `worktree remove` + purge ledger + push miroir) en **une** opération ; miroir best-effort. | `gate/merge.py` / (respawn, phase logique) |
| **12** | **Lock in-process** : ne couvre pas des relais/process concurrents (2 features simultanées). | dispatch legacy | **`flock` sur un fichier du `.git` PARTAGÉ** (`.git/forgemaster-worktree.lock`, fd dédié). | `dispatch/worktree.py` |
| **13** | **Chemins d'état/runner en dur** : `.claude/state/review-gate.json`, `~/.cache/ms-playwright`, `skills/playwright/render_check.js`. | `loops/review_state`, `loops/feature_verify` | Chemins par **config** (sous `settings.home`), runner e2e résolu, pas de chemin projet codé en dur. | `gate/review.py`, `gate/verify.py` |

## Non-changements délibérés (ce qui était déjà bon — porter tel quel)

- Les **backing modules purs** (`git_ops` builders + parsers + classification d'erreurs 409/502, `devserver`
  detect/render, `terminal.safe_path`/parsers, `claude_sessions`, `transcript_norm`) — stdlib seule, zéro
  proxmox : le **gold** de l'extraction, portés quasi tels quels (seul le transport change, refactor #2).
- La **classification d'erreurs git** (409 behind / 403 pat-scope / protected) de `routers/git.py`.
- Le **broker de ports mutex** (`devserver` : `port_store` + locks partagés, reconcile anti-fuite).
- L'**observateur dispatch 100 % découplé** (garde UUID anti-injection) — seul son transport de log change.
- Le **résolveur de tasks read-only déterministe** (`vault_tasks` : classification + ranking + DoD) — on
  absorbe en plus l'héritage de priorité transitif du proto (`eff_prio`), on ne régresse pas vers un tri plat.
- La **garde evidence-verbatim** du verdict de review (un finding sans citation présente dans une ligne
  ajoutée est rejeté) — anti-hallucination, conservée.
