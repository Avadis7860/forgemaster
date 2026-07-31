# spec — Tier-0 natif : gate de toolchain (contrat d'applicabilité **universel**)

> Cible : `gate/toolchain.py` (runner + verdict SHA-bound + `status`), câblé dans `gate/merge.evaluate_gate`.
> Frère déterministe de `gate/verify.py` (feature-verified, Tier-1.5 runtime). Referme « vitest hors gate ».

## Problème tranché

La chaîne d'autorité `compose_merge_decision` prévoyait déjà un cran **Tier-0 natif** (`native_status` :
`applicable`/`ok`/`failed_step`/`cmd`/`exit_code`, veto déterministe non-overridable) — mais il **n'était
jamais peuplé** (`run_merge`/`evaluate_gate` injectaient `native=None`). **Aucun check toolchain déterministe
ne tournait au merge** : ni le front (`npm run gate` = eslint+vitest+build), ni le backend (ruff/mypy/pytest).
Le gate ne reposait que sur le reviewer LLM (Tier-1), feature-verified runtime (Tier-1.5) et le GO humain →
un rouge front/back **pouvait se merger**. `feature-verified` prouve que le RÉSULTAT *s'affiche* (DOM) ; il ne
remplace pas la **preuve statique déterministe** (types, lint, tests unitaires, build).

## Règles verrouillées

1. La porte est **déterministe** : un veto de toolchain est un **fait**, pas un avis → **non-overridable**
   (jamais levé par un override humain, contrairement à Tier-1/Tier-1.5). Le LLM ne juge ni ne seuil.
2. **Applicabilité dérivée du DIFF seul, en cadrage POSITIF** *(amendée 2026-07-31, cf. §Amendement)* — pas
   besoin du worktree côté décision. Les **routes connues** déclenchent leur groupe (`front` ssi le diff
   touche `web/` ; `backend-node` ssi un fichier node hors `web/` ; `backend` ssi un `*.py`) ; **tout
   résidu de source exécutable qu'aucune route connue ne couvre déclenche le groupe `declared`**. La charge
   de la preuve porte sur l'**absence de source**, jamais sur la reconnaissance du langage.
   **`N/A` est réservé aux diffs sans source** : prose (`DOC_SUFFIXES`), verrous de dépendances, assets
   binaires. Un diff vide reste `N/A`.
   *Le cas mixte compose* : `["x.py", "main.go"]` → `["backend", "declared"]` — les deux doivent être couverts.
3. **Convention d'abord, DÉCLARATION pour le reste** *(amendée 2026-07-31)*. Les routes connues restent
   détectées par convention (groupe `front`/`backend-node` si un `package.json` racine ou per-dir porte un
   script `gate` ; groupe `backend` si `pyproject.toml` à la racine). Le groupe `declared`, lui, est monté
   depuis la table **`[bundle.gate]` du `.cockpit/bundle.toml`** du worktree :

   ```toml
   [bundle.gate]
   # Steps ordonnés, arrêt au 1ᵉʳ rouge. `cwd` optionnel, relatif à la racine du worktree.
   steps = [
     { name = "vet",  argv = ["go", "vet", "./..."] },
     { name = "test", argv = ["go", "test", "./..."] },
   ]
   ```

   Le RUN croise **déclencheurs (diff) ∩ unités montables (worktree)**. Steps ordonnés, arrêt au 1ᵉʳ rouge.
   Deux steps identiques (`name`+`cmd`+`cwd`) issus de groupes différents ne sont **joués qu'une fois**.
   **Aucun hardcode de langage** (`go`, `cargo`, `rake`…) : l'agnosticité par délégation est l'acquis qui
   rend cette règle tenable — le gate ne cherche plus à *reconnaître*, il exige d'être *renseigné*.
   **Déclaration malformée = déclaration absente** (fail-CLOSED) : elle ne dégrade jamais vers le vert.
4. **Fail-CLOSED** : toolchain applicable mais verdict **absent / périmé / rouge** → `ok=False` → merge
   **bloqué**. Symétrique à « review absente = blocage de process ». Deps manquantes dans le worktree → step
   rouge (front a un `npm ci` de secours ; le venv py est supposé fourni par le worker/bundle).
5. **Verdict SHA-bound CACHÉ, jamais exécuté dans un GET.** `evaluate_gate` (appelé par le `GET /api/gate`
   **poll-é**) ne fait que **LIRE** le verdict — invariant V4 : le GET reste cheap/idempotent, le runner
   goto-only ne déclenche aucun effet. L'exécution est un **step séparé** (`cockpit gate toolchain` /
   `POST …/toolchain`) qui **ÉCRIT** le verdict. Ancre = SHA de la branche de feature ; tout commit ultérieur
   **périme** la preuve (`is_fresh`).
6. `compose_merge_decision` reste **inchangé** (cœur pur porté verbatim) : on lui **fournit** un `native_status`
   peuplé, on ne touche pas sa logique.
