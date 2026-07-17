---
name: first-session-interview
description: Mener l'interview de 1ʳᵉ session d'un projet neuf — cadrer l'intention avec l'humain (dans le terminal, pas en headless), la fixer dans la doc de design, puis dériver la roadmap de travail via roadmap-decompose. C'est le point d'entrée qui personnalise un projet générique semé.
inputs: [projet neuf semé (socle nu), humain présent au terminal]
outputs: [doc de design renseignée, roadmap de travail authorée (≥1 feature), socle prêt à drainer]
related_catalogs: []
---

# first-session-interview — d'un socle nu à une roadmap de travail

## Quand l'utiliser

À la **toute première session** d'un projet neuf, quand la forge te lance via `cockpit interview <projet>`
(un des rares moments **terminal-légitimes** : la personnalisation exige un humain). Le projet a été semé
avec un **socle nu** — une intention « à renseigner » et aucune feature de travail. Ta mission : transformer
ce socle en une roadmap **drainable** par la boucle autonome.

Tu tournes en **INTERACTIF** (un humain est en face). Tu n'es PAS un worker headless : **pose des questions**,
propose, reformule, jusqu'à ce que l'intention soit nette. C'est une interview, pas une exécution muette.

## Le résultat visé (critère binaire)

1. La doc de design du projet ne porte **plus aucun « à renseigner »** : ce que le projet fait, pour qui, et
   le critère binaire de « fini » du premier jalon sont **écrits**.
2. La roadmap porte **≥1 feature de travail** (facette + tasks avec `depends_on` + `acceptance`), et
   `cockpit roadmap check <projet>` est **vert**.

Tant que ces deux points ne sont pas atteints, le socle reste ouvert (la forge le vérifie à ta sortie).

## Protocole

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
cockpit roadmap add-feature <projet> <feature> --facet <facette> --title "<titre>"
cockpit task add <projet>/<feature> <task> --depends-on <t-prereq> \
    --acceptance "Critère binaire, testé : …"
cockpit roadmap check <projet>          # doit finir VERT (0 issue) — l'autorité de complétude
```

Chaque feature porte **une** facette du bundle du projet ; chaque task porte une **`acceptance` binaire**
(sans elle, le worker n'a pas de définition de « fini »). Les deps back→front se résolvent par l'ordre de
merge (cf. `roadmap-decompose`), pas par un champ inter-feature.

### 4. Rendre la main
Préviens l'humain quand l'intention est fixée et la roadmap authorée. La forge **vérifie** (roadmap check
vert + ≥1 feature de travail) et clôt le socle en `done` — tu n'as pas à marquer les tasks du socle toi-même.
`cockpit run <projet>` prend alors le relais et draine les features de travail en headless.

## Anti-patterns

- **Cadrer sans l'humain** — tu es en interactif *pour* l'interviewer ; ne devine pas l'intention.
- **Doc de design laissée « à renseigner »** — l'intention floue produit une roadmap bancale.
- **Roadmap non vérifiée** — ne rends pas la main tant que `roadmap check` n'est pas vert avec ≥1 feature.
- **Task sans `acceptance`** — le worker headless improvisera sa cible. Toujours un critère binaire.
