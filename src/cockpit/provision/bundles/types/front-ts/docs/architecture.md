# Architecture — application front (TypeScript)

> Point de départ de la doc de ce projet. Ce type = une **app front** (React / Vite) avec un back optionnel
> (Hono), toolchain `eslint` + `tsc` + `vitest`, UI indexée par `frontmap`, code par `codemap`
> (tree-sitter TS). Étoffe cette page ; `docsmap where` la rend interrogeable.

## Intention
_(À renseigner.)_ Ce que l'app fait, pour quels utilisateurs, et le critère de succès (ce qui s'affiche).

## Où vit quoi
- `web/` (ou `src/`) — le front. `frontmap where` pour tokens / primitives / routes ; `codemap where` pour la logique.
- `docs/` — intention, décisions, design-system. Via `docsmap`.
- `tests/` — `vitest` pour la logique ; la vérif d'écran passe par la **boucle visuelle** (screenshot + Read).

## Comment ce projet se travaille
Deux facettes : **frontend** (défaut — boucle visuelle : screenshot puis lecture AVANT de livrer tout écran) et
**backend** (l'API que le front consomme). Une feature frontend qui dépend d'une API se travaille **après** le
merge de la feature backend dans `dev` (elle en voit alors le contrat). Boucle `work-loop`.
