# spec — Tier-0 natif : gate de toolchain (front + backend)

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
2. **Applicabilité dérivée du DIFF seul** (pas besoin du worktree côté décision) : `front` ssi le diff touche
   `web/` ; `backend` ssi le diff touche un `*.py`. Aucune toolchain déclenchée → **N/A** (compose l'ignore,
   zéro régression sur les features non concernées).
3. **Détection par CONVENTION** (pas de config déclarative tant qu'un 2ᵉ projet ne diverge pas) : groupe
   `front` si `web/package.json` a un script `gate` ; groupe `backend` si `pyproject.toml` à la racine. Le RUN
   croise **déclencheurs (diff) ∩ groupes présents (worktree)**. Steps ordonnés, arrêt au 1ᵉʳ rouge.
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
- Diff sans toolchain déclenchée (ex. `README.md` seul) → **N/A** (aucun blocker natif).
- `build_verdict` : `ok` ssi tous les steps verts ; `failed_step` = 1ᵉʳ rouge ; 0 step = vacuously vert.
- `run_toolchain` fail-closed : binaire introuvable / timeout → step rouge, **jamais** d'exception ; groupe
  absent du worktree → aucun subprocess.
- Intégration : `native_status` absent/rouge **bloque même avec `--go`** ; vert → passe.
- **Symétrie** : le même runner lance le front (`npm run gate`) ET le backend (ruff→mypy→pytest) selon le diff.
