# Méthode — facette Backend

1. **Doc-first (anti-boucle)** — avant d'écrire un import non trivial, interroge la doc/le code. Pas de
   signature inventée → pas d'erreur d'exécution → pas de retry.
2. **Contrat explicite** — modèle d'entrée/sortie typé ; l'erreur dit quoi et comment corriger.
3. **Gate avant commit** — `ruff` → `mypy` → `pytest` vert. Corrige la cause, ne déplace pas un seuil.
4. **Fraîcheur carte** — code touché → `codemap build`.
