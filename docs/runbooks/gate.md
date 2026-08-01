# gate — runbook (gate multi-tier fail-closed : Tier-0 déterministe → Tier-1 review → Tier-1.5 feature-verified → merge (SHA-frais))

Le gate est une chaîne d'autorité à **quatre étages** : **Tier-0** (`toolchain`, déterministe non-overridable :
ruff/mypy/pytest + `npm run gate`), **Tier-1** (`review`, verdict de revue), **Tier-1.5** (`verify`,
feature-verified — preuve e2e que le rendu s'affiche), puis **`merge`** qui compose les trois et exige un **GO
humain**. Chaque tier écrit un **verdict lié au SHA** de la branche de feature via une **forme partagée**
(`build_verdict`/`write_verdict`/`state_path` sous `settings.home`, `read_verdict`/`is_fresh`/`gate_blocking`/
`status` en lecture) : le RUN écrit le verdict, le gate le **lit** seulement. Invariant transverse : **fail-CLOSED**
— verdict absent, périmé (`reviewed_sha ≠ HEAD`) ou rouge → **bloque** ; la fraîcheur se mesure au **SHA de HEAD**,
jamais au mtime ; et `run_merge` ne mute **rien** sans `human_go is True` (le LLM ne merge jamais seul).

## toolchain.run_toolchain() — Tier-0 déterministe
`src/cockpit/gate/toolchain.py:281` · appelé par `toolchain.cli_dispatch` (`cockpit gate toolchain <feature>`)
Lance, dans le worktree, les steps des groupes à la fois **présents** ET **déclenchés** par le diff, dans l'ordre,
en s'arrêtant au 1ᵉʳ rouge. Ne lève **jamais** (timeout/binaire absent → step rouge). Un trigger déclenché mais
**non couvert** par une unité de gate → **step rouge synthétique** (« toolchain non montable »), jamais un drop
silencieux ni un vert à 0 step. Diff sans trigger (doc-only) → `[]` (vacuously vert).

## toolchain.detect_groups() — Tier-0 déterministe
`src/cockpit/gate/toolchain.py:112`
Groupes de toolchain **présents (couvrables)** dans le worktree, par **convention** (pas de config déclarative) :
`front` (`web/` ou racine portant un script npm `gate`), `backend-node` (`server/` ou racine unifié), `backend`
(`pyproject.toml` racine). **Descriptif** — l'autorité du RUN reste `_steps_for` (qui porte le fail-closed sur
trigger non couvert). Un `package.json` racine avec script `gate` couvre `web/` ET `server/` (workspaces).

## toolchain.applicable_triggers() — Tier-0 déterministe
`src/cockpit/gate/toolchain.py:165` · appelé par `run_toolchain`, `status`, `cli_dispatch`
Groupes **déclenchés par le diff seul** (sans worktree), dans l'ordre `front → backend-node → backend` : `web/`
touché → `front` ; un fichier node (`.ts/.js…`) **hors `web/`** → `backend-node` ; un `*.py` → `backend`. C'est la
source d'autorité de l'**applicabilité** côté `status`/`evaluate_gate` : pas de trigger → Tier-0 natif **N/A**.

## review.partition_findings() — Tier-1 review
`src/cockpit/gate/review.py:123` · appelé par `review.build_verdict`
Sépare `(kept, rejected)` par la garde déterministe **`evidence ⊂ diff`** (anti-hallucination, fail-closed) : un
finding dont la citation verbatim n'apparaît pas dans une ligne **ajoutée** (`+`) du diff `base...HEAD` est
**rejeté** et porte `reject_reason` (`pas-de-file:line` / `pas-de-citation` / `file-absent-du-diff` /
`citation-absente-du-diff`). PUR (aucun git/réseau) — le diff lui est fourni.

## review.evidence_in_diff() — Tier-1 review
`src/cockpit/gate/review.py:118`
Prédicat unitaire : `True` ssi la citation verbatim d'**un** finding apparaît dans une ligne ajoutée du diff
`base...HEAD` (délègue à la même logique que `partition_findings`, réponse booléenne). La comparaison est
normalisée (strip + collapse des espaces) → robuste à l'indentation ; c'est le CONTENU cité qui compte, pas le
numéro de ligne.

## review.build_verdict() — Tier-1 review
`src/cockpit/gate/review.py:138` · appelé par `review.write_verdict`
PUR (aucune I/O). Assemble le verdict `review-gate-v2` : applique la garde `evidence ⊂ diff` si `diff_text` fourni
(non citables → `rejected[]`, **hors** counts/gate), dérive les `counts` (🔴/🟡/🟣), fige `reviewed_sha`/`ts`
**fournis par l'appelant** (jamais de fallback git/horloge implicite → c'est ce qui le rend pur). Instance Tier-1
de la forme `build_verdict` partagée.

## review.gate_blocking() — Tier-1 review
`src/cockpit/gate/review.py:229` · appelé par `review.status`
`True` ssi le verdict porte au moins un **🔴** reviewer. Un 🔴 **bloque** le merge — mais est **levable** par un
override humain explicite et tracé (`t1_override` dans `compose_merge_decision`), contrairement au filet Tier-0 qui,
lui, n'est jamais overridable. Les 🟡/🟣 sont consultatifs (surfacés, non bloquants).

