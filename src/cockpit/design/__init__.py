"""design — application d'un template UI de référence à un projet (« inspire mon projet de ce template »).

Pendant que `provision/` SÈME l'ossature d'un projet neuf (bundle) et `roadmap/` porte son plan de travail,
`design/` pose une **cible visuelle** : le dirigeant choisit un template de référence de la vitrine
(`routes/templates`) et l'applique à un projet. Forme retenue (A — crée le TRAVAIL de customisation) :
`apply_template` crée une feature+task `design-<slug>` et sème la graine `docs/design/<slug>/`
(`seed.write_design_seed`) sur sa branche ; un worker de customisation la relit
(`roadmap.prompt._design_block`) et customise le vrai `web/` du projet. Cœur **déterministe**, injectable
(`git`) — aucune I/O réseau, aucun `claude` spawné.
"""
