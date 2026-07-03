# CLAUDE.md — projet auto-travaillable (semé par le cockpit)

> Lu au début de **chaque** session dans ce repo. Ce projet a été **créé par le cockpit** avec un toolkit
> minimal qui le rend **travaillable seul** : un clone suffit pour qu'un worker — IA `claude` **ou** humain —
> le fasse évoluer en sûreté, **sans centre de contrôle**. Le cockpit (forge) ne fait qu'**automatiser** la
> même boucle par-dessus ; il reste optionnel, aux **mêmes invariants**.
>
> Ce fichier = **règles + cartes + outils**, PAS la spec du produit. Le détail (intention, roadmap,
> architecture) vit dans `docs/` — **interroge-le** (`docsmap where`), ne le recopie pas ici.

## Règles (non négociables)

- **Boucle de travail** : tout changement passe par le skill **`work-loop`** — worktree `feature/<sujet>`
  créée **depuis `dev`**, gate vert, puis `dev` en ff-only. **`main` ne se travaille jamais** : il n'avance
  que promu depuis un `dev` vert. Jamais de commit direct sur `main`/`dev`.
- **Gate avant merge** : le skill **`quality-gate`** doit être **vert** (lint + types + tests, selon la
  toolchain du projet). Un acte irréversible (merge/destroy/push distant) = **feu vert humain, fail-closed**
  (une IA ne merge/ne pousse jamais seule).
- **Anti-boucle** : n'invente pas une signature d'API « de mémoire » — lis le code ou la doc avant d'écrire
  un import non trivial. Pas de signature inventée → pas d'erreur d'exécution → pas de retry.
- **Anti-archéologie** : ne **fouille jamais à l'aveugle** (grep/lecture en bloc) pour t'orienter. Interroge
  d'abord la **carte** de la couche visée — le **code** via `codemap where`, la **prose** `docs/` via
  `docsmap where`, l'**UI** via `frontmap` — puis lis **seulement** la tranche `fichier:lignes` renvoyée.

## Cartes du repo (bâtis d'abord, puis interroge)

Trois cartes déterministes couvrent ce repo. Leur **config** (`.codemap.toml`, `.docsmap.toml`,
`.frontmap.toml`) voyage avec le repo ; leur **index est dérivé, per-racine et gitignoré** → **absent sur un
clone ou une worktree fraîche**. Première chose à faire en s'orientant : **bâtir les index une fois** (saute
ceux qui ne s'appliquent pas — p. ex. pas de front `web/` → `frontmap` sans objet) :

```
codemap build && docsmap build && frontmap build     # bâtit .codemap/ .docsmap/ .frontmap/ (idempotent)
```

Un verbe de lecture lancé **sans** l'index bâti te le dira (« index absent — lance `… build` ») — bâtis,
puis requête. Ensuite, oriente-toi par **intention**, sans grep :

```
codemap where "<intention>"     # → code : symbole fichier:ligne le plus pertinent
codemap subsystems              # vue d'altitude ; codemap callers/imports <cible> pour le graphe
docsmap where "<intention>"     # → docs/…:lignes de la section pertinente ; docsmap sections = sommaire
frontmap where "<intention>"    # → UI : token / primitive / route (repos avec front seulement)
```

- `docs/architecture.md` — le point de départ : ce qu'est ce projet, où vit quoi, comment il se travaille.

## Outils à disposition

- **Skills** (`.claude/skills/`, embarqués dans ce repo) : `work-loop` (boucle de travail sûre, lightweight,
  sans cockpit) · `quality-gate` (porte qualité avant tout commit).
- **Cartes** `codemap` · `docsmap` · `frontmap` : leurs **configs** sont dans ce repo ; les **binaires** sont
  fournis par l'**environnement** (le cockpit les installe sur l'hôte — ils ne sont **pas** dans le repo). Sur
  un clone nu **sans** cockpit, installe-les d'abord (paquets `code-map`/`docs-map`/`front-map`) ; sinon la
  config est là mais la commande manque.

## Rapport au cockpit

Le cockpit fait **exactement** ceci — worktree feature comme mutex, gate, merge, GO humain fail-closed — mais
**automatisé**, multi-projet, avec DB + web. Ce fichier + les skills sont le même contrat en **manuel** :
suffisant pour faire vivre ce clone seul. Étoffe `docs/` au fil du projet (c'est là que vit sa mémoire) ;
`docsmap` la gardera interrogeable.
