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
`src/forgemaster/gate/toolchain.py:284` · appelé par `toolchain.cli_dispatch` (`forgemaster gate toolchain <feature>`)
Lance, dans le worktree, les steps des groupes à la fois **présents** ET **déclenchés** par le diff, dans l'ordre,
en s'arrêtant au 1ᵉʳ rouge. Ne lève **jamais** (timeout/binaire absent → step rouge). Un trigger déclenché mais
**non couvert** par une unité de gate → **step rouge synthétique** (« toolchain non montable »), jamais un drop
silencieux ni un vert à 0 step. Diff **sans source** (prose ⊕ verrous de dépendances ⊕ assets, ou diff vide) → `[]`
(vacuously vert). **Dédup** : deux steps identiques (`name` + `cmd` + `cwd`) issus de groupes différents ne sont
joués qu'une fois — un projet qui déclare sa commande de gate ne la paie pas deux fois quand le diff déclenche
aussi sa route connue. `env`, s'il est fourni, **REMPLACE** l'environnement des steps : c'est par là que
l'appelant préfixe `tools/bin` au PATH pour résoudre ruff/mypy/pytest/npm sur un hôte frais (`None` = héritage
passif, conservé pour les tests).

## toolchain.detect_groups() — Tier-0 déterministe
`src/forgemaster/gate/toolchain.py:113`
Quatre groupes **présents (couvrables)** dans le worktree. Trois par **convention** : `front` (`web/` ou racine
portant un script npm `gate`), `backend-node` (`server/` ou racine unifié), `backend` (`pyproject.toml` racine).
Le quatrième, `declared`, quand le projet **déclare** sa toolchain (`[bundle.gate]` du `.forgemaster/bundle.toml`) —
c'est la porte de sortie d'un projet dont la stack n'est aucune des trois routes connues. **Descriptif** —
l'autorité du RUN reste `_steps_for` (qui porte le fail-closed sur trigger non couvert). Un `package.json`
racine avec script `gate` couvre `web/` ET `server/` (workspaces).

## toolchain.applicable_triggers() — Tier-0 déterministe
`src/forgemaster/gate/toolchain.py:167` · appelé par `run_toolchain`, `status`, `cli_dispatch`
Groupes **déclenchés par le diff seul** (sans worktree — le `GET /api/gate` poll-é n'a que le diff sous la main),
dans l'ordre `front → backend-node → backend → declared` : `web/` touché → `front` ; un fichier node (`.ts/.js…`)
**hors `web/`** → `backend-node` ; un `*.py` → `backend`. C'est la source d'autorité de l'**applicabilité** côté
`status`/`evaluate_gate` ; la **montabilité**, elle, appartient à `_steps_for`.
**Cadrage POSITIF** (renversement du 2026-07-31) : les trois routes connues déclenchent leur groupe, et **tout
résidu de source déclenche `declared`**. `[]` — donc Tier-0 natif **N/A** — est désormais réservé aux diffs
**sans source** : prose, verrous de dépendances, assets binaires, diff vide. Un langage inconnu ne peut plus
sortir en N/A : le seul veto non-overridable de la pile ne s'éteint plus en silence.

## review.partition_findings() — Tier-1 review
`src/forgemaster/gate/review.py:123` · appelé par `review.build_verdict`
Sépare `(kept, rejected)` par la garde déterministe **`evidence ⊂ diff`** (anti-hallucination, fail-closed) : un
finding dont la citation verbatim n'apparaît pas dans une ligne **ajoutée** (`+`) du diff `base...HEAD` est
**rejeté** et porte `reject_reason` (`pas-de-file:line` / `pas-de-citation` / `file-absent-du-diff` /
`citation-absente-du-diff`). PUR (aucun git/réseau) — le diff lui est fourni.

## review.evidence_in_diff() — Tier-1 review
`src/forgemaster/gate/review.py:118`
Prédicat unitaire : `True` ssi la citation verbatim d'**un** finding apparaît dans une ligne ajoutée du diff
`base...HEAD` (délègue à la même logique que `partition_findings`, réponse booléenne). La comparaison est
normalisée (strip + collapse des espaces) → robuste à l'indentation ; c'est le CONTENU cité qui compte, pas le
numéro de ligne.

## review.build_verdict() — Tier-1 review
`src/forgemaster/gate/review.py:138` · appelé par `review.write_verdict`
PUR (aucune I/O). Assemble le verdict `review-gate-v2` : applique la garde `evidence ⊂ diff` si `diff_text` fourni
(non citables → `rejected[]`, **hors** counts/gate), dérive les `counts` (🔴/🟡/🟣), fige `reviewed_sha`/`ts`
**fournis par l'appelant** (jamais de fallback git/horloge implicite → c'est ce qui le rend pur). Instance Tier-1
de la forme `build_verdict` partagée.

