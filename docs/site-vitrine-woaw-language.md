# Langage « woaw » — doctrine d'art-direction de l'archétype `site-vitrine`

> Doctrine de conception de la vitrine : le **langage visuel contraignant** que le scaffold outille et que la gate
> **woaw-critic** fait respecter. Symétrique de la doctrine UX du forgemaster (agent `cockpit-ux-critic`), côté site
> présentational. **Le woaw est produit par les workers, forcé par le capital** — cette doctrine EST le capital.

## Le gap qu'elle ferme

La gate a trois axes — toolchain (lint/types), Tier-1.5 (ça rend + a11y), Tier-1 (review) — et **aucun ne mesure
l'impact visuel**. Une page fade passe tout au vert : le drain récompense le *correct-pas-le-beau*. Une vitrine
propre mais **premium-sobre** (cartes uniformément bordées, titres en aplat plat, zéro texture, zéro profondeur,
zéro illustration) est un **échec d'objectif** qu'aucune force du système n'attrape aujourd'hui. On ajoute l'axe
manquant (§4) et on **arme le scaffold** (§3) pour que le worker ait les ingrédients ET la contrainte.

## 1. La frontière capital : générique (scaffold + gate) vs instance (projet)

Le bundle d'archétype est **générique**, enrichi **par dérivation**. Le seed `global.css` est **neutre** (accent
bleu, Inter, surfaces blanches) — et le reste : on ne brande pas le scaffold aux couleurs d'un projet, sinon toute
vitrine future naît dans cette marque.

| Couche | Où | Rôle |
|---|---|---|
| **Générique** | scaffold `site-vitrine` + gate woaw-critic | *discipline qui force la richesse*, agnostique de marque : §2 (langage), §3 (primitives), §4 (rubrique) |
| **Instance** | projet, par dérivation (worker) | *marque concrète* : la charte du projet → tokens + kit d'assets (palette, texture, wordmark, illustrations) |

**Règle d'or** : le générique **force** (toute vitrine doit produire du relief, du tissu, du drame) ; l'instance
**habille** (le worker applique la charte via tokens + assets). La gate juge le **générique toujours** (heuristiques
de richesse) et la **fidélité à la charte quand une charte est déclarée**.

## 2. Le langage woaw — 7 principes contraignants (génériques)

Chaque principe : **intention** → **l'anti-pattern qu'il tue** → **signal mesurable** (ce que la gate lit). Le cap
esthétique est un infographique dense, matiéré, hiérarchisé — jamais un mur d'aplats.

- **P1 · Matière, pas aplat** *(titre & surface)* — les surfaces focales (titre héro, bandeau, ring de logo) ont
  une **matière** (texture, gradient multi-stop ≥3 arrêts, masque texturé), pas un aplat mono-ton. *Tue* le titre
  plat et le fond uni pleine page. *Signal* : ≥1 surface matiérée par vue ; le H1 héro n'est pas un simple `color:`.
  *a11y* : le texte LISIBLE reste un aplat contrasté (≥3:1/4.5:1) ; la matière va en **décor** `aria-hidden` ou
  masque à fallback effectif — jamais du texte peint en dégradé (garde socle `layout.test.ts`).
- **P2 · Tissu, pas cartes** *(relief de mise en page)* — l'info vit dans un **tissu** en relief (rangées
  alternées, surfaces stratifiées, chevauchements, bandeaux pleine largeur), pas une grille de cartes bordées
  iso-morphes. *Tue* le *lazy default* « tout est une `<div class="border rounded p-6">` ». *Signal* : pas de vue
  dont >60 % des blocs sont des cartes iso-bordées ; ≥2 registres de surface (élevé/creusé).
- **P3 · Drame du héro** *(composition focale)* — le héros **compose** : contraste d'échelle (titre très grand vs
  corps), point focal non-textuel, respiration généreuse, asymétrie assumée. *Tue* « titre centré + sous-titre + 2
  boutons » sur fond uni. *Signal* : ratio d'échelle H1/corps ≥ ~2.5× ; un élément focal non-textuel ; densité de
  texte du héros sous plafond.
- **P4 · Densité d'ornement** *(rythme décoratif)* — des **ornements** ponctuent et signent (coins, séparateurs
  travaillés, sparkle, filets), présents mais **rythmés** (accent, pas bruit). *Tue* la page « document » sans
  signature, comme l'ornement partout. *Signal* : ≥1 ornement par section porteuse ; même famille réutilisée.
- **P5 · Voix typographique** *(display & wordmark)* — hiérarchie typographique dramatique : police d'affichage
  distincte du corps, wordmark **traité** (script/logo), échelle de titres franche. *Tue* le tout-en-Inter, le
  niveau de titre unique, le wordmark en texte brut. *Signal* : ≥2 rôles typo ; wordmark = traitement (image/SVG/
  police), pas un `<span>` nu ; ≥3 niveaux de titres nets.
