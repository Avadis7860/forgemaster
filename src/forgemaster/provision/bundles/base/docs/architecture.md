# Architecture — {ce projet}

> Stub semé par le forgemaster à la création. C'est le **point d'entrée** de la doc du projet : étoffe-le au fil
> du travail. `docs/` est la **mémoire durable** du projet — `docsmap where "<intention>"` la rend
> interrogeable sans tout relire.

## Intention

_(À renseigner.)_ Ce que ce projet fait, pour qui, et le critère de succès. Une session qui reprend le projet
lit **cette** section d'abord.

## Où vit quoi

- `docs/` — la prose du projet (intention, décisions, specs). Interrogeable via `docsmap`.
- `src/` (ou l'arbre source du projet) — le code. Si un `code-map` est branché, interroge-le plutôt que de
  grep à l'aveugle.
- `.claude/skills/` — les boucles outillées : `roadmap-decompose` (planifier), `docs-authoring` (mémoriser),
  `work-loop` (git-native sûr), `quality-gate` (porte qualité).

## Comment ce projet se travaille

1. **Planifie** avec `roadmap-decompose` : l'intention devient des features (chacune taguée d'une facette) et
   des tasks (DAG `depends_on` + critères d'`acceptance`). C'est ce qui rend le travail dispatchable et
   parallélisable.
2. **Exécute** chaque feature via `work-loop` : worktree `feature/<sujet>` depuis `dev` → `quality-gate` vert
   → `dev` en ff-only → `main` promu depuis un `dev` vert. `main` ne se travaille jamais. Un acte irréversible
   exige un GO humain (fail-closed).
3. **Mémorise** avec `docs-authoring` : ce qui a été décidé/construit et doit survivre va dans `docs/`,
   interrogeable via `docsmap where`.
