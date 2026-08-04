---
name: docs-authoring
description: Rédiger la mémoire durable du projet dans docs/ — pour une session future qui reprend SANS contexte. Audience d'abord, intention avant mécanique, une section = une idée vérifiable, et docs/ tenue interrogeable par docsmap.
inputs: [ce qui vient d'être décidé/construit et mérite de survivre]
outputs: [docs/ à jour, interrogeable via docsmap where, sans duplication du code]
related_catalogs: []
---

# docs-authoring — écrire la mémoire durable du projet

## Quand l'utiliser

Après toute décision structurante, tout contrat d'API posé, toute intention clarifiée — **avant** qu'elle ne
vive que dans ta tête ou dans un diff. `docs/` est la **mémoire long-terme** du projet : ce qu'une session
future (IA ou humaine) lit **d'abord**, sans avoir ton contexte. Ce que tu n'écris pas ici sera re-dérivé à
grands frais, ou perdu.

## Principes

1. **Audience d'abord.** Pour qui cette page, quelle décision sert-elle ? Une session qui reprend le projet
   demain, sans rien savoir. Écris pour elle : intention explicite, rien d'implicite.
2. **Intention avant mécanique.** La section « pourquoi » précède le « comment ». Le *quoi* mécanique, le code
   le dit déjà — ne le recopie pas ; **distille** ce que le code ne peut pas dire (le pourquoi, l'alternative
   écartée, le contrat).
3. **Une section = une idée vérifiable.** Pas de prose molle. Un titre = une affirmation qu'on peut confronter
   au code. Lie les pages entre elles plutôt que de tout répéter.
4. **`docs/` interrogeable, pas récité.** Le CLAUDE.md et l'architecture orientent (`docsmap where`) ; le
   détail vit dans `docs/`. On **interroge** cette mémoire, on ne la duplique pas dans les fichiers de règles.

## Procédure

1. **Choisis la page.** `docs/architecture.md` est le **point d'entrée** — remplis d'abord sa section
   **Intention** (ce que le projet fait, pour qui, critère de succès). Les décisions et contrats vont dans des
   pages dédiées (`docs/<sujet>.md`), pas empilés dans l'architecture.
2. **Écris l'intention, puis la mécanique nécessaire.** Le pourquoi ; ensuite seulement le comment que le code
   ne rend pas évident. Cite le code par `fichier:lignes` plutôt que de le paraphraser (il dérivera).
3. **Relie.** Renvoie aux pages voisines et au symbole de code pertinent (`codemap where`) — la mémoire est un
   graphe, pas une pile.
4. **Rends-la fraîche et interrogeable.** Après avoir touché `docs/` :
   ```bash
   docsmap build && docsmap check     # ré-indexe + signale les sections stale ou supprimées
   ```
   L'**anti-archéologie** en dépend : sans index frais, `docsmap where` renvoie du périmé et la prochaine
   session grep à l'aveugle.

## Anti-patterns

- **Dupliquer le code en prose** — la page dérive au premier refactor. Écris ce que le code **ne dit pas**.
- **Narrer au lieu de distiller** — un journal chronologique au lieu d'un état actuel réutilisable.
- **Doc stale non signalée** — toucher le code sans `docsmap check` : la mémoire ment en silence.
- **Tout entasser dans `architecture.md`** — c'est le sommaire/point d'entrée, pas le dépotoir ; éclate en pages.

## Sortie

Une `docs/` qu'une session **froide** navigue via `docsmap where "<intention>"` : intention en tête,
décisions et contrats en pages liées, zéro duplication du code, index frais (`docsmap check` vert).