7. La promesse du gate est **prouvée live (dogfood)** : `run_toolchain` contre le vrai `web/` du cockpit — vert
   quand le front passe, **rouge** (`failed_step=npm-run-gate`) quand un vitest casse — sinon elle ne vaut rien.

## Invariants de test (encodés dans `tests/test_gate.py`)

- Diff `web/` sans verdict frais → gate **bloque** (« toolchain non exécutée », non-overridable) ; verdict
  frais+vert → passe ; commit ultérieur → **périmé** → bloque.
- Diff sans **source** (prose seule — ex. `README.md` ; verrous ; assets) → **N/A** (aucun blocker natif).
- **Le test du trou** *(2026-07-31)* : diff `["main.go"]` seul → `applicable_triggers` rend `["declared"]`,
  **jamais `[]`** ; sans `[bundle.gate]`, `run_toolchain` rend un step **rouge** « toolchain non déclarée » et
  `status` rend `{applicable: True, ok: False}`. Avec `[bundle.gate]` montable → vert. Sans ce test, la
  régression revient en silence.
- **Non-régression du renversement** : les assertions existantes sur `applicable_triggers` (routes `front` /
  `backend-node` / `backend`) et sur `detect_groups` tiennent **verbatim**. Si l'une doit bouger, c'est que
  le renversement a débordé.
- **Anti-récidive de seed** (`tests/test_provision.py::test_typed_seed_ships_mountable_toolchain`) : pour
  chaque type de bundle, tout groupe déclenché par une probe représentative — **`declared` compris** — doit
  être **montable** depuis le seed. Un type dont le contrat de RUN (`Dockerfile`/`compose.yaml`/`nginx.conf`)
  n'est couvert par rien échouerait le gate par construction.
- `build_verdict` : `ok` ssi tous les steps verts ; `failed_step` = 1ᵉʳ rouge ; 0 step = vacuously vert.
- `run_toolchain` fail-closed : binaire introuvable / timeout → step rouge, **jamais** d'exception ; groupe
  absent du worktree → aucun subprocess.
- Intégration : `native_status` absent/rouge **bloque même avec `--go`** ; vert → passe.
- **Symétrie** : le même runner lance le front (`npm run gate`) ET le backend (ruff→mypy→pytest) selon le diff.

---

## Amendement 2026-07-31 — le contrat d'applicabilité devient universel

### Le défaut corrigé

