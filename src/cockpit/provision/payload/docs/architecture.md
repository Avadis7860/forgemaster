# Architecture — {ce projet}

> Stub semé par le cockpit à la création. C'est le **point d'entrée** de la doc du projet : étoffe-le au fil
> du travail. `docs/` est la **mémoire durable** du projet — `docsmap where "<intention>"` la rend
> interrogeable sans tout relire.

## Intention

_(À renseigner.)_ Ce que ce projet fait, pour qui, et le critère de succès. Une session qui reprend le projet
lit **cette** section d'abord.

## Où vit quoi

- `docs/` — la prose du projet (intention, décisions, specs). Interrogeable via `docsmap`.
- `src/` (ou l'arbre source du projet) — le code. Si un `code-map` est branché, interroge-le plutôt que de
  grep à l'aveugle.
- `.claude/skills/` — les boucles outillées : `work-loop` (git-native sûr), `quality-gate` (porte qualité).

## Comment ce projet se travaille

Toute évolution passe par le skill `work-loop` : worktree `feature/<sujet>` depuis `dev` → `quality-gate`
vert → `dev` en ff-only → `main` promu depuis un `dev` vert. `main` ne se travaille jamais. Un acte
irréversible exige un GO humain (fail-closed).
