---
name: first-session-interview
description: Mener l'interview de 1ʳᵉ session d'un projet neuf — cadrer l'intention avec l'humain (dans le terminal, pas en headless), la fixer dans la doc de design, puis dériver la roadmap de travail via roadmap-decompose. Deux passes OBLIGATOIRES — amorçage (MVP dispatchable) puis profondeur (couvre-ou-diffère les axes-qualité de l'archétype). C'est le point d'entrée qui personnalise un projet générique semé.
inputs: [projet neuf semé (socle nu), humain présent au terminal]
outputs: [doc de design renseignée, roadmap de travail authorée (≥1 feature), socle prêt à drainer]
related_catalogs: []
---

# first-session-interview — d'un socle nu à une roadmap de travail

## Quand l'utiliser

À la **toute première session** d'un projet neuf, quand la forge te lance via `forgemaster interview <projet>`
(un des rares moments **terminal-légitimes** : la personnalisation exige un humain). Le projet a été semé
avec un **socle nu** — une intention « à renseigner » et aucune feature de travail. Ta mission : transformer
ce socle en une roadmap **drainable** par la boucle autonome.

Tu tournes en **INTERACTIF** (un humain est en face). Tu n'es PAS un worker headless : **pose des questions**,
propose, reformule, jusqu'à ce que l'intention soit nette. C'est une interview, pas une exécution muette.

## Le résultat visé (critère binaire)

1. La doc de design du projet ne porte **plus aucun « à renseigner »** : ce que le projet fait, pour qui, et
   le critère binaire de « fini » du premier jalon sont **écrits**.
2. La roadmap porte **≥1 feature de travail** (facette + tasks avec `depends_on` + `acceptance`), et
   `forgemaster roadmap check <projet>` est **vert**.
3. La roadmap **couvre la profondeur de l'archétype**, pas seulement l'amorçage — sinon on livre un projet qui
   *tourne* sans être *complet*. Pour chaque **axe-qualité de l'archétype du livrable** (jeu / outil / service /
   doc — cf. `roadmap-decompose` §6 « critique de complétude ») : **une feature le couvre, ou il est différé
   EXPLICITEMENT avec raison** (trace machine-lisible que `roadmap check` vérifie). Un axe omis en silence est
   une dette qui se découvre en production — le gate de profondeur le refuse (`UNCOVERED_AXIS`).

Tant que ces trois points ne sont pas atteints, le socle reste ouvert (la forge le vérifie à ta sortie).

> **Deux passes, jamais une.** Ta pente naturelle (comme tout worker) est de t'arrêter au **minimum viable** :
> une roadmap dispatchable + `check` vert. Ce n'est que la **passe A**. La **passe B (profondeur) est
> obligatoire** avant de rendre la main — c'est elle qui distingue « ça tourne » de « c'est complet ».

## Protocole

## Passe A — amorçage (l'intention et le MVP dispatchable)

### 1. Interviewer pour cadrer l'intention
Interroge l'humain — par petits lots de questions, pas une à la fois — jusqu'à pouvoir écrire, sans inventer :
- **Quoi / pour qui** : ce que le projet produit, son utilisateur, le problème résolu.
- **Premier jalon** : le plus petit incrément qui a de la valeur, avec son **critère binaire** de succès.
- **Contraintes** : ce qui est hors-scope, les décisions déjà prises, les invariants à respecter.

### 2. Fixer l'intention dans la doc de design
Écris le cadrage dans la **doc de design du projet** (ta facette sait laquelle — suis sa `METHOD.md` ; par
défaut `docs/architecture.md` § « Intention »). Remplace tout « à renseigner » par du concret et vérifiable.
Après avoir touché `docs/`, rafraîchis l'index : `docsmap build`.

### 3. Dériver la roadmap de travail (skill roadmap-decompose)
Applique le skill **`roadmap-decompose`** : décompose l'intention en **features (une facette chacune)** et
**tasks (`depends_on` + `acceptance`)**. AUTHORE-les dans le board via la vraie CLI :

