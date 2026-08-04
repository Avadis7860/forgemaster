"""backend — l'interface `GitBackend` : le contrat que tout adapter git (internal | github) honore. Les
**signatures sont FIGÉES ici** ; elles gouvernent worktree, branches, merge et writeback pour dispatch/
gate/merge, sans que ceux-ci ne connaissent l'implémentation (correctif #1 : DI, plus de god-module).

Ce fichier ne porte PAS de logique — c'est une frontière. Les adapters (`internal.py`, `github.py`) sont
les stubs à porter. Décisions verrouillées reflétées dans les signatures (cf. docs/specs/) :
- `worktree_add(..., base)` ancre `-B <branch> origin/<base>` (spec sot-local : jamais `checkout -B base`
  dans un 2e worktree) ;
- `remove_worktree` est distinct de `delete_branch` et DOIT être appelable AVANT lui (spec
  worktree-cleanup : cleanup worktree AVANT `git branch -D`) ;
- `merge_writeback(..., creds_ref, identity)` prend une **référence** de secret + une identité à injecter
  le temps de l'op (spec merge-writeback : jamais le secret en clair, jamais persisté).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class GitBackend(Protocol):
    """Contrat git du forgemaster. Toute méthode opère sur un SoT (source of truth) local et lève sur échec
    dur ; les échecs *best-effort* (miroir) se signalent sans bloquer (spec forge-sot-local)."""

    def init_sot(self, sot: Path) -> None:
        """Initialise/normalise le repo SoT (bare local co-localisé). Idempotent."""
        ...

    def add_worktree(self, sot: Path, worktree: Path, *, branch: str, base: str) -> None:
        """Crée un worktree attaché au SoT sur `branch`, ancré `-B <branch> origin/<base>` (jamais
        `checkout -B <base>`). Sérialisé par flock sur le `.git` partagé (spec sot-local)."""
        ...

    def remove_worktree(self, sot: Path, worktree: Path) -> None:
        """Retire le worktree (AVANT toute suppression de branche — spec worktree-cleanup)."""
        ...

    def delete_branch(self, sot: Path, branch: str) -> None:
        """Supprime la branche (à n'appeler qu'APRÈS `remove_worktree` si elle y était sortie)."""
        ...

    def current_branch(self, workdir: Path) -> str:
        """Nom de la branche courante (discipline : refuser de pousser depuis une branche protégée)."""
        ...

    def status(self, workdir: Path) -> dict:
        """Status machine-lisible (branche + ahead/behind + fichiers)."""
        ...

    def is_ancestor(self, sot: Path, ancestor: str, descendant: str) -> bool:
        """`ancestor` est-il ancêtre de `descendant` sur le SoT ? Read-only (garde ff + réalignement)."""
        ...

    def rebase_onto(self, sot: Path, worktree: Path, *, onto: str, identity: tuple[str, str]) -> None:
        """Rebase la branche sortie dans `worktree` sur `onto` (préserve les commits worker ; identité
        injectée). Conflit non trivial → `rebase --abort` puis lève (jamais d'écrasement silencieux).
        Réaligne une base périmée avant un ff quand un sibling a fait avancer `onto` (spec sot-local)."""
        ...

    def merge_ff(self, sot: Path, *, into: str, source: str) -> None:
        """Merge fast-forward `source` dans `into` (feature→dev, puis dev→main). Lève si non-ff."""
        ...

    def merge_writeback(self, sot: Path, *, creds_ref: str | None, identity: tuple[str, str]) -> None:
        """Writeback post-merge (align/cleanup) avec creds+identité INJECTÉS le temps de l'op puis
        retirés. `creds_ref` = référence BWS résolue en amont, jamais le secret (spec merge-writeback)."""
        ...

    def push_mirror(self, sot: Path, remote: str) -> bool:
        """Pousse vers le miroir (GitHub). **Best-effort** : retourne False sur échec, ne lève JAMAIS
        (la vérité est le SoT local ; le miroir ne bloque rien — spec forge-sot-local)."""
        ...

    def remote_divergence(
        self, sot: Path, *, remote: str, branches: Sequence[str], creds_ref: str | None = None
    ) -> dict:
        """Écart SoT↔remote par branche + rollup projet, avec **dégradation honnête**. Fetch best-effort
        (auth transitoire via `creds_ref`, jamais persisté) puis compare chaque branche à sa ref de suivi.
        Renvoie `{remote, fetched, branches:{<b>:{ahead,behind,state}}, state}` (rollup
        `synced|local_ahead|remote_ahead|diverged`). **Jamais 0/0 faux-vert** : remote non configuré →
        `no_mirror`, fetch échoué → `unreachable` (branches vides, `fetched=False`). Read-mostly : lit
        l'état, ne mute pas le SoT (la réconciliation ff est une op séparée — spec forge-sot-local)."""
        ...

    def reconcile(
        self, sot: Path, *, remote: str, branches: Sequence[str], creds_ref: str | None = None
    ) -> dict:
        """Réconcilie le SoT avec son miroir **ff-only** (jamais de merge non-ff ni de `--force`). Recalcule
        la divergence puis agit par branche : `remote_ahead` → ff local vers la ref de suivi ; `local_ahead`
        → push ff cette branche ; `diverged` → **bloqué**, aucune mutation (spec forge-sot-local). Garde-fou :
        ne ff jamais une branche checked-out dans un worktree actif → `blocked_worktree`. Renvoie
        `{remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state}`."""
        ...

    def feature_sha(self, sot: Path, ref: str) -> str:
        """SHA d'une réf (branche) sur le SoT — ancre de fraîcheur des verdicts SHA-bound du gate."""
        ...

    def diff_names(self, sot: Path, *, base: str, head: str) -> list[str]:
        """Fichiers changés par `head` vs `base` (three-dot). Read-only (détection de surface UI)."""
        ...

    def diff_text(self, sot: Path, *, base: str, head: str) -> str:
        """Diff unifié `base...head` (three-dot). Read-only (garde `evidence ⊂ diff` du verdict Tier-1)."""
        ...

    def commit_worktree(self, worktree: Path, *, message: str, identity: tuple[str, str]) -> str | None:
        """Committe le travail de l'ouvrier (le worker ne fait pas de git — la forge committe après son run).
        Identité injectée le temps de l'op. Arbre propre → None (no-op)."""
        ...

    def overlay_commit(self, sot: Path, *, branch: str, files: Mapping[str, str], message: str,
                       identity: tuple[str, str]) -> str | None:
        """Superpose `files` (`chemin → contenu`) sur l'arbre courant de `branch` et committe l'overlay comme
        enfant de sa pointe (préservation par construction : seuls ces chemins sont réécrits, rien n'est
        supprimé). Bare-safe (index temporaire). Identité injectée. **Idempotent** : arbre inchangé → None,
        la ref n'avance pas ; sinon le SHA du commit. **Worktree-safe** : si `branch` est sortie dans un
        worktree vivant, les chemins overlayés y sont resynchronisés vers le nouveau commit (un build depuis
        ce worktree verrait sinon l'ancien contenu) — travail worker hors de ces chemins intact. Sert le
        re-seed du scaffold possédé dans un SoT existant (`provision.reseed`)."""
        ...
