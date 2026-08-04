# .claude/ — toolkit agentic du projet

L'environnement qui rend ce projet **auto-travaillable** : personas dispatchables + boucles outillées. Un
clone suffit à travailler ; le forgemaster automatise la même boucle, aux mêmes invariants.

## Contenu

- **`facets/`** — une **persona par facette de travail** (`code`, `test`, `doc`, `infra`, `review`,
  `orchestrator`, + les facettes propres au type). Chaque facette porte `PERSONA.md` (posture), `METHOD.md`
  (méthode) et `settings.local.json` (permissions scopées). Une feature tague sa facette ; le dispatch
  active la persona correspondante (`settings.local.json` copié en `.claude/settings.local.json`).
- **`skills/`** — les boucles outillées, un dossier par usage :
  - `roadmap-decompose` — planifier (intention → features[facette] → tasks[DAG + acceptance]) ;
  - `work-loop` — la boucle git-native sûre (feature depuis `dev`, gate vert, ff-only, GO humain) ;
  - `quality-gate` — la porte qualité (lint → types → tests, verte avant tout commit) ;
  - `docs-authoring` — mémoriser dans `docs/`.
- **`settings.json`** — permissions du terminal humain (jamais élargies aux verbes d'orchestration).

## Règle

Les `settings.local.json` **sources** vivent sous `facets/**` (vendorés). La **copie activée**
`.claude/settings.local.json` est gitignorée — c'est un artefact de dispatch, pas une source.
