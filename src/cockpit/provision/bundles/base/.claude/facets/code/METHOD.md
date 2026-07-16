# Méthode — facette Code

1. **Lis avant d'écrire** — `codemap where`/`callers`/`imports` pour situer ; jamais de fouille à l'aveugle.
2. **Diff minimal** — le plus petit changement qui atteint l'objectif ; pas de refactor opportuniste non demandé.
3. **Vérifie** — lint + types + tests du sous-système touché (s'ils existent) avant de considérer l'étape faite.
4. **Anti-boucle** — une API non triviale se lit (doc/code), elle ne s'invente pas ; pas de signature devinée.
