"""codemap — domaine « flot d'exécution » : matérialise la source d'un projet géré et interroge l'outil
`code-map` (boîte-noire CLI) pour en extraire le flot d'appels inter-fonctions d'une opération.

Frontière : le forgemaster ne calcule rien lui-même — il matérialise (`index.ensure_index`, cache SHA) puis
relaie le contrat JSON figé de `codemap flow` (`flow.list_operations` / `flow.flow`). code-map reste un
outil-carte réutilisable hors forgemaster ; on ne dépend jamais de son API Python interne.
"""
