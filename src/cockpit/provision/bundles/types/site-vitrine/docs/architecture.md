# Architecture — site vitrine (Astro SSG, i18n)

> Point de départ de la doc de ce projet. Ce type = un **site vitrine** statique orienté contenu (Astro SSG),
> multilingue (EN/FR/DE), avec Tailwind (design tokens), des îlots React à la demande et du contenu en MDX
> (content-collections typées). Toolchain composite `astro check` + `tsc` + `vitest` + `astro build`. UI indexée
> par `frontmap`, logique par `codemap`. Étoffe cette page ; `docsmap where` la rend interrogeable.

## Intention
_(À renseigner.)_ Ce que le site présente, pour quels visiteurs, et le critère de succès (le message porté,
ce qui s'affiche).

## Où vit quoi
- `web/` — l'app Astro (le groupe de gate `front` se déclenche par ce chemin). `web/src/pages/` (par locale),
  `web/src/layouts/` (SEO/méta/a11y), `web/src/components/` (îlots React), `web/src/content/` (MDX typé),
  `web/src/styles/` (design tokens), `web/src/i18n/` (dictionnaires + helpers). `frontmap where` pour
  tokens/primitives ; `codemap where` pour la logique.
- `docs/` — intention, décisions, design-system, contenu éditorial. Via `docsmap`.
- Racine — la config de déploiement (`Dockerfile` build statique → nginx, `compose.yaml`, `nginx.conf`).

## Comment ce projet se travaille
Trois facettes : **frontend** (défaut — boucle visuelle : screenshot puis lecture AVANT de livrer tout écran ;
zéro-JS par défaut, hydratation sélective des rares îlots), **content** (rédaction MDX + parité i18n EN/FR/DE en
content-collections typées) et **deploy** (build statique servi par nginx sur `:8000`). Le **contenu précède
l'habillage** : une section se pose d'abord comme collection typée (les 3 locales en parité), puis le layout la
consomme. Accessibilité (wai-aria-apg) et SEO sont des passes d'**acceptance**, jamais « à peaufiner ». Boucle
`work-loop`, gate composite vert avant tout commit.

## Contenu vs cadre
Ce squelette porte le **cadre** (stack verrouillée, layouts, tokens, i18n, îlots, déploiement). Le **contenu
réel** (copy, message, identité visuelle, imagerie) est propre au projet et vit dans les content-collections +
les valeurs des tokens — pas dans le squelette. Au démarrage, applique le blueprint `site-vitrine`
(`query(type=blueprint, scope=site-vitrine)`) : décisions verrouillées + patron d'étapes.
