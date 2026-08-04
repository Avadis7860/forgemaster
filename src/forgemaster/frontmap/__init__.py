"""frontmap — domaine « design-system » : matérialise la source d'un projet géré et interroge l'outil
`front-map` (boîte-noire CLI) pour en extraire le DS indexé (tokens + primitives + routes).

Frontière : le forgemaster ne calcule rien lui-même — il matérialise (`index.ensure_index`, cache SHA) puis
relaie le contrat JSON de `frontmap tokens|primitives|routes` (`catalog.tokens` / `.primitives` / `.routes`).
front-map reste un outil-carte réutilisable hors forgemaster ; on ne dépend jamais de son API Python interne
(jumeau strict de `forgemaster.codemap`, adapté au fait que front-map négocie sa version via `--version`, pas
`--schema-version`, et n'expose pas de `--format` — sa sortie est déjà du JSON).
"""
