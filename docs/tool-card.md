# cockpit — la forge : orchestrer des workers IA isolés

## Ce que c'est

L'**orchestrateur** du framework : un CLI + un daemon (FastAPI + web) qui transforme un projet en **roadmap**
(features → tasks en DAG) et **dispatche des workers IA** sur des tasks définies en amont. Chaque feature =
une branche = un **worktree git isolé** (le mutex) ; N features en parallèle. Backend git **internal-first**
(SoT bare local, zéro réseau). C'est ici que vivent le rail projets/outils et les onglets par projet
(Roadmap · Docs · Dispatch · Gate · Git · Flow · Terminal).

## Pourquoi l'utiliser avec Claude

Lâcher un agent sur un repo sans cadre → dérive, conflits, merges non vérifiés. Le cockpit **borne** le
travail : **pas de task ⇒ pas de dispatch** (le worker part d'un objectif défini) ; chaque worker travaille
dans un **worktree isolé** (aucune collision entre features parallèles) ; un **gate** (tests + review) bloque
**avant** le merge. L'agent travaille dans une boucle **définie et vérifiable**, pas en roue libre. Les autres
outils l'**ancrent** (code-map le code, front-map l'UI, docs-map la prose, mcp-catalogs la doc tierce) ; le
cockpit l'**orchestre**.

## En bref

- `cockpit serve` — daemon + UI (rail projets/outils, onglets par projet).
- boucle : roadmap → dispatch → worktree isolé → gate (tests + review) → merge → cleanup.
- `cockpit run <projet>` — orchestrateur parallèle (drainage du DAG, mutex par feature).
