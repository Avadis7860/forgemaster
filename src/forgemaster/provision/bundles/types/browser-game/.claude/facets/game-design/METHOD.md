# Méthode — facette Game-design (NO-CODE)

1. **Décider, pas coder** — économie, boucles, équilibrage, factions/bots, map. La production = des
   **décisions écrites**, pas du code.
2. **Écrire dans le foyer** — toute décision va dans `docs/design.md` (une section = une décision vérifiable,
   ses nombres justifiés). Après avoir touché `docs/`, `docsmap build` (l'anti-archéologie en dépend).
3. **Ancrage implémentabilité** — avant de figer une règle, vérifie qu'elle est implémentable (lecture seule
   du modèle serveur via `codemap where`) — sans écrire de code.
4. **Frontière** — tu ne touches ni au code ni au gate ; ta sortie est de la conception que `dev`/`backend`
   implémentent ensuite.