- **P6 · Profondeur & relief** *(plans z)* — ombres portées crédibles, superpositions, halos/lueurs, léger
  parallaxe décollent les surfaces du fond. *Tue* le *flat design* total, tout au même plan. *Signal* : ≥2 plans z
  perceptibles ; ombres/halos non nuls sur les surfaces élevées ; aucune vue 100 % plate.
- **P7 · Mouvement retenu** *(motion)* — révélations au scroll, hover vivants, parallaxe léger, **toujours sous**
  `prefers-reduced-motion`. Le mouvement **sert** le drame, ne l'invente pas. *Tue* le mouvement gratuit comme
  l'immobilité totale d'un site premium. *Signal* : ≥1 transition signifiante ; toutes gardées RM ; zéro-JS reste
  le défaut (relief/ornement en CSS d'abord).

**Le fil rouge** : chaque principe **tue un aplat** et **exige une matière / un relief / un rythme**. Un worker qui
coche les 7 ne *peut plus* rendre un mur de texte + cartes bordées.

## 3. Primitives semées (génériques, neutres par défaut)

Le scaffold fournit les **ingrédients** — le worker les **compose et les thème par tokens**, il ne les invente pas.

| Primitive (`web/src/components/`) | Principe | Rôle |
|---|---|---|
| `TexturedTitle.astro` | P1 | titre display + matière DÉCOR `aria-hidden` (`--texture-title`) ; texte toujours en aplat contrasté |
| `Ornament.astro` | P4 | SVG décoratifs `corner`/`separator`/`sparkle`, `aria-hidden`, `currentColor` |
| `Hero.astro` | P3 | patron de héros (slots `eyebrow`/`title`/`lead`/`actions`/`focal`), contraste d'échelle intégré |
| `Surface.astro` | P2/P6 | registres de relief `raised`/`sunken`/`plain` via `--shadow-*` |

Tokens `@theme` (`global.css`), **neutres** : `--font-display` (fallback sur la sans), `--text-display-*`,
`--tracking-display`, `--shadow-raised`, `--shadow-halo`, `--color-surface-sunken`, `--texture-title: none`. Par
défaut une vitrine reste sobre-correcte ; une vitrine **avec charte** (l'instance branche les tokens + assets)
devient woaw. **Le scaffold ne brande rien — il outille et exige.**

**Propagation** : la **discipline** (`.claude/facets/frontend/METHOD.md §9`) est `reseed_owned` → elle atteint les
projets existants par `forgemaster scaffold reseed`. Les **primitives** et **tokens** sont des ingrédients de seed (non
owned : le worker possède et compose ses composants) ; sur un projet existant, le worker les re-dérive sous la
METHOD durcie + la gate. Le levier universel = **METHOD (propagée) + gate (qui force)**, pas l'écrasement des
composants du worker.

## 4. La gate woaw-critic — rubrique

Nouvel **axe esthétique** du pipeline, symétrique de `cockpit-ux-critic` : il juge le **RENDU** (screenshot
at-rest), jamais le code inféré. **Entrée** : screenshot des routes de la feature (mêmes routes que les
`verify-markers` du Tier-1.5) + intention (`acceptance`) + vérité DS (`frontmap`) + **charte du projet si
déclarée**. **Sortie** : verdict classé par les 7 axes P1–P7, chaque finding = sévérité (🔴 bloquant / 🟡 majeur /
🟣 mineur) + localisation + **fix concret**. Deux registres :

1. **Richesse générique (toujours)** — heuristiques P1–P7 du §2. Attrape le plat, les cartes iso-bordées, le héros
   sans focal, l'absence d'ornement/relief. Un seuil de plat (mur de texte + grille de cartes, zéro matière/relief)
   = 🔴.
2. **Fidélité à la charte (si déclarée)** — l'instance déclare sa charte (image + intention de tokens) ; le critic
   juge palette, texture, wordmark, densité d'ornement contre elle. Sans charte : registre 1 seul.

**Calibration** : la rubrique se **cale sur une page-référence** (bâtie par un worker, validée par l'humain) qui
fixe l'échelle « ça, c'est woaw ». On ne fige la gate **qu'après** ce calage. Le critic **ne hand-craft pas** : il
**juge** et renvoie des fix ; le worker corrige et re-passe. **Câblage** : agent `site-vitrine-woaw-critic`, invoqué
dans le gate front sur les routes portées par les `verify-markers`, bloquant selon sévérité (position exacte dans le
pipeline tranchée à l'implémentation de la gate).

## 5. Traçabilité principe → artefact

| Principe | Scaffold | Gate |
|---|---|---|
| P1 matière | `TexturedTitle` + `--texture-title` | ≥1 surface matiérée / vue |
| P2 tissu | `Surface` + `--shadow-*` | plafond cartes iso-bordées |
| P3 héro | patron `Hero` + `--text-display-lg` | focal + ratio d'échelle |
| P4 ornement | `Ornament` | ≥1 ornement / section |
| P5 typo | `--font-display` + échelle | ≥2 rôles typo, wordmark traité |
| P6 profondeur | `--shadow-raised/-halo` | ≥2 plans z, ombres non nulles |
| P7 mouvement | reveal CSS gardé RM | mouvement signifiant + RM |
