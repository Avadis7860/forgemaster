---
name: quality-gate
description: Porte qualité de ce repo (lint + types + tests, selon la toolchain du projet) — à passer VERT avant tout commit. Un rouge = on ne commit pas.
inputs: []
outputs: [rapport pass/fail par étage]
related_catalogs: []
---

# quality-gate — porte qualité avant commit

## Quand l'utiliser

Avant **chaque** commit (et avant tout merge). Prouve que le code est propre, typé et testé. C'est le Tier-0
**déterministe** : ce qu'un script voit, pas ce qu'un humain review. Adapte les commandes à la toolchain du
projet ; la **forme** (lint → types → tests → vert) ne change pas.

## Procédure (défaut : package Python)

```bash
VENV=.venv/bin        # activer le venv du projet d'abord si besoin
$VENV/ruff check src tests      # 1. lint + imports
$VENV/mypy                      # 2. types (config dans pyproject)
$VENV/pytest -q                 # 3. tests
```

1. **Lint** — style + imports + bugs simples. Rouge → corriger la cause, jamais `# noqa` sans motif ni
   déplacer un seuil.
2. **Types** — le code doit typer proprement.
3. **Tests** — tout vert. Un test qui n'existe pas pour une capacité livrée = capacité non livrée.
4. **Fraîcheur de la doc** (si `.docsmap.toml` présent) — après avoir touché `docs/`, re-bâtir l'index et
   vérifier qu'il n'est pas périmé :
   ```bash
   docsmap build --root .   # re-bâtit .docsmap/ (skip idempotent si sources inchangées)
   docsmap check --root .   # signale toute section stale / supprimée
   ```

## Adapter à un autre langage

Ce repo n'est pas forcément en Python. Remplace les trois premiers étages par l'équivalent de la toolchain
(ex. `eslint` / `tsc` / `vitest` pour du TypeScript) — l'étage 4 (docsmap) reste identique. Garde la même
discipline : **tout vert** avant de committer, corriger la cause plutôt que déplacer un seuil.

## Sortie

Un rapport concis par étage : **PASS** ou la première erreur. **Tout doit être PASS** pour committer.
