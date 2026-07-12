"""runtime — le **moteur de run** du cockpit (P2 runtime-hosting). Prend un projet buildé et le **fait
tourner** via un backend **compose** : `1 (projet × branche) = 1 compose-project`, dont le nom EST la
frontière d'isolation (réseau/volumes/conteneurs préfixés → anti-pollution par construction). Consomme le
modèle de données de `projects/deployments` (P1) : matérialise le contexte, réserve un port de service (pool
deploy distinct), pilote le cycle build/start/stop/restart/status, écrit l'état via `set_deployment`.

Frontière : le *scoping* durci des secrets/env/FS vit en P4 ; l'*observabilité* (santé + logs) en P5.
"""