```bash
forgemaster roadmap add-feature <projet> <feature> --facet <facette> --title "<titre>"
forgemaster task add <projet>/<feature> <task> --depends-on <t-prereq> \
    --acceptance "Critère binaire, testé : …"
forgemaster roadmap check <projet>          # doit finir VERT (0 issue) — l'autorité de complétude
```

Chaque feature porte **une** facette du bundle du projet ; chaque task porte une **`acceptance` binaire**
(sans elle, le worker n'a pas de définition de « fini »). Les deps back→front se résolvent par l'ordre de
merge (cf. `roadmap-decompose`), pas par un champ inter-feature.

## Passe B — profondeur (OBLIGATOIRE avant de rendre la main)

La passe A donne un socle *dispatchable*. Elle ne garantit **pas** un livrable *complet*. Ne rends jamais la
main à la fin de la passe A : fais d'abord la passe de profondeur.

### 4. Passe de profondeur — couvrir-ou-différer les axes de l'archétype
Applique la **critique de complétude de `roadmap-decompose` (§6)** : confronte la roadmap à la question
**produit**, pas au découpage. Pour l'**archétype du livrable** (jeu / outil / service / doc), parcours ses
**axes-qualité** et statue **chacun** :

- soit **une feature le couvre** (ajoute-la avec ses tasks + `acceptance` — même règles que la passe A) ;
- soit il est **différé EXPLICITEMENT** avec une raison (une décision assumée, pas un oubli) — trace
  machine-lisible dans `.forgemaster/deferred-axes.yaml` (à la racine du worktree), `{axe: raison}` :

```yaml
# .forgemaster/deferred-axes.yaml — axes de l'archétype assumés HORS périmètre de ce socle (raison obligatoire)
replayability: "run unique au MVP ; méta-progression après validation de la boucle"
```

Puis **vérifie le gate de profondeur** (couvre-ou-diffère chaque axe) avant de rendre la main :

```bash
forgemaster roadmap check <projet> --depth   # doit finir VERT — chaque axe couvert OU différé (raison non vide)
```

Exemples d'axes (non exhaustif, cf. `roadmap-decompose §6`) : **jeu** — équilibrage *convergé* (prouvé *bon*,
p.ex. corridor de win-rate, pas seulement *correct*), persistance de session, qualité des adversaires,
lisibilité, bords/états d'échec, rejouabilité ; **outil/service** — robustesse aux erreurs, observabilité,
doc d'usage, perf/charge, migration/compat. Un axe **omis en silence** ⇒ `roadmap check` lève `UNCOVERED_AXIS`
**et la forge REFUSE de clore le socle** (le gate de profondeur est contraignant à la clôture, pas seulement
opt-in en CLI).

### 5. Rendre la main
Préviens l'humain quand l'intention est fixée, la roadmap authorée **et la passe de profondeur close** (chaque
axe couvert ou différé tracé). La forge **vérifie** (roadmap check vert + **profondeur d'archétype couverte** +
≥1 feature de travail) et clôt le socle en `done` — tu n'as pas à marquer les tasks du socle toi-même. Un socle
à roadmap plate (un axe non statué) **ne se clôt pas** : la forge te rend la main avec `UNCOVERED_AXIS`. `forgemaster run <projet>` prend alors le
relais et draine les features de travail en headless.

## Anti-patterns

- **Cadrer sans l'humain** — tu es en interactif *pour* l'interviewer ; ne devine pas l'intention.
- **Doc de design laissée « à renseigner »** — l'intention floue produit une roadmap bancale.
- **Rendre la main au minimum viable** — s'arrêter à la fin de la passe A (roadmap dispatchable + `check` vert)
  en sautant la passe de profondeur : c'est le défaut par défaut de tout worker. La passe B n'est pas optionnelle.
- **Axe de l'archétype omis en silence** — laisser tomber un axe-qualité sans le couvrir NI le différer avec
  raison. Couvre-le, ou diffère-le explicitement (le gate de profondeur refuse le drop silencieux).
- **Roadmap non vérifiée** — ne rends pas la main tant que `roadmap check` n'est pas vert avec ≥1 feature.
- **Task sans `acceptance`** — le worker headless improvisera sa cible. Toujours un critère binaire.
