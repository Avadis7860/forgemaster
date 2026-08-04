# Méthode — facette Orchestrateur

1. **NEXT** — résous la prochaine task dispatchable (`forgemaster run`/`dispatch`) ; aucune READY = rien à faire.
2. **Dispatch** — un worker sur la task ; tu observes son transcript, tu ne codes pas à sa place.
3. **Gate** — `forgemaster gate` sur la branche ; **rouge = stop** (jamais de `--override` sans raison tracée).
4. **Merge** — sur vert, `forgemaster merge` (+ cleanup) ; puis reboucle. La boucle s'arrête quand la roadmap draine.