## review.gate_blocking() — Tier-1 review
`src/forgemaster/gate/review.py:229` · appelé par `review.status`
`True` ssi le verdict porte au moins un **🔴** reviewer. Un 🔴 **bloque** le merge — mais est **levable** par un
override humain explicite et tracé (`t1_override` dans `compose_merge_decision`), contrairement au filet Tier-0 qui,
lui, n'est jamais overridable. Les 🟡/🟣 sont consultatifs (surfacés, non bloquants).

## verify.has_ui() / has_visual_change() — Tier-1.5 feature-verified
`src/forgemaster/gate/verify.py:48` (has_ui) · `:95` (has_visual_change, **le trigger réel du gate**)
`has_ui` est un prédicat **coarse**, par NOM seul : `True` ssi un fichier du diff a un suffixe front
(`.tsx/.jsx/.vue/.svelte`) ou un chemin de page/composant. **Ce n'est PAS lui que `evaluate_gate` appelle** — il
est conservé comme référence et pour les tests. Le trigger du gate est `has_visual_change`, **hybride nom +
contenu** : les fichiers de STYLE et les dossiers RENDUS (`pages/`, `components/`, `routes/`, `layouts/`,
`views/`, `content/`) sont visuels par nom (ce qui couvre aussi les suppressions) ; un fichier front **ailleurs**
(`App.tsx` racine, `lib/`) n'est visuel que si ses lignes **ajoutées** introduisent du markup (`</`, `/>`,
`className=`, `class=`). Un `.tsx` de câblage ou de type n'exige donc PAS de preuve — c'est cette distinction qui
évite d'imposer un Tier-1.5 sur du contrat typé. Changement visuel → preuve e2e **obligatoire** ; sinon Tier-1.5
= **N/A** (non bloquant).

## verify.verify_target() — Tier-1.5 feature-verified
`src/forgemaster/gate/verify.py:255` · appelé par `verify.cli_dispatch` (`forgemaster gate verify <feature>`)
Prouve **une** cible via le runner Node (Playwright, résolu par `FORGEMASTER_VERIFY_RUNNER` ou
`<home>/runners/render_check.js`) : charge l'URL, vérifie les marqueurs attendus dans le DOM. Ne lève **JAMAIS** —
runner absent / node ko / browser ko / timeout / sortie non-JSON → `{ok: False, error: …}` (**fail-closed** : un
target non prouvé n'est pas un target vert). L'`env` est celui de `tools.tools_env` : le PATH systemd minimal du
daemon n'expose pas `node` (nodeenv sous `tools/bin`), sans ce préfixe le runner est mort-né.
Trois preuves plus fortes que « le marqueur est là », toutes optionnelles : **jalon jouable** — si
`after_markers` est déclaré, le runner joue `clicks` puis exige ces marqueurs **après** le geste ET assert
qu'ils étaient **absents at-rest** (`pre_present` non vide = transition non prouvée = rouge) ; **plancher
canvas** — si `canvas` est déclaré, l'élément doit avoir *peint* (pixels non uniformes) ; et `cookies` /
`wait_for_text` pour atteindre une surface derrière une précondition. C'est l'assert d'absence at-rest qui
distingue une transition prouvée d'un marqueur qui était déjà là.

## verify.build_verdict() — Tier-1.5 feature-verified
`src/forgemaster/gate/verify.py:305` · appelé par `verify.write_verdict`
PUR. Assemble le verdict `feature-verify-v2` (`CONTRACT_VERSION`, preuve deux-temps) : `ok=True` ssi **≥1 cible
ET toutes ok** (jamais blanchi par 0 cible — pas de vert par absence de preuve). Expose `n_targets`/`n_failed`.
Une cible « jalon jouable » n'est ok que si son `after_marker` apparaît **après** le geste et était **absent**
at-rest (`pre_present` vide). `sha`/`ts` injectés par l'appelant
(pas de git/horloge implicite). Instance Tier-1.5 de la forme `build_verdict` partagée ; `gate_blocking` (voisin)
bloque dès `n_failed > 0` ou `ok=False`.

## merge.compose_merge_decision() — merge (cœur PUR)
`src/forgemaster/gate/merge.py:58` · appelé par `evaluate_gate`
La **chaîne d'autorité**, PURE. Conditions CUMULATIVES : **Tier-0** propre (0 🔴 déterministe,
**non-overridable**) ; **Tier-0 natif** propre si `native_status['applicable']` (veto déterministe
**non-overridable**, N/A si absent) ; **Tier-1 présent + FRAIS + PASS** (garde de process non-overridable ; un 🔴
levable par `t1_override` tracé) ; **Tier-1.5** présent + frais + rendu prouvé **si `ui_touched`** (levable par
`t15_override`) ; et **`human_go is True`**. `gate_green` et `human_go` sont **séparés** : gate vert sans go →
`hold`. Fail-CLOSED : Tier-1/1.5 absent/périmé → hold.
Deux N/A **symétriques**, à ne pas confondre avec un blanchiment : le Tier-1 n'est exigé que si le diff porte de
la **source exécutable** (`code_touched`, dérivé de `toolchain.has_reviewable_code`) — un livrable **docs-only**
n'a rien à reviewer, exactement comme le Tier-1.5 hors UI et le Tier-0 natif sans toolchain. Un cinquième axe,
**woaw** (esthétique), est composé quand l'UI est touchée mais reste **advisory** : il ne bloque pas — le
confondre avec un fail-closed serait l'erreur coûteuse. Le retour porte aussi `refixable` (rouge de code frais
qu'un worker peut corriger, cf. `dispatch.refix`).

