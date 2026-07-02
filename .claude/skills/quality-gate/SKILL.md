---
name: quality-gate
description: Gate qualité du cockpit (ruff + mypy + pytest + smoke réponse : CLI --help, daemon importable sans fastapi, socle résout, DB se crée) — à passer VERT avant tout commit. Un rouge = on ne commit pas.
inputs: []
outputs: [rapport pass/fail par étage]
related_catalogs: [ruff, mypy, pytest]
---

# quality-gate — porte qualité avant commit

## Quand l'utiliser

Avant **chaque** commit (et avant tout merge). Prouve que le code est propre, typé, testé et que la
**spine répond** (CLI + socle + persistance). C'est le Tier-0 déterministe : ce qu'un script voit, pas
ce qu'un humain review.

## Procédure

```bash
VENV=.venv/bin        # activer le venv du projet d'abord si besoin
$VENV/ruff check src tests      # 1. lint + imports
$VENV/mypy                      # 2. types (config dans pyproject)
$VENV/pytest -q                 # 3. tests (socle + câblage)
```

1. **Lint** (`ruff check`) — style + imports + bugs simples. Rouge → corriger, jamais `# noqa` sans motif.
2. **Types** (`mypy`) — le package doit typer proprement. Une dép serveur non typée (`fastapi`/`uvicorn`)
   est déjà `ignore_missing_imports` — ne pas l'élargir sans motif.
3. **Tests** (`pytest`) — tout vert. Un test qui n'existe pas pour une capacité livrée = capacité non livrée.
4. **Smoke réponse** (la spine tient pendant qu'on porte les couches) :
   ```bash
   $VENV/cockpit --help                                   # la CLI se construit (parser figé)
   $VENV/python -c "import cockpit; import cockpit.config; import cockpit.db.schema"
   $VENV/python -c "from cockpit.daemon import app"        # daemon importable SANS fastapi (import paresseux)
   $VENV/python -c "from cockpit.config import Settings; from cockpit.db import store; \
     import tempfile, pathlib; d=pathlib.Path(tempfile.mkdtemp()); \
     s=Settings.resolve(home=d); c=store.open_db(s); \
     print(sorted(r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")))"
   # → ['dispatch_jobs','features','projects','tasks'] : le socle SQLite se crée
   ```
   (Remplace le double-build byte-identique de `code-map` : le cockpit n'est pas un générateur
   déterministe — il garantit que la **spine démarre et répond**, pas une sortie byte-identique. À mesure
   que les couches sont portées, ajouter un smoke qui prouve que le RÉSULTAT s'affiche, jamais juste un 200.)

## Sortie

Un rapport concis par étage (lint / types / tests / smoke) : **PASS** ou la première erreur.
**Tout doit être PASS** pour committer. Sinon : corriger la cause (pas déplacer un seuil), re-lancer.
