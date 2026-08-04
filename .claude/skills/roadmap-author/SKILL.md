---
name: roadmap-author
description: Transformer une intention en langage naturel en une roadmap OPÉRATIONNELLE sur n'importe quel projet forgemaster, en pilotant la vraie CLI. Émet une séquence exécutable add-feature/task add complète (priorité + deps + DoD + facette) qui se termine par `roadmap check` — le gate qui certifie la roadmap drainable par l'orchestrateur.
inputs: [projet cible, intention en langage naturel]
outputs: [séquence forgemaster exécutée, roadmap dont `roadmap check` retourne 0 issue]
related_catalogs: []
---

# roadmap-author — d'une intention à une roadmap drainable

## Quand l'utiliser

Depuis l'hôte forgemaster, quand un humain décrit une intention (« je veux un service qui expose une API de
scores avec auth ») et veut une **roadmap opérationnelle** (features + tasks) sur un projet existant, prête
à être **drainée** par `forgemaster run`. C'est un **assist opérateur** : tu pilotes la **vraie CLI** contre le
projet cible — tu n'écris pas de fichier roadmap à la main, tu émets des commandes `forgemaster`.

Distinct du skill semé `roadmap-decompose` (livré *dans* les projets, il enseigne le *quoi* : le modèle de
décomposition à 3 niveaux). Ici on ajoute le *comment opérationnel* : commandes complètes, **priorité
explicite**, **DoD obligatoire**, **facette par feature**, et un **gate final** qui refuse une roadmap non
drainable.

## Invariant : une roadmap n'est « faite » que si `roadmap check` est vert

Le gate `forgemaster roadmap check <projet>` (exit 1 dès une issue) est l'autorité de complétude. Une feature
sans facette, une task sans DoD, une dépendance dangling ou un cycle **bloquent**. Ta séquence n'est
terminée que quand `check` retourne **0 issue**.

## Protocole

### 1. Cartographier le projet cible (ne rien inventer)
```bash
forgemaster project list                     # le projet existe-t-il ? (sinon : forgemaster project create …)
forgemaster bundle show <type>               # les FACETTES disponibles pour ce type (le vocab de --facet)
forgemaster roadmap show <projet>            # l'état actuel (features/tasks déjà là — ne pas dupliquer)
```
La facette d'une feature **doit** appartenir aux facettes du bundle du projet (sinon `check` la rejette en
`BAD_FACET`). Si aucune facette ne correspond à une étape (ex. « test », « infra »), c'est un **manque de
worker spécialiste** à remonter en backlog — ne force pas une facette inadaptée.

### 2. Décomposer (modèle à 3 niveaux — cf. `roadmap-decompose`)
Feature = branche = unité de merge (une **facette** de travail). Task = unité de dispatch séquentielle, avec
sa **DoD binaire** et ses `depends_on` **intra-feature** (slugs de la même feature ; pas de dépendance
inter-features — celles-ci se résolvent par l'ordre de merge).

### 3. Émettre la séquence exécutable
Une feature par facette, puis ses tasks ordonnées par `depends_on`, chacune avec **priorité** et **DoD** :
```bash
forgemaster roadmap add-feature <projet> <feature> --facet <facette> --title "<titre>"

forgemaster task add <projet>/<feature> <task> \
    --priority P0 \
    --acceptance "Critère BINAIRE et testé : ce qui prouve que la task est finie."
forgemaster task add <projet>/<feature> <task-2> \
    --depends-on <task> \
    --priority P1 \
    --acceptance "Critère binaire … (test inclus)."
```
Règles de fidélité :
- **`--acceptance` est obligatoire** et doit être un critère *vérifiable* (« le endpoint /health répond 200
  et un test le couvre »), pas une reformulation du titre.
- **`--priority`** P0–P3 explicite (P0 = fondation débloquante). Le résolveur remonte la priorité effective
  d'une task qui en débloque une plus prioritaire — ordonne par dépendance, pas par micro-priorité.
- `--depends-on` prend des **slugs de tasks de la même feature**.

### 4. Certifier (gate final — non négociable)
```bash
forgemaster roadmap check <projet>
```
Vert (exit 0 + récap) → la roadmap est **opérationnelle**, drainable par `forgemaster run <projet>`.
Rouge → corrige la **cause** (ajoute la DoD/facette manquante, répare la dépendance) et **re-check**. Ne
contourne jamais le gate.

## Sortie

Une roadmap sur le projet cible dont **`forgemaster roadmap check` retourne 0 issue** : chaque feature porte une
facette connue du bundle, chaque task porte une DoD binaire et un DAG `depends_on` sain. Prête pour
`forgemaster run`.