## verify.has_ui() — Tier-1.5 feature-verified
`src/cockpit/gate/verify.py:48` · appelé par `gate/merge.evaluate_gate`
Heuristique **UNIQUE** de détection de surface UI (partagée `gate/merge` ↔ `gate/verify`, pas deux copies qui
dérivent) : `True` ssi un fichier du diff a un suffixe front (`.tsx/.jsx/.vue/.svelte`) ou un chemin de
page/composant (`/web/src/`, `/src/pages/`, `/src/components/`). UI touchée → la preuve e2e Tier-1.5 devient
**obligatoire** ; sinon Tier-1.5 = **N/A** (non bloquant).

## verify.verify_target() — Tier-1.5 feature-verified
`src/cockpit/gate/verify.py:253` · appelé par `verify.cli_dispatch` (`cockpit gate verify <feature>`)
Prouve **une** cible via le runner Node (Playwright, résolu par `COCKPIT_VERIFY_RUNNER` ou
`<home>/runners/render_check.js`) : charge l'URL, vérifie les marqueurs attendus dans le DOM. Ne lève **JAMAIS** —
runner absent / node ko / browser ko / timeout / sortie non-JSON → `{ok: False, error: …}` (**fail-closed** : un
target non prouvé n'est pas un target vert).

## verify.build_verdict() — Tier-1.5 feature-verified
`src/cockpit/gate/verify.py:303` · appelé par `verify.write_verdict`
PUR. Assemble le verdict `feature-verify-v2` (`CONTRACT_VERSION`, preuve deux-temps) : `ok=True` ssi **≥1 cible
ET toutes ok** (jamais blanchi par 0 cible — pas de vert par absence de preuve). Expose `n_targets`/`n_failed`.
Une cible « jalon jouable » n'est ok que si son `after_marker` apparaît **après** le geste et était **absent**
at-rest (`pre_present` vide). `sha`/`ts` injectés par l'appelant
(pas de git/horloge implicite). Instance Tier-1.5 de la forme `build_verdict` partagée ; `gate_blocking` (voisin)
bloque dès `n_failed > 0` ou `ok=False`.

## merge.compose_merge_decision() — merge (cœur PUR)
`src/cockpit/gate/merge.py:58` · appelé par `evaluate_gate`
La **chaîne d'autorité**, PURE (portée verbatim du legacy). Conditions CUMULATIVES : **Tier-0** propre (0 🔴
déterministe, **non-overridable**) ; **Tier-0 natif** propre si `native_status['applicable']` (veto déterministe
**non-overridable**, N/A si absent) ; **Tier-1 présent + FRAIS + PASS** (garde de process non-overridable ; un 🔴
levable par `t1_override` tracé) ; **Tier-1.5** présent + frais + rendu prouvé **si `ui_touched`** (levable par
`t15_override`) ; et **`human_go is True`**. `gate_green` et `human_go` sont **séparés** : gate vert sans go →
`hold`. Fail-CLOSED : Tier-1/1.5 absent/périmé → hold.

## merge.evaluate_gate() — merge (évaluation sans mutation)
`src/cockpit/gate/merge.py:203` · appelé par `run_merge` et par le GET gate (preview)
**Source unique** de l'évaluation, **ne mute rien** : résout le SHA de HEAD de la branche, **lit** les verdicts
Tier-1 (`review.status`) et Tier-1.5 (`verify.status`) ancrés sur ce SHA, dérive `ui_touched` (`verify.has_ui`) et
le Tier-0 natif (injecté en test, sinon **lu** via `toolchain.status` — **jamais exécuté ici**, le GET est poll-é
et idempotent), puis compose. `head_sha`/`decision` = `None` si la feature n'a pas de branche (jamais dispatchée).
Le GET l'expose avec `human_go=False` → `hold`, sans jamais POSTer de merge.

## merge.run_merge() — merge (orchestration IMPURE)
`src/cockpit/gate/merge.py:248` · appelé par `merge.cli_dispatch` (`cockpit merge <feature>`)
Compose le gate via `evaluate_gate` puis, **seulement si `decision['allow']`** (gate vert **ET** `human_go`),
exécute internal-first : ff `feature→dev`, ff `dev→main` (main-suit-dev), writeback identité (token injecté à
l'usage via la réf de credential, 0 secret en DB), **`worktree.release` AVANT `delete_branch`**, clôture DB.
Garde de sûreté : `allow` faux → **aucune mutation** (gate vert sans go → `hold`) ; merge irréversible → fail-CLOSED.

## merge._close_feature_tasks() — merge (writeback DB)
`src/cockpit/gate/merge.py:190` · appelé par `run_merge` (post-merge)
Writeback DB après un merge réussi : la feature passe `merged`, ses tasks **landées** (`in_progress`) passent
`done` ; retourne leurs slugs. Les tasks jamais dispatchées (`todo`) restent telles quelles (surfacées comme
`pending_tasks` par `run_merge`).

## Zones non détaillées
- Le quatuor `state_path`/`write_verdict`/`read_verdict`/`is_fresh`/`status` répété par tier (`toolchain`,
  `review`, `verify` — forme identique, verdict SHA-bound sous `settings.home/gate/<projet>/<feature>/`) ;
  `cli_dispatch` par module (routes `cockpit gate toolchain|review|verify` et `cockpit merge`).
