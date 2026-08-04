# Architecture — service / API (Python)

> Point de départ de la doc de ce projet. Ce type = un **service backend packagé** (FastAPI / CLI service),
> toolchain `ruff` + `mypy` + `pytest`, indexé par `codemap` (moteur AST Python). Étoffe cette page au fil du
> travail — `docsmap where "<intention>"` la rend interrogeable.

## Intention
_(À renseigner.)_ Ce que ce service expose, pour quels consommateurs, et le critère de succès.

## Où vit quoi
- `src/` — le code du service (routes, modèles, logique). Interroge-le via `codemap where`, ne grep pas.
- `docs/` — la prose durable (intention, décisions, contrats d'API). Via `docsmap`.
- `tests/` — un test par capacité livrée ; une capacité sans test = non livrée.

## Comment ce projet se travaille
Facette par défaut **backend** : doc-first (interroge la doc avant d'écrire une API non triviale),
gate `ruff`+`mypy`+`pytest` vert avant tout commit, contrat d'API documenté dans `docs/`. Boucle `work-loop`
(feature depuis `dev`, gate vert, `main` promu depuis `dev`).
