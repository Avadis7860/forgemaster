"""content — canal d'injection d'un asset/contenu fourni par l'opérateur dans un projet.

Pendant que `design/` pose une CIBLE visuelle (template de référence appliqué) et `provision/` sème
l'ossature d'un projet neuf, `content/` livre le **matériau** que le dirigeant apporte lui-même : charte
graphique, schéma de référence, copy, doc. Deux étages, à l'image de `design/` :

- `content.upload.write_project_upload` — cœur **PUR filesystem** (écrit les bytes sous
  `docs/design/<slug>/`, applique les bornes verrouillées, no-op sur data vide), symétrique de
  `design.seed.write_design_seed` ;
- `content.ingest.ingest_upload` — compose la livraison **worktree-aware** (écrit dans le worktree actif du
  projet pour un Read live, ou réserve une feature éphémère `content-<x>` — merge human-GO), `GitBackend`
  **injecté**, à l'image de `design.apply.apply_template`.

Spec : `docs/specs/project-content-upload.md` (règles verrouillées + invariants de test). Tracker vault :
`cockpit-project-content-upload`.
"""
