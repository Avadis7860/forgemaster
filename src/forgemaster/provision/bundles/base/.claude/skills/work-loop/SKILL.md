---
name: work-loop
description: Boucle de travail sûre et lightweight sur CE repo — sans centre de contrôle. Toujours sur une worktree feature depuis dev, gate vert, main jamais cassé. Version manuelle de ce que le forgemaster automatise.
inputs: [sujet de la feature]
outputs: [feature mergée dans dev, gate vert, worktree nettoyée]
related_catalogs: []
---

# work-loop — travailler ce repo en autonomie légère (sans forgemaster)

## Quand l'utiliser

À **chaque** évolution du repo, que tu sois une IA (`claude`) ou un humain, sur un simple clone GitHub.
C'est la version **manuelle et lightweight** de la boucle que le **forgemaster automatise** (dispatch →
worktree → gate → merge). Aucun daemon, aucune DB, aucun réseau requis : juste `git` + le skill
`quality-gate`. Le repo est **auto-travaillable seul** ; le forgemaster est un orchestrateur *optionnel*
par-dessus, aux **mêmes invariants**.

## Invariants (non négociables)

1. **`main` est protégé** : ce n'est **jamais** la surface de travail. Il n'avance **que** par fast-forward
   depuis un `dev` vert. Jamais l'inverse, jamais un commit direct.
2. **Tout travail vit sur `feature/<sujet>`**, créée **depuis `dev`**, dans une **worktree isolée**.
3. **Aucun merge sans gate vert** (`quality-gate`). Un acte **irréversible** (merge, destroy, push distant)
   exige un **feu vert humain explicite** — fail-closed (une IA ne merge/ne pousse jamais seule).

## Boucle

```bash
REPO=$(git rev-parse --show-toplevel)        # racine du repo courant
FEAT=<sujet-kebab-case>                       # ex. tsconfig-alias-resolution

# 1. Partir de dev à jour, dans une worktree feature isolée
git -C "$REPO" fetch --prune
git -C "$REPO" worktree add "../$(basename "$REPO")-$FEAT" -b "feature/$FEAT" origin/dev
cd "../$(basename "$REPO")-$FEAT"

# 2. S'orienter puis travailler (IA ou humain). Anti-archéologie : bâtir les cartes du repo une fois
#    (les index sont gitignorés → absents sur une worktree fraîche) PUIS interroger par intention, jamais grep :
#      codemap build && docsmap build && frontmap build     # saute ceux sans objet (p. ex. pas de front)
#      codemap where "<intention>"   # code   · docsmap where "<intention>"  # prose · frontmap where … # UI

# 3. Gate — skill `quality-gate` (ruff + mypy + pytest + déterminisme [+ front gate si web]).
#    Rouge → corriger la CAUSE, jamais contourner ni déplacer un seuil.

# 4. E2E si pertinent :
#      - surface web  → boucle visuelle (screenshot + Read) / render_check ;
#      - CLI / daemon → smoke réel (--help répond, la commande produit le RÉSULTAT attendu).
#    L'IA propose ; un humain valide tout effet irréversible.
```

Puis, **après feu vert** :

```bash
# 5. Lander dans dev (ff-only : refuse si dev a divergé → rebaser d'abord)
git -C "$REPO" switch dev && git -C "$REPO" merge --ff-only "feature/$FEAT"

# 6. Promouvoir main — DÉLIBÉRÉ, seulement quand dev est vert (jamais l'inverse)
git -C "$REPO" switch main && git -C "$REPO" merge --ff-only dev

# 7. Publier + nettoyer
git -C "$REPO" push origin dev main
git -C "$REPO" worktree remove "../$(basename "$REPO")-$FEAT"
git -C "$REPO" branch -d "feature/$FEAT"
```

## Sortie

Une feature **complète**, gate vert, mergée dans `dev` en ff-only, `main` promu depuis un `dev` vert, la
worktree et la branche nettoyées. À aucun moment `main` n'a été la surface de travail ni cassé.

## Rapport au forgemaster

Le forgemaster fait **exactement** ceci — worktree feature comme mutex, gate multi-tier, merge internal-first,
GO humain fail-closed — mais **automatisé**, **multi-projet**, avec DB + web. Ce skill est le même contrat
en **manuel** : suffisant pour faire vivre un clone seul. Si tu veux l'automatiser sur un seul repo sans le
forgemaster complet, c'est le moment d'écrire un petit script `work`/`land` (déférer tant qu'il n'y a pas de
friction réelle — sobriété).
