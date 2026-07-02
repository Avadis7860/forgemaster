# CLAUDE.md — projet auto-travaillable (semé par le cockpit)

> Lu au début de **chaque** session dans ce repo. Ce projet a été **créé par le cockpit** avec un toolkit
> minimal qui le rend **travaillable seul** : un clone suffit pour qu'un worker — IA `claude` **ou** humain —
> le fasse évoluer en sûreté, **sans centre de contrôle**. Le cockpit (forge) ne fait qu'**automatiser** la
> même boucle par-dessus ; il reste optionnel, aux **mêmes invariants**.
>
> Ce fichier = **règles + index + outils**, PAS la spec du produit. Le détail (intention, roadmap,
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
- **Anti-archéologie** : la prose de `docs/` se **requête** (`docsmap where "<intention>"` → la tranche
  pertinente), jamais ne se lit en bloc pour s'orienter.

## Index (interroge, ne lis pas en bloc)

La doc du projet vit dans `docs/`. **Ne la lis pas en bloc pour t'orienter** — `docs-map` (injecté) répond à
l'intention ; lis ensuite **seulement** la tranche `fichier:lignes` renvoyée :

```
docsmap where "<intention>"     # → docs/…:lignes de la section pertinente
docsmap sections                # table des matières
```

- `docs/architecture.md` — le point de départ : ce qu'est ce projet, où vit quoi, comment il se travaille.

## Outils à disposition (embarqués dans ce repo)

- **Skills** (`.claude/skills/`) : `work-loop` (boucle de travail sûre, lightweight, sans cockpit) ·
  `quality-gate` (porte qualité avant tout commit).
- **Carte de doc** : `docsmap where/sections/read/check` sur la prose `docs/` de ce repo (injecté, zéro-dép).

## Rapport au cockpit

Le cockpit fait **exactement** ceci — worktree feature comme mutex, gate, merge, GO humain fail-closed — mais
**automatisé**, multi-projet, avec DB + web. Ce fichier + les skills sont le même contrat en **manuel** :
suffisant pour faire vivre ce clone seul. Étoffe `docs/` au fil du projet (c'est là que vit sa mémoire) ;
`docsmap` la gardera interrogeable.
