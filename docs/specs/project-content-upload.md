# spec — canal d'upload de contenu/asset dans un projet (cycle de vie)

> Contrainte distillée (tracker vault `cockpit-project-content-upload`, 2026-07-31 ; recoupe l'épic
> **ROADMAP-framework-distribution**).
> Cibles : `content/{upload,ingest}.py`, `daemon/routes/projects.py` (`/upload`), `cli.py` (`upload`),
> `docs/schema-contract.md`. Réutilise : `design/seed.py` (`write_design_seed`), `roadmap/prompt.py`
> (`_design_block`), `dispatch/worktree.py` (`reserve`/`worktree_path_for`), `git/backend.py`
> (`commit_worktree`), `git/identity.py` (`resolve_identity`).

## Problème tranché

L'onboarding first-session **demande l'identité de marque** (charte, schéma de référence), mais le cockpit
n'exposait **aucun canal** pour injecter un fichier dans un projet — ni à l'onboarding, ni en cours de route.
Le seul recours était un `scp` manuel de l'assistant dans le worktree actif de l'interview : ni self-service,
ni scalable, absent de tout cockpit distribué. Un projet orienté contenu (vitrine, docs, tout ce qui a besoin
d'assets de marque / de référence) ne pouvait pas recevoir son matériau par l'UI.

Symétriquement : l'IA d'interview, à qui on demande de comprendre une identité, n'avait **aucun moyen de
lire** l'asset que le dirigeant voulait lui fournir *pendant* la session.

## Règles verrouillées

Ne pas re-débattre.

1. **Canal = déclenché-opérateur.** Le dirigeant ajoute un fichier à un projet (`POST /api/projects/{slug}/upload`
   ou `cockpit upload`). Aucune ingestion automatique, aucun scan de source externe.
2. **Destination v1 = `docs/design/<slug>/`.** Réutilise le circuit design-brief : le fichier atterrit dans le
   dossier **déjà relu par `_design_block`** et lisible par le worker/interview dans son worktree. Pas de
   destination libre `docs/` ni de routage multi-intention en v1 (forward-feature rejeté : l'opérateur choisit
   un sous-dossier `docs/design/<slug>/`, défaut `docs/design/brand/`). Un asset seul (sans `brief.md` frère)
   est **lisible dans le worktree** mais n'est pas auto-injecté dans le prompt (l'injection `_design_block`
   exige un `brief.md` ; la génération d'un brief n'est pas de ce canal — N/A-safe).
3. **Livraison worktree-aware, voie forge, jamais d'acte outward autonome.** Deux cas, une seule discipline :
   - **Worktree actif réservé** (feature `status='active'` du projet, ex. l'interview `socle`) → écrit **dans ce
     worktree** (Read **live** par la session en cours) + `commit_worktree` sur **sa** branche. Devient
     project-wide au merge human-GO **standard** de cette feature.
   - **Aucun worktree actif** (moment B, projet en cours) → `reserve` une feature éphémère `content-<x>` sur
     `dev`, `commit_worktree`, puis merge = **feu vert humain, fail-closed** (jamais autonome). Même discipline
     que `inspire`.
   - **Jamais de commit direct sur `dev`.** Cohérent avec la règle #4 du circuit design-brief
     (`template-ui-application-lifecycle.md`) et l'invariant repo « merge/destroy jamais en autonomie ».
4. **Cœur déterministe + injection explicite (spine).** `content/upload.py:write_project_upload` = **pur
   filesystem** (écrit les bytes, fail-soft, **no-op sur data vide** — retourne `None`), symétrique de
   `write_design_seed`. `content/ingest.py:ingest_upload` compose résolution-worktree + `GitBackend` **injecté**
   (argument, jamais `import server`). **Zéro** I/O réseau/ssh/proxmox (transport local). Daemon et CLI sont des
   **vues** au-dessus du même cœur.
5. **Bornes taille/type — allow-list, jamais de cap silencieux.** Type autorisé par **allow-list** d'extensions
   (images `png/jpg/jpeg/svg/webp` ; texte/doc `md/txt/css/pdf`). Cap taille `_UPLOAD_MAX_BYTES`. Dépassement →
   **lève avec pointeur** (`413` côté HTTP), jamais de troncature. Type hors allow-list → rejet (`415`).
6. **Exclusion des secrets — canal ≠ BWS.** Rejet des noms/patterns de secrets (`.env`, `id_rsa*`, `*.pem`,
   `*.key`, `*.p12`, `*.pfx`, `credentials*`). Les secrets passent par le BWS secret manager, **jamais** par ce
   canal. Le rejet est **explicite** (message clair), pas un filtrage silencieux.
7. **Garde path-traversal.** `dest_rel` + `filename` sont **confinés** sous `docs/design/` du projet : rejet de
   `..`, des chemins absolus, des séparateurs suspects. Le chemin résolu doit rester dans l'arbre du projet.
8. **Parité CLI/route.** `cockpit upload <project> <path> [--dest docs/design/<slug>]` et
   `POST /api/projects/{slug}/upload` (multipart `UploadFile`) délèguent au **même** `ingest_upload` (spine =
   CLI + cœur déterministe ; daemon = vue).
9. **Contrat API figé.** La route `/upload` s'ajoute au contrat : bump `docs/schema-contract.md` + `CHANGELOG.md`
   (schéma inter-couches figé — bump + changelog à tout ajout).

## Cycle de vie

```
dirigeant (UI onboarding | action projet)
        │  POST /api/projects/{slug}/upload   ·   cockpit upload
        ▼
   ingest_upload  ── résout le worktree ──┐
        │                                  │
   worktree actif ?                        │
   ├─ oui → write DANS le worktree (Read LIVE) + commit_worktree(branche feature)
   │         └▶ project-wide au merge GO standard de la feature
   └─ non → reserve feature content-<x> + commit_worktree ─▶ merge [GO HUMAIN, fail-closed]
```

Le worker/interview relit l'asset depuis son worktree (`_design_block` l'injecte s'il porte un `brief.md`
frère ; sinon il reste Read-able à la demande). La discipline forge est **identique** à `inspire` : rien ne
devient project-wide sans un merge human-GO.

## Invariants de test (encodés dans cockpit)

- `write_project_upload` écrit les bytes sous `<worktree>/docs/design/<slug>/<filename>` ; **data vide → no-op**
  (`None`, rien écrit) ; type hors allow-list → rejet ; taille > `_UPLOAD_MAX_BYTES` → **lève avec pointeur**
  (pas de troncature) ; nom de secret (règle #6) → rejet ; `..`/chemin absolu → rejet (règle #7).
- `ingest_upload` : worktree actif présent → écrit **dedans** + `commit_worktree` sur sa branche (le fichier est
  lisible **immédiatement** dans ce worktree) ; **absent** → feature `content-<x>` réservée + `commit_worktree`,
  et le merge **exige un GO** (jamais mergé en autonomie) ; **jamais** de commit direct sur `dev`.
- `commit_worktree` sous l'identité **worker** (`resolve_identity(project, branch, role="worker")`) ; tree clean
  (ré-upload identique) → no-op.
- Parité CLI/route : `cockpit upload <project> <path>` et `POST /api/projects/{slug}/upload` délèguent au **même**
  `ingest_upload` ; projet inconnu → `KeyError` ; source CLI absente → erreur claire.
