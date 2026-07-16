# roadmap — runbook (roadmap.yaml : DAG feature/task, next dispatchable (délègue taskmap), prompt de worker, cohérence)

`.cockpit/roadmap.yaml` est le SoT projet du DAG **feature → task** : une feature = branche = worktree (unité de merge), une task = unité de dispatch séquentielle à `depends_on` explicite. `model` fait le CRUD DB + la (dé)sérialisation du contrat ; `resolver` classe/range le DAG en déléguant **tout** le séquencement au moteur générique `taskmap` (aucune copie du graphe ici) et rend la NEXT task dispatchable ; `prompt` la transforme en prompt de worker `claude -p` déterministe ; `check` gate la complétude ; `seed` sème la roadmap de lancement d'un bundle.

## model.add_feature() — insère une feature (branche + facette + DAG inter-feature)
`src/cockpit/roadmap/model.py:51` · appelé par `cli_dispatch` (`roadmap add-feature`), `seed.seed_launch_roadmap`
Crée un row feature `planned` (`branch = feature/<slug>`, `worktree_path=None`). `facet` (dispatch qui alignera le worker) est validée contre `_project_facets(project)` — vocab **du bundle du projet**, registre-driven, pas d'enum global. `blueprint` (v9) = ref STAMP brute. `depends_on` (v10) = slugs de features prérequises (DAG inter-feature). `IntegrityError` → `ValueError` (doublon). Row rendu avec `depends_on` re-décodé en liste.

## model.resolve_feature() — ref « projet/feature » → row
`src/cockpit/roadmap/model.py:81` · appelé par `add_task`, `resolver.index_for_feature`
Split `"<projet>/<feature>"` (sans `/` → `ValueError`), résout le projet via `get_project`, SELECT par `(project_id, slug)`. `None` → `KeyError(ref)`. Décode `depends_on` (v10) en liste avant de rendre.

## model.add_task() — insère une task (priorité + DoD)
`src/cockpit/roadmap/model.py:96` · appelé par `resolver.cli_dispatch` (`task add`), `seed.seed_launch_roadmap`
Task `todo` sous une feature résolue. `priority` bornée à `P0..P3` (sinon `ValueError`). `depends_on` = slugs de tasks **de la même feature** (JSON). `acceptance` = TEXT libre injecté comme DoD dans le prompt worker au dispatch. `IntegrityError` → `ValueError` (doublon). NB : la validation « acceptance non vide » vit côté CLI (`resolver.cli_dispatch`), pas ici — `model` reste souple.

