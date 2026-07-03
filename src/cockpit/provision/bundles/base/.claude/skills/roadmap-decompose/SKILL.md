---
name: roadmap-decompose
description: Décomposer une intention en une roadmap travaillable — features (chacune taguée d'une facette) et tasks (DAG depends_on + critères d'acceptation). C'est ce qui rend le projet dispatchable, séquencé et parallélisable.
inputs: [vision ou chunk de travail à planifier]
outputs: [roadmap — features[facet] + tasks[depends_on + acceptance] — prête à dispatcher]
related_catalogs: []
---

# roadmap-decompose — d'une intention à une roadmap travaillable

## Quand l'utiliser

Au **démarrage** du projet, et à chaque fois qu'un pan de travail neuf est à planifier. C'est l'étape qui
précède la boucle `work-loop` : elle transforme une **intention** (« je veux X ») en **unités dispatchables**
et séquencées. Un projet dont la roadmap est bien décomposée se travaille **seul** (un humain enchaîne les
features) **ou** s'automatise (`cockpit run` draine et parallélise) — aux mêmes artefacts.

## Le modèle (3 niveaux)

```
vision  →  features[facet]  →  tasks[depends_on + acceptance]
```

- **Feature** = **branche = worktree = mutex = un groupe cohérent de tasks**. Elle porte **une** facette
  (`backend` · `frontend` · `tool` · `doc`) qui **aligne le worker** (persona + méthode). Granularité :
  l'unité exacte de changement de casquette — si le travail passe du back au front en cours de route, ce sont
  **deux** features.
- **Task** = un **pas dispatchable** dans une feature. Elle déclare ses prérequis via `depends_on` (→ un
  **DAG** intra-feature : le résolveur ne dispatche qu'une task dont tous les prérequis sont `done`) et ses
  **critères d'acceptation** (`acceptance`) — la **DoD binaire** injectée telle quelle dans le prompt du
  worker. Une task sans `acceptance` = un worker sans définition de « fini ».

## Méthode

1. **Énonce l'issue mesurable** — que produit ce chunk, pour qui, à quel critère binaire de succès. Écris-le
   avant de découper (c'est la tête de la roadmap).
2. **Découpe en features par facette ET par indépendance.** Une frontière de feature tombe là où (a) la
   casquette change (back↔front↔tool↔doc) ou (b) un bloc peut avancer **sans attendre** un autre. Les features
   **indépendantes prêtes** tournent **en parallèle** (`cockpit run` ⇒ N workers) — les rendre indépendantes
   est ce qui débloque le parallélisme.
3. **Ordonne les features par dépendance = ordre de merge.** Les deps **inter-features** ne s'expriment pas
   dans un champ : elles se résolvent par le **merge vers `dev`**. Une feature `frontend` qui consomme une API
   se planifie **après** la feature `backend` — la worktree frontend, créée `base=dev`, verra le contrat mergé.
   Séquence back → merge → front.
4. **Dans chaque feature, pose le DAG des tasks (`depends_on`).** Ce qui est indépendant reste indépendant
   (deux tasks sans lien = deux `NEXT` prêtes, sérialisées par le mutex de la feature mais toutes deux
   drainées). Ne crée pas de dépendance factice « pour l'ordre » — elle bride le résolveur.
5. **Donne à chaque task ses critères d'acceptation.** Concrets, vérifiables (« l'endpoint `POST /x` renvoie
   422 si `y` manque, testé »), pas « faire marcher ». Ils **deviennent** le contrat que le worker doit
   satisfaire — c'est le levier qualité le plus direct.

## En pratique (cockpit ou manuel)

```bash
# Avec le cockpit (in-repo : features + tasks dans la roadmap du projet)
cockpit roadmap add-feature <projet> <feature> --facet backend
cockpit task add <projet>/<feature> <task> --depends-on <t-prereq> \
    --acceptance "Critère binaire, testé : … "
cockpit roadmap show <projet>          # relire le DAG
cockpit run <projet> --max-parallel 2  # drainer + paralléliser les features indépendantes prêtes
```

Sans cockpit, les mêmes artefacts se tiennent à la main (roadmap in-repo + boucle `work-loop` feature par
feature) — le modèle (facette par feature, DAG + acceptance par task) est ce qui compte, pas l'outil.

## Anti-patterns

- **Méga-feature multi-facettes** — un worker aligné `backend` qui doit finir en `frontend` : coupe en deux.
- **Task sans `acceptance`** — le worker n'a pas de DoD → il improvise la cible. Toujours un critère binaire.
- **Dépendance inter-features cachée** — back→front non exprimé par l'ordre de merge → le front part sans le
  contrat. Séquence par le merge, ne parallélise que l'indépendant.
- **DAG sur-contraint** — des `depends_on` factices « pour ranger » : ils tuent le parallélisme intra-feature.

## Sortie

Une roadmap où **chaque feature porte une facette**, **chaque task porte `depends_on` + `acceptance`**, les
features indépendantes peuvent paralléliser, et l'ordre de merge résout les deps back→front. Prête à
`work-loop` (manuel) ou `cockpit run` (automatisé).
