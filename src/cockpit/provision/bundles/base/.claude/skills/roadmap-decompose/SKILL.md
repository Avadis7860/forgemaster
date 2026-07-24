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
3. **Ordonne les features par dépendance = ordre de merge.** Les deps **inter-features** s'expriment via le
   champ `depends_on` **au niveau feature** (`--depends-on` d'`add-feature`, ou `roadmap set-deps` après coup) :
   une feature reste **non-dispatchable** tant qu'une prérequise n'est pas `merged` — soit exactement l'ordre
   de merge `back → front`. Une feature `frontend` qui consomme une API se planifie **après** la feature
   `backend` (la worktree frontend, créée `base=dev`, verra le contrat mergé). Ne pose une dep inter-feature
   que là où le merge est un vrai prérequis — sinon tu sérialises ce qui pourrait paralléliser.
4. **Dans chaque feature, pose le DAG des tasks (`depends_on`).** Ce qui est indépendant reste indépendant
   (deux tasks sans lien = deux `NEXT` prêtes, sérialisées par le mutex de la feature mais toutes deux
   drainées). Ne crée pas de dépendance factice « pour l'ordre » — elle bride le résolveur.
5. **Donne à chaque task ses critères d'acceptation.** Concrets, vérifiables (« l'endpoint `POST /x` renvoie
   422 si `y` manque, testé »), pas « faire marcher ». Ils **deviennent** le contrat que le worker doit
   satisfaire — c'est le levier qualité le plus direct.
6. **Critique de complétude (dernière passe, AVANT de rendre).** Une roadmap dispatchable n'est pas une
   roadmap **profonde**. Confronte-la à la question **produit**, pas seulement découpage : *pour un livrable
   **complet et de qualité** de CE type, quels axes ne sont pas couverts ?* Passe l'intention contre les axes
   propres au type de livrable — **pour un jeu** : convergence d'équilibrage (prouvé *bon*, pas seulement
   *correct*), persistance de session, pression/qualité des adversaires, lisibilité/onboarding, états
   d'échec & bords, rejouabilité/contenu ; **pour un outil/service** : robustesse aux erreurs, observabilité,
   doc d'usage, perf/charge, migration/compat. Chaque axe pertinent : **couvert par une feature, ou différé
   EXPLICITEMENT avec raison**. Un axe omis en silence = une dette qui se découvre en production.

## En pratique (cockpit ou manuel)

```bash
# Avec le cockpit (in-repo : features + tasks dans la roadmap du projet)
cockpit roadmap add-feature <projet> <feature> --facet backend --depends-on <feat-prereq>
cockpit task add <projet>/<feature> <task> --depends-on <t-prereq> \
    --acceptance "Critère binaire, testé : … "
# Corriger une dépendance DÉCOUVERTE APRÈS COUP (ex. la critique §6 révèle un prérequis manquant) —
# jamais en éditant cockpit.db à la main : les verbes valident (refus dangling/cycle/self) et écrivent atomiquement.
cockpit roadmap set-deps <projet> <feature> --depends-on <feat-prereq...>   # REMPLACE les deps inter-feature
cockpit task set-deps <projet>/<feature> <task> --depends-on <t-prereq...>  # REMPLACE les deps intra-task
cockpit roadmap show <projet>          # relire le DAG
cockpit roadmap check <projet>         # prouver : DoD/DAG/facettes OK (0 issue = drainable)
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
- **Édition du DAG en raw-SQL** — corriger une dépendance en ouvrant `cockpit.db` (`sqlite3`/`UPDATE`) contourne
  la validation (dangling/cycle) et corrompt le résolveur en silence. Utilise `roadmap set-deps` / `task set-deps`.
- **Roadmap plate** — décomposition dispatchable mais sans profondeur : toutes les features prouvent que « ça
  tourne », aucune ne prouve que « c'est bon » (résultat convergé/équilibré, contenu suffisant, axes du type
  couverts). Passe la **critique de complétude** (méthode §6) avant de rendre.

## Sortie

Une roadmap où **chaque feature porte une facette**, **chaque task porte `depends_on` + `acceptance`**, les
features indépendantes peuvent paralléliser, et l'ordre de merge résout les deps back→front. Prête à
`work-loop` (manuel) ou `cockpit run` (automatisé).
