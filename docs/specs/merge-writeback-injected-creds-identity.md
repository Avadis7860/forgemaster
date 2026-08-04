# spec — merge-writeback (creds + identité injectés)

> Contrainte distillée (vault `decisions/projects/2026-06-25--merge-go-feedback-and-reviewer-wiring.md`).
> Cible : `git/internal.py` (`merge_writeback`), `gate/merge.py`. Refactor #8.

## Problème tranché

La cause racine du merge-go qui échouait en live n'était **ni le gate ni le reviewer** mais le
**writeback** post-merge (align/cleanup/close) : le miroir projet est provisionné **read-only + sans
identité git** → `push 403` et « empty ident name ». Faux-vert massif : tout le chemin était prouvé en
transport-fake, cette frontière de credential est **invisible aux tests à I/O injectée**.

## Règles verrouillées

- La couture d'injection vit à **une source unique partagée**, consommée par les DEUX chemins : merge
  déclenché à l'UI **et** rattrapage hors-bande (reconcile). **Jamais dupliquée par appelant.**
- On passe la **référence** du secret (résolue par l'appelant / control-plane, via BWS **en amont**),
  **jamais le secret** en clair.
- Injection en `GIT_CONFIG_*` / `GIT_AUTHOR_*` **le temps du writeback seulement, jamais persistée**
  (`core.run(..., env=...)` avec un env composé ponctuel).
- **Preuve de merge = signaux côté serveur/miroir**, pas l'UI : branche supprimée, task `done` archivée,
  commit de clôture authoré par l'identité injectée. Un reviewer headless ne streame rien → « rien de
  visible » est normal.

## Invariants de test (à encoder dans forgemaster)

- Un test **transport-fake ne suffit jamais** à cocher la DoD d'un writeback → exiger une preuve live e2e
  (invariant de process, gate de release).
- L'env creds/identité est **présent pendant** le writeback et **absent après** (non persisté).
- Les deux consommateurs (merge UI + reconcile) tirent la **même** couture (une seule source).
- Corrélat gate : **Tier-1 non-overridable** (un 🔴 reviewer bloque même sous GO humain) — distinct du
  Tier-1.5 overridable de `feature-verified`.
