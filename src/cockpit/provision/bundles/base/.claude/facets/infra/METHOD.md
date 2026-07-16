# Méthode — facette Infra / DevOps

1. **Lis l'état réel** — sonde avant d'agir ; un diagnostic précède toute mutation (pas de fix supposé).
2. **Converge idempotent** — l'opération se rejoue sans casser ; défaut de précondition = message clair, pas de crash.
3. **Vérifie** — l'effet est constaté (health/sonde), pas présumé d'un exit 0.
4. **Documente** — ce qui a changé et pourquoi, pour la session/opérateur suivant.
