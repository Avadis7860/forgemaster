# Architecture — outil / bibliothèque CLI (Python)

> Point de départ de la doc de ce projet. Ce type = un **package réutilisable** (CLI déterministe + lib),
> schéma de sortie figé, multi-OS, toolchain `ruff`+`mypy`+`pytest`. Étoffe cette page ; `docsmap where` la
> rend interrogeable.

## Intention
_(À renseigner.)_ Ce que l'outil fait, qui le consomme (humain / autre programme), et son contrat de sortie.

## Où vit quoi
- `src/` — le package (CLI + cœur pur). `codemap where` pour t'orienter.
- `docs/` — contrat de schéma, décisions, guide d'usage. Via `docsmap`.
- `tests/` — déterminisme d'abord : même entrée → même sortie ; un test par sous-commande.

## Comment ce projet se travaille
Facette par défaut **tool** : schéma de sortie **figé** (une sortie = un contrat, versionné), cœur pur
séparé des effets, gate `ruff`+`mypy`+`pytest`. Boucle `work-loop`.
