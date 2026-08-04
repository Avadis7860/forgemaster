# Méthode — facette Backend (dans un projet front-ts)

1. **Contrat d'API d'abord** — publié dans `docs/` ; c'est la source d'alignement du frontend.
2. **Doc-first (anti-boucle)** — interroge la doc/le code avant d'écrire un import non trivial.
3. **Gate** — lint → types → tests vert (toolchain du back : TS `eslint`/`tsc`/`vitest` ou Python selon le repo).
4. **Séquence** — merge la feature backend dans `dev` AVANT la feature frontend qui la consomme.