L'applicabilité était dérivée d'une **allowlist de trois motifs** (`FRONT_DIR`, `PY_SUFFIX`,
`NODE_SUFFIXES`). Un diff qui n'en touchait aucun sortait en `[]` → `status` rendait
`{"applicable": False}` → `compose_merge_decision` ignorait **le seul veto non-overridable de la pile**
(Tier-1 est levable par `--override`, Tier-1.5 dépend d'une UI, le juge esthétique est advisory).

Un diff 100 % Go / Rust / Ruby / shell mergeait donc sans qu'aucun étage déterministe ne se soit allumé,
**et sans un mot**. Le module ne raisonnait pas faux — il appliquait « ce que je sais gater, je le gate ».
Le défaut était le **silence** : l'utilisateur voit des verdicts s'afficher et un merge passer, sans savoir
qu'un étage entier n'a jamais tourné. C'est pire qu'un gate absent — c'est un gate qui *prétend*.

Invisible sur notre stack (Python + TS) ; structurel pour tout utilisateur distribué.

### Pourquoi maintenant, et pourquoi c'est la spec qui s'amende

La règle 3 portait sa propre clause d'échappement : *« pas de config déclarative **tant qu'un 2ᵉ projet ne
diverge pas** »*. L'utilisateur distribué **est** ce 2ᵉ projet. On amende une règle verrouillée **par la
condition qu'elle a elle-même posée**.

### Renversement, pas élargissement

Ajouter `.go`, `.rs`, `.java`… aux motifs connus a été **écarté** : ça déplace la frontière du silence au
langage N+1 et contredit l'agnosticité par délégation (`toolchain.py`, frontière §3). La charge de la preuve
devait s'inverser. Le patron correct existait **déjà dans le même module**, pour le tier voisin :
`is_docs_only` / `has_reviewable_code` (Tier-1) raisonnent en positif — *tout fichier non-prose exige une
review*. Le Tier-0 adopte la même orientation.

**Le Tier-0 garde son propre ensemble de non-source**, voisin mais **distinct** de `DOC_SUFFIXES` : une
*review* Tier-1 veut voir un `.sh` ou un `.toml`, une *toolchain* n'a rien à faire d'un `.png` ou d'un
`package-lock.json`. Non-source Tier-0 = **prose ⊕ verrous de dépendances ⊕ assets binaires**.

### La pureté est préservée — c'est ce qui rend le fix petit

`applicable_triggers` **doit** rester diff-only : `status` est appelé par `evaluate_gate`, lui-même appelé
par le `GET /api/gate` **poll-é** (règle 5, invariant V4 — aucun worktree, aucun effet). Or la déclaration
vit dans le worktree. On sépare donc :

| | autorité | entrée |
|---|---|---|
| **applicabilité** | `applicable_triggers` (PUR) | le diff seul |
| **montabilité** | `_steps_for` | le worktree (qu'il reçoit déjà) |

Le chemin fail-closed « groupe déclenché mais non couvert → step rouge synthétique » **existait déjà**.
`declared` s'y branche sans plomberie neuve.

### Rayon d'explosion — mesuré, pas estimé

Rejoué sur les **197 derniers commits** de ce repo : **6 basculent** de `N/A` vers `declared`
(≈ 3 %), tous défendables — `deploy/provision-ct.sh` ×2, `.codemap.toml`, `.frontmap.toml`+`.gitignore`,
seed `settings.local.json`+`launch-roadmap.yaml`, et **`pyproject.toml` seul**. Ce dernier est le plus
parlant : on pouvait jusqu'ici changer ses pins de dépendances et sa config de lint **sans aucun gate**.

Le résidu réel d'un projet semé est son **contrat de RUN** — `Dockerfile`, `compose.yaml`, `nginx.conf`,
`.dockerignore` — plus ses **entrées de toolchain** (`pyproject.toml`, `tsconfig.json`, `package.json`).
Ces dernières sont déjà *matériellement* couvertes : ruff/mypy/pytest et `npm run gate` les lisent. Déclarer
la commande de gate du projet comme couverture du résidu n'est donc pas un faux-semblant — c'est **le projet
qui déclare ce qu'il sait gater**. La différence avec l'ancien comportement est doctrinale et entière :
**inférer, c'est prétendre ; déclarer, c'est répondre.**

Le mode d'échec choisi est **sur-déclencher** (un rouge honnête, actionnable, que le projet lève en
déclarant) plutôt que **sous-déclencher** (le trou silencieux). Un rouge se voit et se corrige ; un `N/A`
non mérité, non.

### Piège à ne pas rejouer : la surcharge whole-file

Un overlay de type **surcharge `bundle.toml` en whole-file**. Le piège est déjà documenté pour
`[bundle.mcp]` (*« sans ce bloc, le `corpus = true` de la base est PERDU »*) : **`[bundle.gate]` déclaré dans
la base est perdu pour tout type qui surcharge le manifeste.** Chaque type qui a un résidu à couvrir porte
donc son propre bloc, et `test_typed_seed_ships_mountable_toolchain` en est la garde.

### Le coût doit être nul pour un projet semé — et une déclaration ne restate jamais une cible dynamique

Un projet correctement semé ne doit **rien payer** pour le renversement : sa déclaration **duplique** sa route,
et la dédup `(name, cmd, cwd)` de `run_toolchain` l'absorbe. `test_typed_seed_declared_group_costs_nothing`
mesure cette égalité sur les 5 types, sur un diff réaliste (route ⊕ résidu).

Ce test existe parce que le balayage de non-régression a trouvé la **seule** façon de casser cette égalité :
une déclaration est **statique**, une route peut être **dynamique**. La route `backend` calcule sa cible mypy
depuis le worktree (`src` si le dossier existe, sinon `.`). Déclarer `mypy .` collait au seed (plat), puis
divergeait dès que le projet grandissait un `src/` → **mypy joué deux fois**, le second en duplicate-module →
**faux rouge sur un projet parfaitement normal**. Un check qui s'allume sur ce qui est normal est défaillant :
c'est le trou d'en face, moins visible mais aussi coûteux.

Règle qui en découle : **une déclaration ne restate jamais une cible que la route calcule.** Là où la route est
dynamique, elle reste propriétaire du step et la déclaration l'omet — la couverture n'est pas perdue, car la
route se déclenche précisément quand ce step a un sens (mypy ↔ un `*.py` touché ; un diff sans Python n'a aucun
type à revérifier). D'où l'absence volontaire de `mypy` dans les `[bundle.gate]` de `service-api`/`cli-tool`.

### Ce qui ne change PAS

- **Aucun bump `SCHEMA_VERSION`** : `bundle.toml` est **hors contrat figé** (`docs/schema-contract.md` §2c) ;
  aucune colonne SQLite ne bouge ; `native_status` garde ses clés (`cmd` gagne une *valeur*, pas un champ).
  La politique de versionnage fait du bump le *déclencheur de migration* — sans migration, pas de bump.
  Entrée **CHANGELOG** uniquement.
- **`compose_merge_decision`** reste inchangé (règle 6).
- **`is_docs_only` / `has_reviewable_code`** (prédicats **Tier-1**) restent inchangés.
- Les **quatre consommateurs** (`gate/merge.evaluate_gate`, `dispatch/orchestrator`, `daemon/routes/gate`,
  `cli.cli_dispatch`) consomment `applicable_triggers`/`run_toolchain` par leur **contrat**, qui ne change pas.
