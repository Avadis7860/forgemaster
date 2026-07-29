# Méthode — facette Code

1. **Lis avant d'écrire** — `codemap where`/`callers`/`imports` pour situer ; jamais de fouille à l'aveugle.
2. **Diff minimal** — le plus petit changement qui atteint l'objectif ; pas de refactor opportuniste non demandé.
3. **Vérifie** — lint + types + tests du sous-système touché (s'ils existent) avant de considérer l'étape faite.
4. **Anti-boucle** — une API non triviale se lit (doc/code), elle ne s'invente pas ; pas de signature devinée.
5. **URL relative, jamais une autorité en dur** — un lien/redirect/base d'API que ton produit sert ou émet
   reste **relatif** (`/x`, `./x`), résolu contre l'origine par laquelle le visiteur t'a atteint. N'écris JAMAIS
   un `scheme://host:port` en dur (`http://127.0.0.1:...`, `http://localhost`, un port interne, un host deviné) :
   tu ne contrôles pas l'autorité côté visiteur → il suit vers du vide (`ERR_CONNECTION_REFUSED`), bug réputation.
   Une URL absolue ne vient que d'une **base injectée au déploiement** (variable d'env / config), jamais bakée
   dans le code ou l'artefact servi. Le bind d'écoute (`0.0.0.0:PORT`) est OK — c'est l'**advertise** qui est visé.
   Verrouillé par la dimension de gate `advertised_authority` (le gate te met 🔴 si un redirect fuit une autorité).
