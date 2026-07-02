---
name: port-tool
description: Porte un outil legacy (source vault MCP) dans son emplacement de package, en appliquant le correctif de point faible documenté + un test sur fixture. LE workflow récurrent d'extraction.
inputs: [module-cible]
outputs: [module porté, test, PORTING.md à jour]
related_catalogs: []
---

# port-tool — porter un outil dans son emplacement propre

## Quand l'utiliser

À chaque étape de l'extraction *outil par outil* : un stub `raise NotImplementedError("port: <source>
— #N")` doit devenir du code réel. (Skill jumeau du repo `code-map` — même discipline, sources différentes.)

## Procédure

1. **Cible.** Ouvre le module à porter (ex. `src/mcp_catalogs/catalogs.py`). Lis son docstring et sa
   constante `_PORT = "port: <fichier source> — #N"` : elle nomme la **source** legacy et le **correctif**.
2. **Lis la source + le correctif.** Ouvre le fichier source vault nommé (`lib/vault_catalogs.py`,
   `server/tools/<x>.py`, `server/server.py`, …) ET la ligne `#N` de `docs/weak-points.md`. Le correctif
   dit *quoi changer* en portant (imports de package, résolveur `Settings`, drop BWS, etc.). **Ne recopie
   pas tel quel** — applique le fix.
3. **Anti-boucle.** Avant une API non triviale (`fastmcp`, `sqlite3` FTS5, PyJWT), vérifie la signature
   dans la doc / le code — jamais « de mémoire ».
4. **Porte.** Écris le code dans le slot. Respecte : schéma consommé **figé** (`docs/schema-contract.md`),
   zéro cap silencieux, `from __future__ import annotations`, imports **relatifs de package** (`from
   ..config import Settings`, `from . import catalogs`), aucun chemin en dur, aucune notion de vault/BWS.
   Les fonctions de lecture restent **pures** (paramétrées par dossier). Scaffold : `.claude/templates/module.py.tmpl`.
5. **Teste.** Ajoute/complète un test sur la **`DATA_ROOT` de fixture** (`.claude/templates/test_module.py.tmpl`) :
   correction sur `tests/fixtures/data/` (mini-catalog + index, mini `decisions.jsonl`). Jamais la donnée live.
6. **Gate.** Lance le skill `quality-gate` (ruff + mypy + pytest + smoke). Tout vert.
7. **Journal.** Coche la ligne du module dans `PORTING.md` (source, correctif appliqué, test).

## Garde-fous

- Un module porté qui casse le schéma consommé ou une signature d'outil sans bump = à refuser.
- Ré-introduire un `sys.path.insert`, un résolveur de racine vault, ou une dépendance BWS = à refuser
  (c'est précisément la dette qu'on extrait — correctifs #1/#2/#3).
- Si le port révèle un point faible non listé → l'ajouter à `docs/weak-points.md` avant de continuer.
