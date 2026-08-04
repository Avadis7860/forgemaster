---
name: port-tool
description: Réimplémente une couche du forgemaster depuis sa source legacy (aggregator) dans son slot de package, en appliquant le correctif de dette documenté + un test sur fixture. LE workflow récurrent de portage propre.
inputs: [couche-cible]
outputs: [couche portée, test, PORTING.md à jour]
related_catalogs: []
---

# port-tool — porter une couche dans son slot propre

## Quand l'utiliser

À chaque étape de la réimplémentation *couche par couche* : un stub `raise NotImplementedError("port:
<source> — #N")` doit devenir du code réel. (Skill frère des repos `code-map` / `forgemaster-catalogs` — même
discipline, sources différentes : ici le legacy `services/aggregator/` du vault.)

## Procédure

1. **Cible.** Ouvre la couche à porter (ex. `src/forgemaster/gate/merge.py`). Lis son docstring et sa constante
   `_PORT = "port: <fichier source> — #N"` : elle nomme la **source** legacy (`orchestrator.py`,
   `lib/worker_merge_gate.py`, `loops/review_state.py`, `terminal.py`, …) et le **correctif** du registre.
2. **Lis la source + le correctif.** Ouvre le fichier source legacy nommé ET la ligne `#N` de
   `docs/weak-points.md`. Le correctif dit *quoi changer* en portant (god-module → injection explicite ;
   ssh/proxmox → `core.run` local ; monolithe → routers par domaine ; `/home/dev` en dur → config ;
   find|tail → lecture incrémentale ; clone-split → worktree ; creds/identité writeback injectés ; …).
   **Ne recopie pas tel quel — c'est une réimplémentation propre** : on importe la *décision*, pas la ligne.
3. **Anti-boucle.** Avant une API non triviale (`fastapi`, `sqlite3`, `git`, `pty`), vérifie la signature
   dans la doc / le code — jamais « de mémoire ».
4. **Porte.** Écris le code dans le slot. Respecte : schéma consommé **figé** (`docs/schema-contract.md` :
   SQLite / `roadmap.yaml` / API HTTP), zéro cap silencieux, `from __future__ import annotations`, imports
   **relatifs de package** (`from ..config import Settings`, `from . import worktree`), aucun chemin en dur
   (racines par `Settings`), **transport local injectable** (`core.run`, jamais ssh/CT). L'I/O (exécution,
   git) est **injectée** → le cœur reste pur et testable hors-live. Scaffold : `.claude/templates/module.py.tmpl`.
5. **Teste.** Ajoute/complète un test (`.claude/templates/test_module.py.tmpl`) sur une **fixture jetable**
   (`tmp_path` + un SoT bare réel via `InternalGit` quand la couche touche au git) : runner/exécution
   **fake injecté**, jamais un vrai `claude` ni un hôte distant. Noms fictifs, jamais un vrai basename.
6. **Gate.** Lance le skill `quality-gate` (ruff + mypy + pytest + smoke réponse : CLI/daemon/socle/DB).
   Tout vert. Un gate qui touche à l'irréversible (merge) reste **fail-closed** + feu vert humain.
7. **Journal.** Coche la ligne de la couche dans `PORTING.md` (source, correctif appliqué, test).

## Garde-fous

- Une couche portée qui casse un schéma consommé (SQLite / roadmap.yaml / API) sans bump = à refuser.
- Ré-introduire un **god-module** (`import server`), un **couplage transport distant** (ssh/proxmox/CT-id,
  `/home/dev` en dur), ou un **monolithe** = à refuser (c'est précisément la dette qu'on extrait —
  correctifs #1/#2/#3/#4).
- Un gate qui **blanchit** un échec d'exécution (node/browser/timeout → jamais « vert »), ou qui juge la
  fraîcheur au **mtime** au lieu du **SHA de HEAD**, = à refuser.
- Si le port révèle une dette non listée → l'ajouter à `docs/weak-points.md` avant de continuer.
