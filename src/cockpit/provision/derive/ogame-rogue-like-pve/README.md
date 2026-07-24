# derive/browser-game — sources vendorées de la projection `template → seed`

Ce dossier est la **source de vérité build-time** du seed dérivé du bundle `browser-game`. Il n'est **jamais
semé** dans un projet (hors de `bundles/`) : il alimente `cockpit bundle derive`.

- `scaffold.md` — copie épinglée du template corpus `mcp-catalogs-data:corpus/templates/browser-game-pve/scaffold.md`.
- `blueprint.md` — copie épinglée du blueprint `browser-game-pve.md` (source du patron d'étapes splicé en §6).
- `values.toml` — jetons archétype (remplis au build) + allowlist des jetons projet (laissés `{{…}}`).
- `derived.manifest.json` — **généré** : template ref + sha des sources + chemins managés.

## Chemins MANAGÉS de l'overlay (générés — NE PAS éditer à la main)

Les fichiers suivants de `bundles/types/browser-game/` sont **projetés** depuis `scaffold.md`/`blueprint.md` —
les éditer à la main les fait **diverger** (le drift-check rougit). Pour changer le seed : édite la source ici
(ou le corpus, puis re-vendorise), relance `cockpit bundle derive --type browser-game`, commite.

- `package.json`, `tsconfig.json`, `src/shared/schema.ts`, `src/index.ts` (fichiers entiers, depuis `scaffold.md`)
- `CLAUDE.md` **§6** — région `<!-- derived:blueprint-pattern:start/end -->` uniquement (splice depuis le patron
  d'étapes du blueprint ; le reste du `CLAUDE.md` reste hand-authored).

Garde : `cockpit bundle derive --type browser-game --check` (exit 1 si drift) + `tests/test_derive.py`.
