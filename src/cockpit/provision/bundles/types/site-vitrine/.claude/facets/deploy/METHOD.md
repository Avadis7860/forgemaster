# Méthode — facette Deploy

1. **Build reproductible** — `astro build` → `web/dist/` statique, déterministe (pas d'horloge murale ni de
   fetch non-figé au build). L'artefact déployé est du HTML/CSS/JS, rien d'autre.
2. **Image mince** — `Dockerfile` multi-stage (Node build → `nginx:alpine`) ; `.dockerignore` exclut outillage,
   docs, `node_modules`, `dist` ; conserve `web/` et `nginx.conf`.
3. **Contrat de run** — nginx écoute `0.0.0.0:8000` ; `compose.yaml` publie `${COCKPIT_PORT:?}:8000` (échec
   bruyant si le port n'est pas injecté) ; aucun `volumes:`/`networks:` ; aucun nom de projet en dur.
4. **Secrets** — jamais dans l'image ni le compose ; passent par le mécanisme du cockpit.
5. **Vérif** — après build : le conteneur répond 200 sur `:8000` et sert les 3 locales. Interroge
   `query(type=tech, scope=nginx)` / `scope=docker` avant une directive non triviale.
