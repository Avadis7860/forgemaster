# Méthode — facette Tool

1. **Schéma figé** — la sortie est un contrat ; tout changement = bump + entrée CHANGELOG, jamais en douce.
2. **Cœur pur / effets aux bords** — la logique testable ne fait pas d'I/O ; les effets sont injectables.
3. **Déterminisme testé** — même entrée → même sortie ; un test par sous-commande, cas limites inclus.
4. **Gate avant commit** — `ruff` → `mypy` → `pytest` vert.