## merge.evaluate_gate() — merge (évaluation sans mutation)
`src/forgemaster/gate/merge.py:203` · appelé par `run_merge` et par le GET gate (preview)
**Source unique** de l'évaluation, **ne mute rien** : résout le SHA de HEAD de la branche, **lit** les verdicts
Tier-1 (`review.status`), Tier-1.5 (`verify.status`) et woaw (`woaw.status`, advisory) ancrés sur ce SHA, tire le
diff **une seule fois** puis en dérive `ui_touched` (`verify.has_visual_change`, hybride nom+contenu) et
`code_touched` (`toolchain.has_reviewable_code` — docs-only ⇒ Tier-1 N/A), lit le Tier-0 natif (injecté en test,
sinon **lu** via `toolchain.status` — **jamais exécuté ici**, le GET est poll-é et idempotent), puis compose.
`head_sha`/`decision` = `None` si la feature n'a pas de branche (jamais dispatchée). Les overrides
(`t1_override`/`t15_override`) traversent jusqu'à `compose_merge_decision` : c'est ici qu'ils entrent, pas au
merge. Le GET l'expose avec `human_go=False` → `hold`, sans jamais POSTer de merge.

## merge.run_merge() — merge (orchestration IMPURE)
`src/forgemaster/gate/merge.py:248` · appelé par `merge.cli_dispatch` (`forgemaster merge <feature>`)
Compose le gate via `evaluate_gate` puis, **seulement si `decision['allow']`** (gate vert **ET** `human_go`),
exécute internal-first : **réalignement anti-stale-base** si `dev` n'est plus ancêtre de la branche (un merge de
sibling l'a fait avancer pendant le drain parallèle) — rebase linéaire qui préserve les commits worker et
**ré-ancre `head_sha`** ; puis ff `feature→dev`, ff `dev→main` (main-suit-dev), writeback identité (token injecté
à l'usage via la réf de credential, 0 secret en DB), **`worktree.release` AVANT `delete_branch`**, clôture DB.
Garde de sûreté : `allow` faux → **aucune mutation git** (gate vert sans go → `hold`) ; merge irréversible →
fail-CLOSED. Une nuance qui compte : un gate **rouge** (≠ vert-sans-GO) émet une **alerte `gate_red`** bloquante
(`blocker_tier` sur le premier blocker) — c'est la seule écriture d'un `allow` faux, et elle est délibérée.
Une feature planifiée mais **jamais dispatchée** n'a pas de branche : outcome de domaine propre
(`decision=None`, « rien à merger »), pas un traceback.

## merge._close_feature_tasks() — merge (writeback DB)
`src/forgemaster/gate/merge.py:190` · appelé par `run_merge` (post-merge)
Writeback DB après un merge réussi : la feature passe `merged`, ses tasks **landées** (`in_progress`) passent
`done` ; retourne leurs slugs. Les tasks jamais dispatchées (`todo`) restent telles quelles (surfacées comme
`pending_tasks` par `run_merge`).

## Zones non détaillées
- Le quatuor `state_path`/`write_verdict`/`read_verdict`/`is_fresh`/`status` répété par tier (`toolchain`,
  `review`, `verify` — forme identique, verdict SHA-bound sous `settings.home/gate/<projet>/<feature>/`) ;
  `cli_dispatch` par module (routes `forgemaster gate toolchain|review|verify` et `forgemaster merge`).
- **`toolchain.py` — les prédicats de périmètre** : `touches_front`/`touches_node_backend`/`touches_py`/
  `touches_undeclared_source` (les quatre déclencheurs lus par `applicable_triggers`), `is_tier0_source`
  (qu'est-ce qu'une source exécutable), `is_docs_only` et `has_reviewable_code` (le prédicat qui rend le Tier-1
  N/A sur un livrable de prose). Un seul endroit décide « ce diff porte-t-il du code » ; ces quatre lignes le
  disent, leurs corps sont des tests d'extension/préfixe lisibles inline.
- **`verify.py` — les lecteurs de contrat** : `read_verify_contract` (le `verify.json` du worktree),
  `read_declared_markers`, `build_payload` (la charge envoyée au runner), `runner_path` (résolution du runner
  Node), `autoverify_feature` (la passe Tier-1.5 automatique post-travail).
- **`merge.py`** — `blocker_tier` (classe un blocker par tier, pour l'alerte `gate_red`).
