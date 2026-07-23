# spec — application d'un template UI de référence (cycle de vie)

> Contrainte distillée (épic **ROADMAP-cockpit-ui-capital**, P4 capstone ; tracker vault
> `cockpit-ui-template-capital-home`, 2026-07-23).
> Cibles : `design/{seed,apply}.py`, `roadmap/prompt.py` (`_design_block`),
> `daemon/routes/{templates,projects}.py` (`/inspire`), `cli.py` (`inspire`).

## Problème tranché

La bibliothèque de templates UI de référence (`web/dist/templates/<slug>/`, servie par la vitrine) était
**contemplative** : le dirigeant voyait un beau template mais ne pouvait rien en faire. Aucun chemin pour
dire « inspire-toi de *ce* template pour *mon* projet ». Le capital visuel restait au centre, jamais
appliqué — l'inverse du compounding (un template non appliqué ne se distille jamais en identité de projet).

Symétriquement : un worker dispatché sur l'UI d'un projet n'avait **aucune cible visuelle** ; il inventait
un rendu à l'aveugle, sans identité ancrée.

## Règles verrouillées

Ne pas re-débattre.

1. **Application = déclenchée-opérateur, jamais auto-seed.** C'est le **dirigeant** qui dit « inspire-toi
   de ce template pour mon projet » (route `POST /api/projects/{slug}/inspire` ou `cockpit inspire`). Pas
   d'auto-application au seed du bundle `derive/` (rejeté : trop rigide — l'opérateur choisit son moment
   et sa cible ; utility-user, pas convention).
2. **Le worker CUSTOMISE, jamais ne copie.** La graine est une **cible visuelle**, pas un artefact final :
   identité (tokens), structure, intention — à **adapter** au projet. Une copie verbatim est un échec.
   Le bloc injecté (`_design_block`) porte la consigne « inspire-t'en et customise, ne copie pas ».
3. **`inspire` crée le TRAVAIL de customisation** (feature `design-<slug>` + task `customize-ui`), il ne
   pose pas qu'un fichier. Raison : une graine `.css`/`.png` n'est **pas** docs-only (`is_docs_only`) →
   `code_touched=True` → le merge exige une revue Tier-1 ; une feature **sans task** ne peut pas
   auto-dispatcher son reviewer. En créant la task, le worker de customisation touche le vrai `web/` →
   merge = revue UI **normale** (Tier-1 + Tier-1.5 + GO humain). L'action dissout l'impasse du gate.
4. **La graine atterrit par la voie forge, jamais en commit direct.** `apply_template` réserve un worktree
   de la branche feature `design-<slug>` + `git.commit_worktree` (précédent exact : `interview.py`
   `reconcile_socle`/`_commit_design`). La graine ne devient **project-wide qu'après le merge human-GO**
   standard — cohérent avec le fail-closed (aucun acte outward autonome).
5. **Forme de la graine = `docs/design/<slug>/`** : `brief.md` (l'intention + la consigne customise) +
   `tokens.css` (identité, point de départ) + `preview.png` (le « voici à quoi ça ressemble »). Le worker
   la relit via `_design_block` — **miroir exact** du cliquet décisions (`_decisions_block` ← `docs/decisions/`).
6. **Réinjection worker = budget-bornée, fail-soft, récence.** `_design_block` glob `docs/design/*/brief.md`,
   tri déterministe, borné par `_DESIGN_BUDGET` avec **pointeur d'épuisement** (pas de cap silencieux),
   excerpt borné par `_DESIGN_EXCERPT_MAX`. Aucun `docs/design/` → chaîne vide, filtrée par le `join`
   (aucun câblage requis, N/A-safe). Injecté dans `build_worker_prompt` **et** `build_fix_prompt`.
7. **Idempotence.** Re-`inspire` du même template sur le même projet : feature/task déjà là → réutilise ;
   graine identique → `commit_worktree` no-op sur tree clean. Deux templates distincts → deux
   `docs/design/<slug>/` coexistent (le worker les reçoit toutes, budget-bornées).
8. **MCP différé (maison = vendored/cockpit).** Données terrain : N=1 template, 0 application réelle →
   bâtir un genre/index MCP `ui-kit` maintenant serait un forward-feature. On livre le **mécanisme
   d'application** ; le MCP gradue plus tard, sur réutilisation cross-projet **prouvée** (>1 template,
   usage réel). Voir la décision verrouillée [roadmap](../roadmap.md#décisions-de-conception-verrouillées).

## Cycle de vie (jamais figé)

```
lab authoring ─▶ validation cockpit-ux-critic ─▶ vitrine (web/dist/templates/)
     ▲                                                   │
     │ re-gradue (belle customisation)          opérateur applique (inspire)
     │                                                   ▼
  template du lab ◀── worker CUSTOMISE ◀── graine docs/design/<slug>/ (feature design-<slug>, merge GO)
```

Une customisation réussie **re-gradue** en template du lab (le capital visuel grandit par distillation,
pas par accumulation de brut au centre — doctrine capital-jeton). La boucle est **ouverte** : on la
retendra vers un genre/index MCP le jour où les données (réutilisation cross-projet) le justifient.

## Invariants de test (encodés dans cockpit)

- `docs/design/<slug>/` présent + `brief.md` non vide → le bloc « Cible visuelle du projet » est injecté
  dans le prompt worker **et** fix ; absent → chaîne vide, aucun câblage (N/A-safe, fail-soft).
- Réinjection **budget-bornée** : au-delà de `_DESIGN_BUDGET`, un **pointeur d'épuisement** (jamais de
  cap silencieux) ; ordre **déterministe** (tri par chemin).
- `write_design_seed` copie `tokens.css` + `preview.png` depuis la source servie ; `brief.md` blanc →
  **no-op** (aucune graine vide).
- `apply_template` crée la feature `design-<slug>` **et** la task `customize-ui` (idempotent : ré-exécution
  réutilise) ; commit par la forge sous l'identité worker ; template inconnu → `ValueError` ;
  projet inconnu → `KeyError`.
- Parité CLI/route : `cockpit inspire <project> <template>` et `POST /inspire` délèguent au **même**
  `apply_template` (spine = CLI + cœur déterministe ; daemon = vue).
