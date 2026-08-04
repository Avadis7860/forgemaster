---
name: quality-gate
description: Gate qualité du forgemaster (ruff + mypy + pytest + smoke réponse : CLI --help, daemon importable sans fastapi, socle résout, DB se crée) — à passer VERT avant tout commit. Un rouge = on ne commit pas.
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
$VENV/ruff check .              # 1. lint + imports (TOUT le first-party ; bundles exclus via config)
$VENV/mypy                      # 2. types (config dans pyproject)
$VENV/pytest -q                 # 3. tests (socle + câblage)
```

1. **Lint** (`ruff check .`) — style + imports + bugs simples, sur **tout le Python first-party** (racine
   `hatch_build.py`, `web/tools/`, `src`, `tests`), les payloads bundles étant exclus par `extend-exclude`
   (règle négative, pas d'allowlist énumérée qui laisse dériver du code load-bearing hors `src`). Rouge →
   corriger, jamais `# noqa` sans motif.
2. **Types** (`mypy`) — le package doit typer proprement. Une dép serveur non typée (`fastapi`/`uvicorn`)
   est déjà `ignore_missing_imports` — ne pas l'élargir sans motif.
3. **Tests** (`pytest`) — tout vert. Un test qui n'existe pas pour une capacité livrée = capacité non livrée.
4. **Smoke réponse** (la spine tient pendant qu'on porte les couches) :
   ```bash
   $VENV/forgemaster --help                                   # la CLI se construit (parser figé)
   $VENV/python -c "import forgemaster; import forgemaster.config; import forgemaster.db.schema"
   $VENV/python -c "from forgemaster.daemon import app"        # daemon importable SANS fastapi (import paresseux)
   $VENV/python -c "from forgemaster.config import Settings; from forgemaster.db import store; \
     import tempfile, pathlib; d=pathlib.Path(tempfile.mkdtemp()); \
     s=Settings.resolve(home=d); c=store.open_db(s); \
     print(sorted(r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")))"
   # → ['dispatch_jobs','features','port_reservations','projects','tasks'] : le socle SQLite se crée
   ```
   (Remplace le double-build byte-identique de `code-map` : le forgemaster n'est pas un générateur
   déterministe — il garantit que la **spine démarre et répond**, pas une sortie byte-identique. À mesure
   que les couches sont portées, ajouter un smoke qui prouve que le RÉSULTAT s'affiche, jamais juste un 200.)

## Front (`web/`) — quand le diff touche `web/src/`

Node via **nvm** (`. ~/.nvm/nvm.sh && nvm use 22`), depuis `web/` :

```bash
npm run lint            # 1. eslint (+ react-hooks)
npm run test            # 2. vitest (primitives + logique) — HORS Tier-0, lancé à la main
npm run build           # 3. tsc --noEmit && vite build (types + bundle)
python tools/front_conformance.py   # 4. design-system (R1-R5 : primitive/token/statusTone)
```

- **Design-system d'abord** : toute vue consomme les primitives (`components/ui/`) + les tokens
  (`@theme` dans `index.css`) ; `front_conformance.py` refuse bouton brut / z-index·couleur arbitraires /
  teinte de statut inline (échappatoire `forgemaster:allow` motivée). Exempte `components/ui/` et les tests.
- **Boucle visuelle AVANT de livrer** (mandat, pas optionnel) : `python tools/ui_shot.py <route> …` →
  **Read** le PNG → critique (hiérarchie, 1 action primaire, états vide/chargement, densité) → edit →
  re-shoot. C'est l'itération (aucun verdict) ; le gate SHA-bound feature-verified prouve avant merge.
- vitest peut être 🟡 (rouge front non bloquant Tier-0) mais **doit être lancé à la main avant tout merge
  `web/`**. Le build (types) et la conformance, eux, sont bloquants.

## Sortie

Un rapport concis par étage (lint / types / tests / smoke / front) : **PASS** ou la première erreur.
**Tout doit être PASS** pour committer. Sinon : corriger la cause (pas déplacer un seuil), re-lancer.
