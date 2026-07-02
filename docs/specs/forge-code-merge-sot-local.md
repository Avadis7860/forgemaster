# spec — forge-code-merge (sot:local)

> Contrainte distillée (vault `decisions/stack-choices/2026-06-28--self-hosted-forge.md`,
> `decisions/projects/2026-06-29--forge-local-sot-and-reset.md`). Cible : `git/`, `gate/merge.py`, config.
> Refactor #11.

## Problème tranché

(a) Faut-il adopter une forge externe (Forgejo/Gitea/GitLab) ? (b) Comment remettre un repo projet à un
seed propre et merger en `sot:local` sans boucler, alors que l'ancien reset PR-based bouclait (purge
ledger / close PR / `git clean` manuels, gardes de branche héritées) ?

## Règles verrouillées — frontière forge

- **Le cockpit EST la forge** (native-IA-worker) : PR/merge, review-gate, CI Tier-0, registre, dispatch, CD
  sont déjà possédés. **On ne réintroduit pas** un produit forge externe comme SoT.
- **GitHub = stockage/transport git brut loué + miroir/backup.** Forgejo reste seulement candidat
  **backend-muet swappable** pour une phase OSS-later. Garder les seams swappables (storage / events /
  authZ) pour que l'OSS soit une *phase*, pas une réécriture.

## Règles verrouillées — SoT local & reset

- **SoT = repo local** co-localisé avec le control-plane qui le manipule → **pas de hop réseau dans les
  ops qui bouclent**. Registre : `backend=internal`, `sot_path` local, `mirror_remote`.
- **Reset = respawn vers un seed, PAS un reset PR-based** (une remise à zéro d'état ne passe pas par une
  PR — sinon mélange revue de code / plomberie d'état + gardes de branche → boucles). Respawn = `dev`→seed
  local + `worktree remove` + purge ledger + push miroir, en **une** opération.
- **GitHub miroir = best-effort, jamais bloquant** : `push_mirror` échoue → on surface l'erreur, le respawn
  local reste `ok`. **Ne jamais faire dépendre la vérité (SoT local) d'un backup.**
- **Cœur pur testable hors-live, toute I/O injectée** pour les primitives à blast-radius (force-push,
  purge, recycle) ; étapes live gardées une par une sous **go humain**.
- **Fail-close voulu** : une route reset/merge `sot:local` répond **409 si `backend != internal`** tant que
  le conducteur n'existe pas (garde-fou, pas un bug).

## Invariants de test (à encoder dans cockpit)

- Reset **idempotent** sur ≥2 itérations (cœur pur vert sans toucher au live, I/O injectée).
- `push_mirror` KO → respawn `ok` + erreur miroir surfacée (jamais de blocage sur backup, jamais faux-vert).
- `backend != internal` sur route reset/merge → **409 fail-close**.
- Le respawn garde le marqueur d'époque committé (forward append-only).
