#!/usr/bin/env bash
# demo_seed.sh — sème une démo lisible du système typed-bundles (Phases 1-6) dans un COCKPIT_HOME isolé.
# 3 projets typés × facettes × DAG depends_on × critères d'acceptation → de quoi voir/tester dans l'UI.
# Usage : COCKPIT_HOME=… COCKPIT_PROJECTS_ROOT=… scripts/demo_seed.sh   (idempotent : reset avant de semer)
set -euo pipefail

: "${COCKPIT_HOME:?export COCKPIT_HOME}" "${COCKPIT_PROJECTS_ROOT:?export COCKPIT_PROJECTS_ROOT}"
rm -rf "$COCKPIT_HOME" "$COCKPIT_PROJECTS_ROOT"
mkdir -p "$COCKPIT_HOME" "$COCKPIT_PROJECTS_ROOT"

# 1) service-api : un backend + sa doc. DAG : endpoints dépend du schéma.
cockpit project create demo-api --type service-api --name "Demo — service API"
cockpit roadmap add-feature demo-api auth --facet backend --title "Authentification"
cockpit task add demo-api/auth schema --title "Modèle User + migration" \
  --acceptance "Table users créée (email UNIQUE, hash) ; migration idempotente ; test round-trip vert."
cockpit task add demo-api/auth endpoints --depends-on schema --title "POST /login /logout" \
  --acceptance "POST /login renvoie 200+token si ok, 401 sinon, 422 si champ manquant ; testé."
cockpit roadmap add-feature demo-api api-docs --facet doc --title "Doc du contrat d'API"
cockpit task add demo-api/api-docs openapi --title "docs/api-contract.md" \
  --acceptance "Chaque endpoint documenté (entrée/sortie/erreurs) ; docsmap check vert."

# 2) front-ts : un front + le back qu'il consomme. Deux features INDÉPENDANTES prêtes → parallélisables.
cockpit project create demo-web --type front-ts --name "Demo — app front"
cockpit roadmap add-feature demo-web session-api --facet backend --title "API de session"
cockpit task add demo-web/session-api contract --title "Contrat /session" \
  --acceptance "GET /session renvoie l'état auth ; contrat publié dans docs/ ; testé."
cockpit roadmap add-feature demo-web login-screen --facet frontend --title "Écran de connexion"
cockpit task add demo-web/login-screen form --title "Formulaire + états" \
  --acceptance "Écran vérifié par boucle visuelle (screenshot+Read) ; erreurs affichées ; vitest vert."

# 3) cli-tool : un outil déterministe. DAG : le writer dépend du schéma figé.
cockpit project create demo-cli --type cli-tool --name "Demo — outil CLI"
cockpit roadmap add-feature demo-cli export --facet tool --title "Commande export"
cockpit task add demo-cli/export schema-freeze --title "Figer le schéma de sortie" \
  --acceptance "Schéma JSON versionné + entrée CHANGELOG ; test de non-régression du contrat."
cockpit task add demo-cli/export csv-writer --depends-on schema-freeze --title "Writer CSV" \
  --acceptance "Même entrée → même CSV (déterministe) ; test par sous-commande, cas limites inclus."

echo "=== projets semés ==="
cockpit project list
