# git — runbook (git internal-first : opérations parse/sync/push, branches protégées, creds writeback, identité)

La couche `git/` du cockpit est **internal-first** : un contrat `GitBackend` (frontière injectable, seam DI) dont l'unique adapter V1 est `InternalGit` — un `git -C <repo>` **local, zéro réseau, sans shell** sur un SoT bare ; le backend GitHub reste différé (P6). Deux invariants non négociables la traversent : les branches `main`/`dev` sont **protégées** (jamais poussées en direct, jamais ff sur une branche sortie dans un worktree actif) ; les credentials (token) et l'identité git sont **injectés par env le temps de l'op puis retirés** — jamais persistés, jamais en argv, jamais loggés (spec merge-writeback). Les mutations de worktree sont sérialisées par un `flock` inter-process sur le git-dir du SoT.

## GitBackend — le contrat git figé (frontière injectable)
`src/cockpit/git/backend.py:22` · `Protocol` runtime-checkable · implémenté par `InternalGit`, honoré (stub) par `GitHubGit` · consommé par dispatch / gate / merge sans connaître l'implémentation
Frontière pure, **aucune logique** — juste les signatures qui gouvernent worktree, branches, merge et writeback (correctif #1 : DI, plus de god-module). Trois décisions verrouillées y sont gravées : `add_worktree(..., base)` ancre `-B <branch> <base>` (jamais `checkout -B base`) ; `remove_worktree` est distinct de `delete_branch` et doit être appelable **avant** lui (spec worktree-cleanup) ; `merge_writeback(..., creds_ref, identity)` prend une **référence** de secret + une identité à injecter, jamais le secret en clair.

## InternalGit — adapter GitBackend sur repo bare local (zéro réseau)
`src/cockpit/git/internal.py:238` · implémente GitBackend · instancié par l'orchestration (gate/merge, dispatch)
Adapter V1 par défaut (décision internal-first). Le SoT est un bare **amorcé** (`dev`+`main` seedés dès `init_sot`, sinon `worktree add --base dev` échouerait) ; le miroir GitHub est **best-effort et opt-in** (jamais bloquant — spec forge-sot-local). Un `cred_resolver` optionnel (`Callable[[str], str]`, **total** : renvoie `''` si absent/illisible, ne lève jamais) est injecté par l'appelant pour résoudre un `credential_ref` opaque → token, le temps du writeback/fetch seulement. Ce paquet **n'importe jamais `cockpit.secrets`** : la couche git reste une primitive, la policy de résolution vit chez l'appelant. Les méthodes de sync/reconcile (`remote_divergence`, `reconcile`, `sync_tracking`) et de lecture read-only (`branches`, `log`, `ls_tree`, `read_blob`, `commit_detail`…) composent les primitives ci-dessous ; le détail de chacune vit dans leur docstring.

## parse_status() — porcelain v2 → dict machine-lisible
`src/cockpit/git/internal.py:85` · parser **PUR** · appelé par `InternalGit.status()` (`status --porcelain=v2 -b`)
Parse la sortie porcelain v2 en `{branch, upstream, ahead, behind, files, clean}`. Lit les lignes d'en-tête `# branch.head/upstream/ab`, puis les entrées de fichiers (tags `1`/`2` = tracked, `u` = unmerged, `?` = untracked), chacune projetée par `_file_entry` en `{path, index, worktree, staged}`. `clean` = aucune entrée. Aucune exécution git ici — fonction de transformation pure, testable sur une string.

## parse_log() — oneline → [{sha, subject}]
`src/cockpit/git/internal.py:130` · parser **PUR** · appelé par `InternalGit.log()` (`log --oneline`)
Découpe chaque ligne `<sha> <subject>` sur le premier espace (`partition`), ignore les lignes vides. Pur, sans git.

## classify_push_error() — stdout/stderr d'un push → catégorie
`src/cockpit/git/internal.py:141` · classifieur **PUR** · consommé par l'orchestration de push / `clone_sot`
Normalise le couple `(stdout, stderr)` en minuscules et le range en `'behind'` (non-fast-forward, fetch first, tip behind), `'pat-scope'` (write access not granted, 403, permission denied to), `'auth'` (could not read username/password, authentication failed, terminal prompts disabled, no credential) ou `'other'`. Sert à donner une raison **honnête** à un échec réseau (le PAT est trop étroit vs l'auth a échoué vs la branche est en retard) plutôt qu'un blob opaque.

## is_protected_branch() — garde des branches main/dev
`src/cockpit/git/internal.py:158` · prédicat **PUR** · `PROTECTED_BRANCHES = ("main", "dev")` (l. 31)
`True` si la branche est protégée. **Invariant** : les branches protégées ne sont jamais travaillées ni poussées en direct — le travail vit sur une feature branch, `main`/`dev` n'avancent que par merge ff piloté par l'orchestration. `current_branch()` fournit l'entrée de ce garde côté discipline de push.

## _rollup_sync_state() — états par-branche → état projet
`src/cockpit/git/internal.py:176` · rollup **PUR** · au-dessus de `_branch_sync_state()` (l. 142) · consommé par `remote_divergence`
`_branch_sync_state(ahead, behind)` classe **une** branche : `ahead∧behind` → `diverged`, `ahead` → `local_ahead` (SoT en avance, à pousser), `behind` → `remote_ahead` (à ff), sinon `synced`. `_rollup_sync_state` remonte au projet : une branche divergée **ou** deux branches tirant en sens opposés (l'une local-avance, l'autre remote-avance) = `diverged` global (pas de réconciliation ff unique) ; sinon l'état non-`synced` dominant. Un input vide reste `synced` seulement si l'appelant a bien fetché (sinon la dégradation `no_mirror`/`unreachable` est posée en amont) — **jamais de 0/0 faux-vert**.

## writeback_env() — identité git injectée (auteur + committer)
`src/cockpit/git/internal.py:194` · pur (compose un env, **ne mute pas `os.environ`**) · appelé par `merge_writeback`, `commit_worktree`, `_seed_base`
Compose un env à partir d'une `base` (défaut `os.environ`) en surchargeant `GIT_AUTHOR_*`/`GIT_COMMITTER_*` avec l'identité `(name, email)`. **Invariant creds/identité éphémères** : l'env est ponctuel, le temps de l'op, jamais persisté dans un `.gitconfig` — corrige la cause racine « empty ident name ». L'identité elle-même vient de `resolve_identity`.

## credential_env() — token de push HTTPS injecté par env
`src/cockpit/git/internal.py:207` · pur (**ne mute ni `base` ni `os.environ`**) · composé au-dessus de `writeback_env` par `merge_writeback`/`_authed_env`
Injecte un token GitHub pour un push/fetch HTTPS **uniquement dans l'env du process git enfant**, via `GIT_CONFIG_KEY_n`/`VALUE_n` (`url.https://x-access-token:<token>@github.com/.insteadOf` → `https://github.com/`). **Invariant creds-éphémères, cœur de la couche** : le token n'est jamais écrit dans un `.gitconfig`, jamais dans l'argv (`git push … --all`) — seulement dans l'env transitoire. `GIT_TERMINAL_PROMPT=0` fait échouer bruyamment plutôt que pendre sur un prompt. Compose au-dessus d'un `GIT_CONFIG_COUNT` déjà présent sans écraser ses entrées.

## _worktree_lock() — sérialisation flock inter-process
`src/cockpit/git/internal.py:225` · contextmanager · `flock LOCK_EX` sur `<sot>/cockpit-worktree.lock` (`_LOCK_NAME`, l. 32) · enveloppe `add_worktree`/`remove_worktree`
Sérialise **toute** mutation de worktree par un `flock` exclusif posé sur le git-dir du bare (le dossier du SoT). Couvre des process/relais **concurrents** — contrairement à un lock in-process qui ne verrouillerait qu'un seul interpréteur (spec sot-local, refactors #7/#12).

## resolve_identity() — identité git déterministe par projet
`src/cockpit/git/identity.py:33` · **PUR** · placé sous `git/` (neutre, aucun cycle) · deux consommateurs : commit worker (`dispatch`, rôle `worker`), writeback merge (`gate/merge`, rôle `writeback`)
Dérive au runtime `(name, email)` d'une écriture git automatisée : `project` fourni → `<projet>-<base>-<rôle>` kebab + `<name>@worker.local` ; absent → fallback générique par rôle (smokes / writeback anonyme). Un projet géré obtient ses identités **sans une ligne de code**. Anti-injection : chaque segment doit matcher un kebab strict (`_SLUG_RE`) sinon `ValueError` fail-loud — les valeurs finissent dans un env git, on ne maquille pas une entrée douteuse. Identité ≠ credential : aucun secret par acteur, injectée le temps de l'op via `writeback_env`.

## GitHubGit — backend externe DIFFÉRÉ (P6)
`src/cockpit/git/github.py:13` · scaffoldé, **non implémenté** · signatures figées par `GitBackend`
Adapter GitBackend sur GitHub (clone, PR flow, PAT étroit gitignored). ⏸ **Différé P6** : en V1 `InternalGit` est le seul backend requis, GitHub reste transport/miroir best-effort. Chaque méthode lève `NotImplementedError`. Le choix de backend est **par-projet** (colonne `projects.backend`) — le contrat étant figé, l'implémentation future se branche sans toucher aux appelants.

## Zones non détaillées
- `GitOpError` (l. 42), `_git`/`_checked` (l. 46, 51), `_file_entry` (l. 60) : primitives d'exécution internes — wrapper `git -C` argv-liste sans shell (`_git` ne lève pas, l'appelant inspecte `.ok`/classifie ; `_checked` lève `GitOpError` sur échec dur), et projecteur d'entrée de status. Signal, non documentés un par un.
- Méthodes d'orchestration/lecture de `InternalGit` (seed/worktree/merge/remote/sync/reconcile/exploration read-only : `init_sot`, `clone_sot`, `add_worktree`, `merge_ff`, `merge_writeback`, `remote_divergence`, `reconcile`, `sync_tracking`, `branches`, `log`, `ls_tree`, `read_blob`, `archive`, `commit_detail`, `commit_worktree`, …) : documentées en place par leurs docstrings ; ce runbook cadre la frontière, les invariants et les primitives parse/creds/lock qui les sous-tendent.
