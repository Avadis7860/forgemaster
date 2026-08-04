# Templates de référence UI

Des **cibles visuelles concrètes** pour les projets que le forgemaster sème. Une session Claude qui
attaque l'UI d'un projet regarde le template de son type d'outil pour savoir « à quoi ça doit
ressembler » — au lieu de coder en aveugle et de re-dériver une identité à chaque fois.

Ce sont du **capital-token léger et montrable** : contrairement au corpus servi par le MCP
(blueprint/tech, privé), ces templates sont **shippés avec le forgemaster** (ils voyagent dans `web/dist`,
donc dans le wheel) et servis à `/templates/<slug>/` — un « petit plus » de la distribution.

## Anatomie d'un template

Un template = **un dossier self-contained** sous `web/public/templates/<slug>/` :

```
<slug>/
  index.html      # markup + <link> relatifs vers tokens.css & template.css + switch inline éventuel
  tokens.css      # L'IDENTITÉ : uniquement des design-tokens (:root plat / .app[data-theme]). Zéro composant.
  template.css    # LA STRUCTURE : composants + layout, ne lisent QUE des var(--…). Zéro valeur d'identité en dur.
  template.toml   # métadonnées sèches (nom, tool_type, genre, tags, intention, entry, preview)
  preview.png     # aperçu at-rest, régénéré par la boucle visuelle
```

## Contraintes (non négociables)

- **Zéro-build** : ouvrable seul dans un navigateur, aucun `npm`/`vite`. Assets en **chemins relatifs**
  (`./tokens.css`), jamais d'absolu `/assets/…` (casserait en `file://`). JS toléré s'il est **inline**
  et self-contained (aucun bundle externe).
- **Token-driven** : toute l'identité (couleurs, glow, radius…) vit dans `tokens.css`. `template.css` ne
  fait que consommer `var(--…)`. Changer un token → change tout le template ; re-thémer n'édite que
  `tokens.css`. Un template peut porter **plusieurs colorimétries** (scopes `.app[data-theme="…"]`) et
  un switch inline.
- **`file://`-capturable** : la boucle visuelle (`render_check.js`) screenshote le template at-rest sans
  serveur ; un `?theme=<nom>` permet de capturer chaque colorimétrie.
- **Doctrine UX** : le template passe l'agent critique `cockpit-ux-critic` (8 axes) sur son rendu.

## Ajouter un template

1. Copier un dossier existant (ex. `browser-game-spatial/`) sous un nouveau `<slug>`.
2. Éditer `tokens.css` (l'identité) puis `template.css`/`index.html` (la structure/contenu).
3. Renseigner `template.toml` — surtout `tool_type` (l'archetype) **et** `genre`/`tags` (le sous-genre,
   pour la catégorisation : tous les `browser-game` ne sont pas `spatial`).
4. Régénérer `preview.png` via `render_check.js` (goto-only, at-rest), puis passer l'agent critique UX.

Emplacement d'authoring de cette phase ; la maison-capital définitive des templates est tranchée plus tard.
