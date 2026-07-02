# .claude/ — contexte & starter pack de session (bundle `cockpit`)

Rend le repo **auto-décrivant et outillé** : une session Claude ouverte ici (ou quand le repo est monté
dans un projet) démarre câblée, orientée, et gated — sans configuration manuelle.

| Élément | Rôle |
|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | Constitution **mince** : règles non négociables + **index** vers `docs/` (+ `docs/specs/`) + **outils** embarqués (le détail vit dans `docs/`, pas ici). |
| [`../PORTING.md`](../PORTING.md) | Journal de réimplémentation *couche par couche* (état vivant : quel module porté/testé). |
| `output-styles/tool-builder.md` | Persona : déterministe d'abord, schéma figé, zéro cap silencieux, générique par config. |
| `skills/work-loop/` | Boucle de travail **sûre et lightweight** (worktree feature depuis `dev` → gate → `dev` ff-only → `main` promu) — la forge l'**automatise**, ici on la **dogfoode** à la main. |
| `skills/quality-gate/` | Gate ruff + mypy + pytest + **smoke réponse** (CLI/daemon/socle/DB) — avant chaque commit. |
| `skills/port-tool/` | Le workflow récurrent : porter un stub (source vault → refactor `#N` → module + test → gate). |
| `hooks/post-edit-check.py` | `PostToolUse` (Write\|Edit) : `py_compile` + ruff léger sur le `.py`/`.json`/`.toml` touché, non bloquant. |
| `templates/module.py.tmpl` · `templates/test_module.py.tmpl` | Scaffolds d'un module porté + test. |
| `settings.json` | Câble le hook + `outputStyle` + permissions. |

## Origine (bibliothèque de bundles)

Bundle **`cockpit`** dérivé de l'archétype `tool-builder` (repos frères : `code-map`, `mcp-catalogs`),
persona `tool-builder`. Le cockpit n'est pas un port mécanique : c'est une **réimplémentation propre** de
l'orchestrateur legacy — on importe les décisions distillées comme **specs** (`docs/specs/`), le registre
`docs/weak-points.md` liste les dettes refusées et le refactor décidé. La **source canonique** du bundle
est le vault (`bundles/`) ; ici c'est l'instance qui **voyage avec le repo**. Faire évoluer le contexte
durablement = modifier le bundle côté vault **puis** re-vendorer — ne pas laisser diverger.

_(Capture formelle dans `bundles/cockpit/` côté vault : chunk séparé, après validation de la structure.)_
