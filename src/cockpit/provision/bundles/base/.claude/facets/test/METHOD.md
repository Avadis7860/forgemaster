# Méthode — facette Test / QA

1. **Reproduis** — un test qui **échoue** d'abord, pour la cause exacte (rouge honnête, pas un faux rouge).
2. **Couvre le comportement** — l'entrée→sortie observable et les cas-limites, pas les détails internes.
3. **Déterministe** — pas d'horloge/réseau/aléa non injectés ; un test flaky est un test cassé.
4. **Minimal au vert** — le plus petit changement qui passe ; puis refactor à vert constant.