## model.list_features() — features d'un projet
`src/cockpit/roadmap/model.py:119` · appelé par `check_roadmap`, `resolver.classify_features`, `cli_dispatch` (`show`)
SELECT `WHERE project_id ORDER BY slug`, `depends_on` re-décodé par row (v10). Ne joint pas les tasks (l'appelant les attache via `list_tasks`).

## model.list_tasks() — tasks d'une feature
`src/cockpit/roadmap/model.py:129` · appelé par `check_roadmap`, `resolver.index_for_feature`, `cli_dispatch` (`show`)
SELECT `WHERE feature_id ORDER BY slug`, `depends_on` re-décodé par row. C'est la source des index passés au resolver.

## model.to_yaml() — sérialise la roadmap au contrat figé
`src/cockpit/roadmap/model.py:159` · appelé par `cli_dispatch` (`roadmap show`)
PUR. Émet `{version, project, features:[…]}` via `_feature_doc` (helper ligne 139) : `facet`/`blueprint`/`depends_on`/`acceptance` sortis **seulement si présents** (contrat rétro-compatible, une roadmap v1 reste byte-identique). `sort_keys=False, allow_unicode=True`.

## resolver.classify() — état + blockers en vocab cockpit
`src/cockpit/roadmap/resolver.py:65` · appelé par `check_roadmap`, `cli_dispatch` (branche « aucune READY »)
Adaptateur mince : `_classify_tm` délègue l'**état** au moteur `taskmap.classify` (via `_to_records`, `root`/`today=None`), puis re-projette `{**row, state, blockers}` byte-identique au contrat cockpit — `_blockers` re-traduit depuis l'index **original** (ERROR → dep inconnue, BLOCKED_DEPS → dep non-done + son statut). Le graphe reste la seule autorité ; zéro copie du moteur.

## resolver.eff_prio() — priorité effective transitive (déléguée au cœur)
`src/cockpit/roadmap/resolver.py:76` · appelé par (surface interne ; le ranking passe par `_core_rank_ready`)
Délègue à `taskmap.core.graph.eff_prio(_to_records(index), PRIO)`. Historiquement forkée, `eff_prio` a **gradué dans le cœur taskmap** au dé-fork (distillation-vers-le-centre). Dict keyé par slug (`id ≡ slug`).

## resolver.resolve_next() — la prochaine task dispatchable
`src/cockpit/roadmap/resolver.py:81` · appelé par `cli_dispatch` (`task next`)
Range les READY par `taskmap.core.graph.rank_ready(_classify_tm(index), PRIO)` (rang canonique = `eff_prio` transitive + tiebreaks), prend `ranked[0]`, re-traduit `{**row, state:"READY", blockers:[]}`. Aucune READY → `None`.

## resolver.classify_features() — DAG **inter-feature** d'un projet (v10)
`src/cockpit/roadmap/resolver.py:128` · appelé par `check_roadmap`
Même autorité taskmap, une couche au-dessus des tasks. Une feature est READY quand toutes ses prérequises sont `merged` (`_FEATURE_STATUS_TO_TM = {"merged":"done"}`, ligne 103) ; BLOCKED_DEPS sinon ; ERROR/CYCLE comme le DAG des tasks. `_feature_blockers` re-traduit en vocab cockpit. Index feature projet-global, **jamais** threadé dans l'index task (invariant « 1 index taskmap = 1 feature »).

## resolver.index_for_feature() — {slug: task} d'une feature depuis la DB
`src/cockpit/roadmap/resolver.py:91` · appelé par `cli_dispatch` (`task next`), tests
Résout la feature (`model.resolve_feature`) puis mappe `model.list_tasks` en `{slug: row}` — la forme d'index consommée par `classify`/`resolve_next`.

## prompt.build_worker_prompt() — synthétise le prompt du worker `claude -p`
`src/cockpit/roadmap/prompt.py:80` · appelé par la machinerie de dispatch (au lancement d'un worker sur la NEXT task)
Déterministe (zéro LLM, seul un `read_text` des docs du worktree). Compose : header (task/feature/facette/branche) + `_facet_block(…, "PERSONA.md")` + `_mandate()` (mandat worker autonome : implémente la task, `docsmap where`, NE touche PAS au cycle git, termine par `## Décisions prises`) + `_facet_block(…, "METHOD.md")` + `_acceptance_block(task)` (DoD verbatim) + `_context_block(root)` (aperçus bornés `≤1200c` de `docs/design.md`|`roadmap.yaml`|`architecture.md`). Blocs vides filtrés. Facette résolue via `facet_mod.resolve_facet`. Le prompt part sur le **stdin** de `claude -p` (parade E2BIG). Écarte délibérément les injections mémoire du vault (blueprints/stacks/catalogs) — inadaptées à une forge générique.

## check.check_roadmap() — gate de complétude (drainable ssi liste vide)
`src/cockpit/roadmap/check.py:32` · appelé par `cli_dispatch` (`roadmap check`, exit 1 dès une issue)
Read-only, déterministe, unique autorité de complétude (partagée CLI + API). Réutilise `resolver.classify` (dangling → `DANGLING_DEP`, cycle → `CYCLE`) et le vocab `model._project_facets` — zéro réécriture du DAG. Émet des `Issue` (dataclass frozen, ligne 22) : `EMPTY` (0 feature / feature sans task), `MISSING_FACET`/`BAD_FACET` (facette absente / hors bundle), `MISSING_ACCEPTANCE` (task sans DoD). Puis le DAG inter-feature via `resolver.classify_features` : `DANGLING_FEATURE_DEP`/`FEATURE_CYCLE` + `DEAD_FEATURE_DEP` (dépend d'une feature `cancelled`, jamais débloquable). Une feature BLOCKED_DEPS **normale** (prérequis pas encore mergé) n'est PAS une issue.

## seed.seed_launch_roadmap() — sème la roadmap de lancement d'un bundle
`src/cockpit/roadmap/seed.py:17` · appelé par `projects.registry.create_project` (chemin SEED d'un projet neuf uniquement)
Pure seed (aucune heuristique design-first ; l'ordre naît de la GRAINE `depends_on`, jamais du resolver). `load_launch_roadmap(project_type)` (fail-soft → 0) → boucle features (`model.add_feature`, `depends_on` inter-feature semable v10) puis tasks (`model.add_task`, `priority` défaut `P1`). Retourne le nombre de features semées. Idempotence : aucune — `add_feature`/`add_task` lèvent `ValueError` sur doublon, l'appelant enveloppe fail-soft (jamais de rollback SoT). Import paresseux côté registry (évite le cycle registry↔model).

## Zones non détaillées
- **model.py** : `_now` (timestamp ISO UTC), `_project_facets` (vocab facettes bundle∪base, registre-driven, fallback `{'doc'}`), `_feature_doc` (bloc feature du contrat YAML, champs optionnels conditionnels), `cli_dispatch` (route `roadmap add-feature|show`).
- **resolver.py** : `_to_records`/`_feature_records` (projection rows cockpit → forme record taskmap : `id≡slug`, `created≡created_at`, `tags:[]`, statut mappé), `_blockers`/`_feature_blockers` (re-traduction blockers en vocab cockpit), `_classify_tm` (délégation `taskmap.classify`), `_counts` (tally d'états pour le rapport CLI), `cli_dispatch` (route `task add|next`), constantes `PRIO`/`_STATUS_TO_TM`/`_FEATURE_STATUS_TO_TM`.
- **prompt.py** : `_mandate`, `_context_block`, `_facet_block`, `_acceptance_block` (helpers de composition détaillés inline dans `build_worker_prompt` ci-dessus), constantes `CONTEXT_DOCS`/`_EXCERPT_MAX`.
- **check.py** : `Issue` (dataclass frozen, énumération des `kind`), `cli_dispatch` (route `roadmap check`, rapport groupé par feature).
