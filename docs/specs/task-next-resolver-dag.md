# spec — task-next-resolver (DAG depends_on)

> Contrainte distillée (vault `decisions/meta/2026-07-01--tasks-phase-sequencing.md`,
> `2026-07-01--task-graph-reliability.md`, `2026-06-19--tasks-data-layer-refonte.md`).
> Cible : `roadmap/resolver.py`. Refactor #9.

## Problème tranché

Faux-`next` : une phase tardive ressortait **READY** alors que sa devancière n'était pas faite, parce que
la séquence de phases ne vivait qu'en **prose** (checklist/`next:`) jamais parsée ; le seul lien machine
était le `depends_on` explicite, incomplet. Statut 100 % déclaratif, jamais corroboré à git.

## Règles verrouillées

- Le **graphe est la seule autorité de séquencement** ; on ne fige jamais un statut dans une spec.
- **Inter-feature** : manifeste **`phases:`** (liste ORDONNÉE ; chaque étape = liste d'ids parallèles) ;
  chaque membre de l'étape N reçoit, **en union**, les étapes `< N` comme dépendances. **Intra-feature** :
  `depends_on` explicite reste roi. Dérivé = **union, jamais écrasement**.
- **Point de dérivation unique** en fin de chargement (tous les consommateurs passent par là) → zéro
  logique dupliquée chez les lecteurs. La **donnée** est enrichie, pas la décision (`classify` inchangé).
- **Opt-in rétro-compatible** : sans `phases:`, sortie identique au `depends_on` seul.
- **Classification à ordre figé** : `CYCLE → ERROR (dep dangling) → BLOCKED_DEPS → DEFERRED → READY`. Le
  **statut est la source de vérité**, pas le dossier.
- **Ordre total** : priorité effective transitive `eff_prio(t) = min(prio propre, min sur dépendants)` +
  tri topologique lexicographique avec tiebreak `id` final (zéro ex-æquo). `PRIORITIES = (P0,P1,P2,P3)`.
- **Triggers** = grammaire fermée vérifiable (`glob_count`/`task_done`/`path_exists`/`date_after`/`manual`,
  composition 1 niveau `any_of`/`all_of`), **date injectée** (déterminisme). **Fail-soft** : non
  vérifiable → `DEFERRED` + warning, jamais READY ni crash. Moteur **read-only, déterministe, zéro LLM**.

## Invariants de test (à encoder dans cockpit)

- **Repro avant fix** : une phase tardive dont le prédécesseur n'est pas `done` sort **BLOCKED_DEPS**,
  exclue du `next` (le test bascule par enrichissement de données, sans toucher au classifieur).
- `slug ≠ id` (arête `depends_on` cassée en silence) → **ERREUR** de validation, pas warning.
- Priorité hors vocab `P0-P3` → warning fail-soft, jamais crash.
- Un P2 qui débloque un P1 doit **remonter** (`eff_prio` ; ne pas régresser vers un tri-priorité plat).
- Trigger en prose legacy / glob non scopé (`..`/absolu) / `when` hors-enum → `DEFERRED`, jamais READY.
