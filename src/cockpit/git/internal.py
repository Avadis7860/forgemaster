"""internal — `InternalGit` : adapter `GitBackend` sur un **repo bare LOCAL** (zéro réseau). Adapter V1
par défaut (décision internal-first).

Port de `services/aggregator/git_ops.py` (builders purs + parsers `parse_status`/`parse_log` + classifieurs
d'erreur). Refactors appliqués :
- **#2** : les builders `cd <workdir> && git …` étaient exécutés par `ssh dev@ip` ; ici on exécute via
  `core.run(["git", "-C", <repo>, …])` LOCAL — argv liste, plus de shell, plus d'IP/clé ssh.
- **#6** : le `pull`/`sync` legacy faisait `reset --hard && clean -fd` (destructif) ; `align_worktree`
  ne fait qu'un `merge --ff-only` (jamais de `reset --hard` implicite).
- **#7/#12** : worktree **attaché au SoT partagé** (`worktree add -B <branch> <path> <base>`, jamais
  `checkout -B <base>`), création **sérialisée par `flock`** sur `<sot>/cockpit-worktree.lock` (le git-dir
  du bare EST le dossier du SoT) — pas un lock in-process.

Le flux d'orchestration du merge (ordre gates → merge → cleanup worktree → writeback) vit dans
`gate/merge.py` ; ici on ne fournit que les **primitives** git.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cockpit.core import run

PROTECTED_BRANCHES = ("main", "dev")
_LOCK_NAME = "cockpit-worktree.lock"

# Garde-fous de lecture d'un blob (explorateur read-only) : au-delà de _MAX_BLOB_READ on ne lit RIEN (juste
# la taille via `cat-file -s`), au-delà de _MAX_BLOB_DISPLAY on tronque le contenu affiché. Bornent la
# mémoire ET le rendu — un dépôt de code ne devrait jamais approcher ces seuils, un binaire lourd est signalé.
_MAX_BLOB_READ = 10 * 1024 * 1024
_MAX_BLOB_DISPLAY = 512 * 1024
_BINARY_SNIFF = 8000  # octets sondés pour un NUL (heuristique git-like « fichier binaire »)
# Cap de la liste plate des chemins (palette « go to file ») : au-delà on tronque et on le SIGNALE
# (`truncated`), jamais de cap silencieux. Un dépôt de code réel reste très en-deçà.
_MAX_TREE_PATHS = 10_000
# Recherche de code (grep) : cap **signalé** du nombre de correspondances (`truncated`) et borne d'affichage
# d'un extrait (lignes minifiées) — même doctrine anti-cap-silencieux que _MAX_TREE_PATHS.
_MAX_GREP_RESULTS = 500
_MAX_MATCH_LEN = 500


class GitOpError(RuntimeError):
    """Échec dur d'une op git (le message porte stderr). Les échecs best-effort ne lèvent pas."""


class BlobTooLargeError(GitOpError):
    """Blob au-delà du plafond de lecture brute (`_MAX_BLOB_READ`) : refus **signalé** (jamais un flux
    tronqué en silence). Sous-classe de `GitOpError` → l'appelant qui distingue (413 vs 404) l'attrape en
    premier ; sinon il retombe dans la gestion générique."""


def _git(repo: str | Path, *args: str, env: Mapping[str, str] | None = None) -> run.RunResult:
    """`git -C <repo> <args>` local, sans shell. Ne lève pas (l'appelant inspecte `.ok`/classifie)."""
    return run.run(["git", "-C", str(repo), *args], env=env)


def _checked(repo: str | Path, *args: str, env: Mapping[str, str] | None = None) -> run.RunResult:
    r = _git(repo, *args, env=env)
    if not r.ok:
        raise GitOpError(f"git {' '.join(args)} @ {repo}: {r.stderr.strip()[:200]}")
    return r


def _is_blame_header(line: str) -> bool:
    """Vrai ssi `line` est l'en-tête d'un groupe blame porcelain : `<sha 40 hex> <orig> <final> [<n>]`
    (détection structurelle, pas de regex — même style « split-de-champs » que le reste du module)."""
    head = line.split(" ", 1)[0]
    return len(head) == 40 and all(c in "0123456789abcdef" for c in head)


# -- parsers PURS (portés verbatim de git_ops.py) ---------------------------------------------------

def _file_entry(index: str, worktree: str, path: str) -> dict:
    return {"path": path, "index": index, "worktree": worktree, "staged": index not in (".", "?")}


def parse_status(stdout: str) -> dict:
    """Parse `git status --porcelain=v2 -b` → {branch, upstream, ahead, behind, files, clean}. PUR."""
    branch = upstream = None
    ahead = behind = 0
    files: list[dict] = []
    for line in (stdout or "").splitlines():
        if not line:
            continue
        if line.startswith("# "):
            parts = line.split()
            key = parts[1] if len(parts) > 1 else ""
            if key == "branch.head" and len(parts) >= 3:
                branch = parts[2]
            elif key == "branch.upstream" and len(parts) >= 3:
                upstream = parts[2]
            elif key == "branch.ab" and len(parts) >= 4:
                try:
                    ahead = int(parts[2].lstrip("+"))
                    behind = int(parts[3].lstrip("-"))
                except ValueError:
                    pass
            continue
        tag = line[0]
        if tag in ("1", "2"):
            maxsplit = 8 if tag == "1" else 9
            f = line.split(None, maxsplit)
            if len(f) <= maxsplit:
                continue
            xy = f[1]
            path = f[maxsplit].split("\t", 1)[0] if tag == "2" else f[maxsplit]
            files.append(_file_entry(xy[0], xy[1], path))
        elif tag == "u":
            f = line.split(None, 10)
            if len(f) <= 10:
                continue
            xy = f[1]
            files.append(_file_entry(xy[0], xy[1], f[10]))
        elif tag == "?":
            path = line[2:]
            if path:
                files.append(_file_entry("?", "?", path))
    return {"branch": branch, "upstream": upstream, "ahead": ahead, "behind": behind,
            "files": files, "clean": not files}


def parse_log(stdout: str) -> list[dict]:
    """Parse `git log --oneline` → [{sha, subject}]. PUR."""
    out: list[dict] = []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition(" ")
        out.append({"sha": sha, "subject": subject})
    return out


def classify_push_error(stdout: str, stderr: str) -> str:
    """Échec de push → 'behind' / 'pat-scope' / 'auth' / 'other'. PUR (porté de git_ops)."""
    blob = f"{stdout}\n{stderr}".lower()
    if "non-fast-forward" in blob or "fetch first" in blob or \
            ("rejected" in blob and "behind" in blob) or "tip of your current branch is behind" in blob:
        return "behind"
    if "write access to repository not granted" in blob or \
            ("permission to" in blob and "denied to" in blob) or \
            "the requested url returned error: 403" in blob or "error: 403" in blob:
        return "pat-scope"
    if "could not read username" in blob or "authentication failed" in blob or \
            "permission denied" in blob or "terminal prompts disabled" in blob or \
            "no credential" in blob or "could not read password" in blob:
        return "auth"
    return "other"


def is_protected_branch(branch: str | None) -> bool:
    """True si `branch` est protégée (push direct interdit : main/dev). PUR."""
    return (branch or "").strip() in PROTECTED_BRANCHES


def _branch_sync_state(ahead: int, behind: int) -> str:
    """État de sync d'UNE branche vs son miroir depuis (ahead, behind). PUR. `ahead` = commits locaux
    absents du remote (SoT en avance → à pousser) ; `behind` = commits remote absents en local (remote en
    avance → à ff). Les deux non nuls = vraie divergence non-ff (spec forge-sot-local : pas d'auto-merge)."""
    if ahead and behind:
        return "diverged"
    if ahead:
        return "local_ahead"
    if behind:
        return "remote_ahead"
    return "synced"


def _rollup_sync_state(branches: Mapping[str, dict]) -> str:
    """Rollup projet depuis les états par-branche. PUR. Une seule branche divergée, OU des branches qui
    tirent en sens opposés (l'une local-avance, l'autre remote-avance) = `diverged` au niveau projet (pas de
    réconciliation ff unique). Sinon on remonte l'état non-`synced` dominant. Jamais 0/0 faux-vert : un input
    vide (aucune branche comparable) reste `synced` seulement si l'appelant a bien fetché (sinon dégradé en
    amont via `no_mirror`/`unreachable`)."""
    states = {b["state"] for b in branches.values()}
    if "diverged" in states or ("local_ahead" in states and "remote_ahead" in states):
        return "diverged"
    if "remote_ahead" in states:
        return "remote_ahead"
    if "local_ahead" in states:
        return "local_ahead"
    return "synced"


# -- injection d'identité writeback (spec merge-writeback : env ponctuel, non persisté) --------------

def writeback_env(identity: tuple[str, str], *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Compose un env avec l'identité git INJECTÉE (auteur + committer), à partir de `base` (défaut
    `os.environ`). **Ne mute pas** `os.environ` — l'env est ponctuel (spec merge-writeback : le temps de
    l'op, jamais persisté). Corrige la cause racine « empty ident name »."""
    name, email = identity
    env = dict(base if base is not None else os.environ)
    env.update({
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
    })
    return env


def credential_env(token: str, *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Compose un env qui **injecte** un token d'accès pour un push GitHub HTTPS, le temps de l'op seulement
    (spec merge-writeback : jamais persisté, jamais en clair en DB/config). Le mécanisme est `GIT_CONFIG_*`
    (`url.<embed>.insteadOf`) : il réécrit `https://github.com/` → `https://x-access-token:<token>@github.com/`
    **uniquement dans l'env du process git enfant** — rien n'est écrit dans un `.gitconfig`. Le token
    n'apparaît PAS dans l'argv (`git push … --all`), seulement dans l'env. `GIT_TERMINAL_PROMPT=0` fait
    échouer bruyamment plutôt que de pendre sur un prompt. Compose au-dessus d'un `GIT_CONFIG_COUNT` déjà
    présent dans `base` (sans écraser ses entrées `GIT_CONFIG_*`). **Ne mute pas** `base`/`os.environ`."""
    env = dict(base if base is not None else os.environ)
    n = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    env[f"GIT_CONFIG_KEY_{n}"] = f"url.https://x-access-token:{token}@github.com/.insteadOf"
    env[f"GIT_CONFIG_VALUE_{n}"] = "https://github.com/"
    env["GIT_CONFIG_COUNT"] = str(n + 1)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


@contextmanager
def _worktree_lock(sot: str | Path) -> Iterator[None]:
    """Sérialise les mutations de worktree par `flock` sur `<sot>/cockpit-worktree.lock` (le git-dir du
    bare = le dossier du SoT). Couvre des process/relais concurrents, contrairement à un lock in-process."""
    lock_path = Path(sot) / _LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


class InternalGit:
    """Adapter GitBackend sur repo bare local. Zéro réseau (le miroir est best-effort et opt-in).

    `cred_resolver` (optionnel) résout un `credential_ref` opaque → token, injecté par l'orchestration
    (`gate/merge`) le temps du writeback. **Total** (le contrat côté appelant : renvoie un token, `''` si
    absent/illisible → jamais d'exception ici), pour que le push miroir reste best-effort. Absent (défaut) →
    push authentifié par l'ambiant (compat historique). Ce paquet n'importe JAMAIS `cockpit.secrets` : la
    couche git reste une primitive, la policy de résolution vit chez l'appelant."""

    def __init__(self, *, cred_resolver: Callable[[str], str] | None = None) -> None:
        self._cred_resolver = cred_resolver

    def init_sot(self, sot: Path, payload: Mapping[str, str] | None = None) -> None:
        """Initialise un repo bare local **amorcé** (idempotent). Un `init --bare` nu n'a AUCUNE branche →
        `add_worktree(..., base='dev')` échouerait ; on **seed** donc `dev` et `main` sur un commit racine
        dès la création, pour que la 1ʳᵉ feature ait une base d'où partir (main-suit-dev).

        `payload` (optionnel, mapping `chemin-relatif → contenu`) sème un arbre non vide dans la racine
        (« toolkit auto-travaillable » injecté par le provisioning) ; absent → arbre vide (compat historique).
        La **policy** (quel payload) vit dans `provision`/`registry` — ici on ne fait que la plomberie."""
        sot = Path(sot)
        sot.mkdir(parents=True, exist_ok=True)
        probe = _git(sot, "rev-parse", "--is-bare-repository")
        if not (probe.ok and probe.stdout.strip() == "true"):
            _checked(sot, "init", "--bare")
        self._seed_base(sot, payload)
        self._point_head_at_default(sot)

    def clone_sot(self, sot: Path, url: str, *, creds_env: Mapping[str, str] | None = None) -> None:
        """Adopte un SoT bare depuis une **URL distante** (`git clone --bare`) — au lieu de semer un arbre
        vide. Auth **transitoire** via `creds_env` (composé par `credential_env` : le token vit dans l'env de
        l'enfant git, jamais en argv ni persisté) ; `creds_env=None` → clone **anonyme** (repo public — auth
        optionnelle, forward-compatible). Puis `_normalize_forge_branches` garantit `dev`+`main`. Read-only
        côté distant (aucune écriture). Lève `GitOpError` si la destination est non-vide ou si le clone échoue
        (l'appelant classe l'échec via `classify_push_error` : auth / pat-scope / 404)."""
        sot = Path(sot)
        if sot.exists() and any(sot.iterdir()):
            raise GitOpError(f"clone: destination non vide {sot}")
        sot.parent.mkdir(parents=True, exist_ok=True)
        env = dict(creds_env) if creds_env is not None else dict(os.environ)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")   # fail-loud, jamais de prompt interactif (privé/no token)
        r = run.run(["git", "clone", "--bare", url, str(sot)], env=env)
        if not r.ok:
            raise GitOpError(f"git clone --bare {url}: {r.stderr.strip()[:200]}")
        self._normalize_forge_branches(sot)
        self._point_head_at_default(sot)

    def _normalize_forge_branches(self, sot: Path) -> None:
        """Garantit l'invariant forge **`dev`+`main`** sur un SoT fraîchement cloné, en **dérivant** des
        branches présentes (jamais un root divergent façon `_seed_base`). Un repo bien formé (dev+main déjà
        là — les 5 outils framework) → **no-op**. Un repo `master`-only / `main`-only → synthèse des branches
        manquantes depuis le SHA de base. Un upstream vide (aucune branche) → seed racine. Idempotent."""
        have = {b["name"] for b in self.branches(sot)}
        base = next((b for b in ("dev", "main", "master") if b in have), None)
        if base is None:
            self._seed_base(sot)
            return
        base_sha = self.feature_sha(sot, base)
        for target in ("dev", "main"):
            if target not in have:
                _checked(sot, "update-ref", f"refs/heads/{target}", base_sha)

    def _seed_base(self, sot: Path, payload: Mapping[str, str] | None = None) -> None:
        """Pose un commit racine sur `dev` et `main` si `dev` n'existe pas encore (early-return =
        idempotence). Plumbing pur (`mktree`/`commit-tree`/`update-ref`) → fonctionne sur un bare sans index
        ni worktree. `payload` non vide → l'arbre racine le porte ; vide/None → arbre vide. Identité injectée
        le temps du commit-tree (jamais persistée), corrige « empty ident name »."""
        if _git(sot, "show-ref", "--verify", "--quiet", "refs/heads/dev").ok:
            return
        env = writeback_env(("cockpit", "cockpit@localhost"))
        tree_sha = self._seed_tree_with_payload(sot, dict(payload or {}), env)
        commit = run.run(
            ["git", "-C", str(sot), "commit-tree", tree_sha, "-m", "root: cockpit seed"], env=env)
        if not commit.ok:
            raise GitOpError(f"seed commit-tree @ {sot}: {commit.stderr.strip()[:200]}")
        sha = commit.stdout.strip()
        for base in ("dev", "main"):
            _checked(sot, "update-ref", f"refs/heads/{base}", sha)

    def _point_head_at_default(self, sot: Path) -> None:
        """Pointe le HEAD du bare sur `refs/heads/main` (la branche promue/stable de la forge). Un
        `git init --bare` laisse HEAD sur `refs/heads/master` — jamais créé ici (branches réelles
        `dev`+`main`) → un `git clone` du SoT sort « remote HEAD refers to nonexistent ref » et ne
        matérialise AUCUN fichier (le client tombe sur un arbre vide). `symbolic-ref` est idempotent et
        n'exige pas que la cible existe déjà → normalise aussi un bare hérité de l'ancien init."""
        _checked(sot, "symbolic-ref", "HEAD", "refs/heads/main")

    def _seed_tree_with_payload(
        self, sot: Path, files: Mapping[str, str], env: Mapping[str, str],
    ) -> str:
        """Construit un arbre git à partir d'un mapping `chemin-relatif → contenu` et renvoie le SHA de
        l'arbre racine, par plumbing pur : `hash-object -w --stdin` par blob, `mktree` imbriqué par
        sous-dossier. `files` vide → arbre vide (le `mktree` sur stdin vide donne l'empty-tree). **Primitive**
        bare-safe (ni index ni worktree) : aucune policy ici — le QUOI vendoré vit dans `provision/`."""
        nested: dict[str, object] = {}
        for rel, content in files.items():
            parts = [p for p in rel.split("/") if p]
            if not parts:
                continue
            node = nested
            for part in parts[:-1]:
                child = node.setdefault(part, {})
                if not isinstance(child, dict):
                    raise GitOpError(f"seed payload: {part!r} est à la fois fichier et dossier")
                node = child
            node[parts[-1]] = content
        return self._mktree(sot, nested, env)

    def _mktree(self, sot: Path, node: Mapping[str, object], env: Mapping[str, str]) -> str:
        """Écrit un niveau d'arbre et renvoie son SHA : `100644 blob` pour les feuilles (contenu str),
        `040000 tree` récursif pour les sous-dossiers (dict). Entrées **triées** → déterministe."""
        lines: list[str] = []
        for name in sorted(node):
            value = node[name]
            if isinstance(value, dict):
                sub = self._mktree(sot, value, env)
                lines.append(f"040000 tree {sub}\t{name}")
            else:
                blob = run.run(["git", "-C", str(sot), "hash-object", "-w", "--stdin"],
                               input_text=str(value), env=env)
                if not blob.ok:
                    raise GitOpError(f"seed hash-object {name!r} @ {sot}: {blob.stderr.strip()[:200]}")
                lines.append(f"100644 blob {blob.stdout.strip()}\t{name}")
        tree = run.run(["git", "-C", str(sot), "mktree"],
                       input_text=("\n".join(lines) + "\n") if lines else "", env=env)
        if not tree.ok:
            raise GitOpError(f"seed mktree @ {sot}: {tree.stderr.strip()[:200]}")
        return tree.stdout.strip()

    def overlay_commit(self, sot: Path, *, branch: str, files: Mapping[str, str], message: str,
                       identity: tuple[str, str]) -> str | None:
        """Superpose `files` (`chemin-relatif → contenu`) sur l'arbre courant de `branch` et committe
        l'overlay comme ENFANT de la pointe de `branch`, avançant la ref. **Préservation par construction** :
        on part de l'arbre de `branch` (`read-tree`), on n'ajoute/remplace QUE les chemins de `files`, on ne
        supprime rien → tout le reste de l'arbre est conservé intact. **Bare-safe** : passe par un index
        temporaire (`GIT_INDEX_FILE`), ni worktree ni index par défaut du dépôt touché — plumbing pur comme
        `_seed_base`. **Idempotent** : si l'overlay ne change pas l'arbre (contenus déjà identiques), aucun
        commit n'est créé, la ref n'avance pas → retour `None` ; sinon le SHA du commit d'overlay. Identité
        INJECTÉE (non persistée). Sérialisé par le flock du SoT. Lève `GitOpError` si `files` est vide ou si
        `branch` est absente. **Worktree-safe** : si `branch` est sortie dans un worktree vivant, les chemins
        overlayés y sont resynchronisés vers le nouveau commit (sinon un build depuis ce worktree servirait
        l'ancien contenu ; cf. sync ci-dessous) — le travail worker hors de ces chemins reste intact."""
        if not files:
            raise GitOpError("overlay_commit: aucun fichier à superposer")
        env = writeback_env(identity)
        with _worktree_lock(sot):
            head = _git(sot, "rev-parse", "--verify", f"refs/heads/{branch}", env=env)
            if not head.ok:
                raise GitOpError(f"overlay_commit: branche {branch!r} absente @ {sot}")
            parent = head.stdout.strip()
            with tempfile.TemporaryDirectory() as tmp:
                idx_env = {**env, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
                _checked(sot, "read-tree", parent, env=idx_env)     # index = arbre courant de `branch`
                for rel, content in sorted(files.items()):
                    blob = run.run(["git", "-C", str(sot), "hash-object", "-w", "--stdin"],
                                   input_text=content, env=env)
                    if not blob.ok:
                        raise GitOpError(f"overlay hash-object {rel!r} @ {sot}: {blob.stderr.strip()[:200]}")
                    _checked(sot, "update-index", "--add", "--cacheinfo",
                             f"100644,{blob.stdout.strip()},{rel}", env=idx_env)
                tree = _checked(sot, "write-tree", env=idx_env).stdout.strip()
            parent_tree = _checked(sot, "rev-parse", f"{parent}^{{tree}}", env=env).stdout.strip()
            if tree == parent_tree:
                return None                                          # idempotent : rien n'a changé
            commit = run.run(
                ["git", "-C", str(sot), "commit-tree", tree, "-p", parent, "-m", message], env=env)
            if not commit.ok:
                raise GitOpError(f"overlay commit-tree @ {sot}: {commit.stderr.strip()[:200]}")
            sha = commit.stdout.strip()
            _checked(sot, "update-ref", f"refs/heads/{branch}", sha, parent)   # CAS sur parent (anti-course)
            # Si `branch` est SORTIE dans un worktree vivant (feature en vol), `update-ref` vient d'avancer
            # son HEAD sous ses pieds, mais son index + arbre de travail restent PÉRIMÉS : un build depuis ce
            # worktree (`deploy_preview`) servirait l'ANCIEN contenu, et les chemins overlayés y seraient des
            # « modifs worker » fantômes (l'inverse de la préservation promise — pire, un commit worker les
            # ré-annulerait). On resynchronise donc les SEULS chemins overlayés du worktree vers le nouveau
            # commit ; le reste (travail worker) reste intact (aucun worktree sur `branch`, cas `dev` : skip).
            wt = self._worktree_for_branch(sot, branch)
            if wt is not None:
                _checked(wt, "checkout", sha, "--", *sorted(files))
            return sha

    def add_worktree(self, sot: Path, worktree: Path, *, branch: str, base: str) -> None:
        """Crée un worktree attaché au SoT sur `branch`, ancré sur `base` (`add -B <branch> <path> <base>`,
        jamais `checkout -B <base>`). Sérialisé par flock (spec sot-local, #7/#12)."""
        with _worktree_lock(sot):
            _checked(sot, "worktree", "add", "-B", branch, str(worktree), base)

    def remove_worktree(self, sot: Path, worktree: Path) -> None:
        """Retire le worktree (à appeler AVANT `delete_branch` — spec worktree-cleanup). Sérialisé."""
        with _worktree_lock(sot):
            _checked(sot, "worktree", "remove", "--force", str(worktree))

    def delete_branch(self, sot: Path, branch: str) -> None:
        """Supprime la branche (après `remove_worktree` si elle y était sortie)."""
        _checked(sot, "branch", "-D", branch)

    def current_branch(self, workdir: Path) -> str:
        """Nom de la branche courante du worktree (`rev-parse --abbrev-ref HEAD`)."""
        return _checked(workdir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def status(self, workdir: Path) -> dict:
        """Status machine-lisible (`--porcelain=v2 -b`, parsé)."""
        return parse_status(_checked(workdir, "status", "--porcelain=v2", "-b").stdout)

    def is_ancestor(self, sot: Path, ancestor: str, descendant: str) -> bool:
        """`ancestor` est-il un ancêtre de `descendant` sur le SoT (`merge-base --is-ancestor`) ? Read-only.
        Sert la garde ff (`merge_ff`) ET le réalignement anti-stale-base (`run_merge` rebase quand faux)."""
        return _git(sot, "merge-base", "--is-ancestor", ancestor, descendant).ok

    def rebase_onto(self, sot: Path, worktree: Path, *, onto: str, identity: tuple[str, str]) -> None:
        """Rebase la branche sortie dans `worktree` sur `onto` (linéarise, **préserve** les commits worker)
        — réaligne une base périmée quand un sibling a fait avancer `onto` (dev) depuis le drain de cette
        feature. Identité INJECTÉE (les commits rejoués reçoivent un committer valide, non persisté — spec
        merge-writeback ; corrige « empty ident »). Sur conflit non trivial : `rebase --abort` (jamais
        d'état demi-rebasé ni d'écrasement silencieux) puis `GitOpError` — une vraie divergence doit être
        re-drainée/re-revue. Sérialisé par le flock du SoT (cohérence avec add/remove_worktree)."""
        env = writeback_env(identity)
        with _worktree_lock(sot):
            if not _git(worktree, "rebase", onto, env=env).ok:
                _git(worktree, "rebase", "--abort")
                raise GitOpError(f"rebase sur {onto} : conflit non trivial (base périmée non réalignable "
                                 f"automatiquement) — re-drainer la feature")

    def merge_ff(self, sot: Path, *, into: str, source: str) -> None:
        """Merge **fast-forward** `source` dans `into` sur le SoT bare : `into` doit être ancêtre de
        `source` (sinon `GitOpError` non-ff). Sur un bare, un ff = avancer la ref (`branch -f`)."""
        if not self.is_ancestor(sot, into, source):
            raise GitOpError(f"merge non-ff : {into} n'est pas ancêtre de {source}")
        _checked(sot, "branch", "-f", into, source)

    def merge_writeback(self, sot: Path, *, creds_ref: str | None, identity: tuple[str, str]) -> None:
        """Writeback post-merge avec identité injectée (non persistée). En sot:local le SoT bare EST la
        vérité (les refs sont déjà à jour après `merge_ff`) : le writeback se réduit à l'identité + un push
        miroir **best-effort** si un remote est configuré. Si `creds_ref` est fourni ET qu'un résolveur est
        injecté, le token est résolu à l'usage et injecté dans l'env (`credential_env`) le temps du push,
        jamais persisté ni loggé (spec merge-writeback). Ne lève jamais sur échec miroir (spec
        forge-sot-local)."""
        env = writeback_env(identity)
        if creds_ref and self._cred_resolver is not None:
            token = self._cred_resolver(creds_ref)          # total : '' si absent/illisible → push ambiant
            if token:
                env = credential_env(token, base=env)
        for remote in ("origin", "mirror"):
            if _git(sot, "remote", "get-url", remote).ok:
                _git(sot, "push", remote, "--all", env=env)  # best-effort, non bloquant
                break

    def push_mirror(self, sot: Path, remote: str) -> bool:
        """Pousse toutes les branches vers `remote`. **Best-effort** : retourne False sur échec, ne lève
        jamais (la vérité est le SoT local — spec forge-sot-local)."""
        return _git(sot, "push", remote, "--all").ok

    # -- config de remote + fetch authentifié (sync miroir : projets ⟶ `mirror`, outils ⟶ `origin`) --

    def set_remote(self, sot: Path, name: str, url: str) -> None:
        """Configure (idempotent) le remote `name` → `url` sur le SoT bare : `set-url` s'il existe déjà,
        sinon `add`. Câblé par `registry.create_project`/`set_mirror_remote` pour **matérialiser dans git**
        le miroir qui n'était jusqu'ici qu'une string en DB (`mirror_remote`). Zéro réseau (config locale)."""
        if _git(sot, "remote", "get-url", name).ok:
            _checked(sot, "remote", "set-url", name, url)
        else:
            _checked(sot, "remote", "add", name, url)

    def remove_remote(self, sot: Path, name: str) -> None:
        """Retire le remote `name` s'il existe (best-effort : absent → no-op, ne lève pas). Symétrique de
        `set_remote` pour le retrait d'un miroir (`set_mirror_remote(None)`)."""
        _git(sot, "remote", "remove", name)

    def _authed_env(self, creds_ref: str | None) -> dict[str, str]:
        """Env d'un enfant git avec auth **transitoire** pour un aller-retour réseau (fetch/push). Si
        `creds_ref` est fourni ET qu'un résolveur est injecté, le token est résolu à l'usage et injecté via
        `credential_env` (jamais persisté, jamais en argv). `GIT_TERMINAL_PROMPT=0` fait échouer bruyamment
        plutôt que pendre sur un prompt (privé sans token). Sinon → env ambiant (compat historique)."""
        env: dict[str, str] = dict(os.environ)
        if creds_ref and self._cred_resolver is not None:
            token = self._cred_resolver(creds_ref)      # total : '' si absent/illisible → op ambiante
            if token:
                env = credential_env(token, base=env)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        return env

    def fetch_remote(self, sot: Path, remote: str, *, creds_ref: str | None = None) -> bool:
        """Fetch `remote` sur le SoT bare (met à jour `refs/remotes/<remote>/*`). **Best-effort** : retourne
        False sur échec (remote absent / injoignable / auth), ne lève **jamais** — la vérité reste le SoT
        local (spec forge-sot-local), et la couche divergence (P2) en dérivera un état honnête `unreachable`.
        Auth **transitoire** via `_authed_env` (token résolu à l'usage, jamais persisté ni en argv)."""
        return _git(sot, "fetch", remote, env=self._authed_env(creds_ref)).ok

    def ensure_fetch_refspec(self, sot: Path, remote: str) -> None:
        """Garantit (idempotent) le refspec de fetch standard `+refs/heads/*:refs/remotes/<remote>/*` sur
        `remote`. **Pourquoi** : un `git clone --bare` (voie d'adoption d'un outil, `clone_sot`) NE POSE
        AUCUN refspec de fetch — contrairement à `git remote add` (voie miroir). Sans lui, `git fetch
        <remote>` échoue à peupler `refs/remotes/<remote>/*`, ce qui fausserait `remote_divergence` (branches
        de suivi absentes → faux `local_ahead`). `sync_tracking` l'appelle avant de fetcher un outil ⟶
        **auto-répare** aussi les clones déjà adoptés (les 4 outils du rail, gelés sans refspec). Remote
        absent → no-op (best-effort). `--replace-all` → exactement un refspec, ré-appliquable sans dommage."""
        if not _git(sot, "remote", "get-url", remote).ok:
            return
        _git(sot, "config", "--replace-all",
             f"remote.{remote}.fetch", f"+refs/heads/*:refs/remotes/{remote}/*")

    # -- lectures pour le gate (SHA d'ancrage + diff base...head, read-only) -------------------------

    def feature_sha(self, sot: Path, ref: str) -> str:
        """SHA d'une réf (branche) sur le SoT bare (`rev-parse <ref>`). Ancre de fraîcheur du verdict
        Tier-1/Tier-1.5 (le gate le compare au `reviewed_sha`). Lève si la réf est introuvable."""
        return _checked(sot, "rev-parse", ref).stdout.strip()

    def diff_names(self, sot: Path, *, base: str, head: str) -> list[str]:
        """Fichiers changés par `head` vs `base` (`diff --name-only base...head`, three-dot = merge-base).
        Sert `feature_verify.has_ui` (détection de surface UI). Read-only."""
        out = _checked(sot, "diff", "--name-only", f"{base}...{head}").stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def diff_text(self, sot: Path, *, base: str, head: str) -> str:
        """Diff unifié complet `base...head` (three-dot). Corpus de la garde `evidence ⊂ diff` du verdict
        Tier-1 (une citation de finding doit apparaître dans une ligne ajoutée). Read-only."""
        return _checked(sot, "diff", f"{base}...{head}").stdout

    # -- lectures read-only pour la vue git (bare-safe : ni index ni working-tree requis) ------------

    def branches(self, sot: Path) -> list[dict]:
        """Branches locales du SoT bare (`for-each-ref refs/heads`) → `[{name, sha, subject}]`, triées par
        nom. Read-only, bare-safe (aucun working-tree). `%09` = tab dans le format for-each-ref."""
        fmt = "%(refname:short)%09%(objectname:short)%09%(contents:subject)"
        out = _checked(sot, "for-each-ref", "--sort=refname", f"--format={fmt}", "refs/heads").stdout
        rows: list[dict] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            name, _, rest = line.partition("\t")
            sha, _, subject = rest.partition("\t")
            rows.append({"name": name, "sha": sha, "subject": subject})
        return rows

    def tags(self, sot: Path) -> list[dict]:
        """Tags du SoT bare (`for-each-ref refs/tags`) → `[{name, sha, subject}]`, triés par date de création
        décroissante (tags récents d'abord). Même forme que `branches` (le sélecteur de réf les unifie).
        `subject` = message du tag annoté (vide pour un tag léger). Read-only, bare-safe."""
        fmt = "%(refname:short)%09%(objectname:short)%09%(contents:subject)"
        out = _checked(sot, "for-each-ref", "--sort=-creatordate", f"--format={fmt}", "refs/tags").stdout
        rows: list[dict] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            name, _, rest = line.partition("\t")
            sha, _, subject = rest.partition("\t")
            rows.append({"name": name, "sha": sha, "subject": subject})
        return rows

    def log(self, sot: Path, ref: str, *, n: int = 20) -> list[dict]:
        """Log court d'une réf sur le SoT bare (`log --oneline -n <n> <ref>`, parsé PUR). Read-only,
        bare-safe. `--no-decorate` → sujet propre (pas de `(HEAD -> …)`). Lève si la réf est introuvable."""
        out = _checked(sot, "log", f"--max-count={n}", "--oneline", "--no-decorate", ref).stdout
        return parse_log(out)

    def ahead_behind(self, sot: Path, *, base: str, head: str) -> dict:
        """Avance/retard de `head` vs `base` (`rev-list --left-right --count base...head` → `L\\tR`) :
        `behind` = commits de `base` absents de `head` (L, gauche), `ahead` = commits de `head` absents de
        `base` (R, droite). En sot:local (main-suit-dev), `base=main head=dev` → `ahead` = ce que main doit
        rattraper. Read-only, bare-safe. Lève si une réf est introuvable."""
        out = _checked(sot, "rev-list", "--left-right", "--count", f"{base}...{head}").stdout
        left, _, right = out.strip().partition("\t")
        return {"base": base, "head": head, "behind": int(left or 0), "ahead": int(right or 0)}

    def remote_divergence(
        self,
        sot: Path,
        *,
        remote: str = "mirror",
        branches: Sequence[str] = ("dev", "main"),
        creds_ref: str | None = None,
    ) -> dict:
        """Écart SoT↔remote **par branche** + rollup projet, avec **dégradation honnête**. Fetch best-effort
        d'abord (`fetch_remote`, auth transitoire via `creds_ref` — jamais persisté), puis compare chaque
        branche locale à sa ref de suivi. Renvoie `{remote, fetched, branches, state}` où `branches` mappe
        `<b> → {ahead, behind, state}` et `state` est le rollup.

        Convention (identique à `ahead_behind`) : `<remote-ref>...<local-ref>` → gauche = commits remote
        absents en local = `behind` ; droite = commits locaux absents du remote = `ahead`. États :
        `synced | local_ahead | remote_ahead | diverged`.

        **Jamais de 0/0 faux-vert** : remote non configuré → `no_mirror` (rien à comparer) ; fetch échoué
        (injoignable/auth) → `unreachable` — dans les deux cas `branches={}` et `fetched=False`, l'appelant
        signale l'état dégradé au lieu d'afficher « à jour ». Une branche absente d'un côté est un écart réel
        (remote sans la branche → `local_ahead` ; local sans la branche → `remote_ahead`), pas `synced`."""
        if not _git(sot, "remote", "get-url", remote).ok:
            return {"remote": remote, "fetched": False, "branches": {}, "state": "no_mirror"}
        if not self.fetch_remote(sot, remote, creds_ref=creds_ref):
            return {"remote": remote, "fetched": False, "branches": {}, "state": "unreachable"}

        per: dict[str, dict] = {}
        for b in branches:
            local = f"refs/heads/{b}"
            tracked = f"refs/remotes/{remote}/{b}"
            has_local = _git(sot, "rev-parse", "--verify", "--quiet", local).ok
            has_tracked = _git(sot, "rev-parse", "--verify", "--quiet", tracked).ok
            if not has_local and not has_tracked:
                continue  # branche demandée inexistante des deux côtés → hors rapport (pas un faux-vert)
            if has_local and has_tracked:
                out = _checked(sot, "rev-list", "--left-right", "--count", f"{tracked}...{local}").stdout
                left, _, right = out.strip().partition("\t")
                behind, ahead = int(left or 0), int(right or 0)
            elif has_local:  # le remote n'a pas encore la branche → tout le local est en avance
                ahead = int(_checked(sot, "rev-list", "--count", local).stdout.strip() or 0)
                behind = 0
            else:  # seule la ref de suivi existe → le local est en retard de toute la branche remote
                behind = int(_checked(sot, "rev-list", "--count", tracked).stdout.strip() or 0)
                ahead = 0
            per[b] = {"ahead": ahead, "behind": behind, "state": _branch_sync_state(ahead, behind)}

        return {"remote": remote, "fetched": True, "branches": per, "state": _rollup_sync_state(per)}

    def _checked_out_branches(self, sot: Path) -> set[str]:
        """Branches **sorties dans un worktree actif** (`worktree list --porcelain` → lignes `branch
        refs/heads/<b>`). `branch -f <b>` refuserait une branche checked-out → on ne la ff JAMAIS (garde-fou
        `reconcile`). Un bare sans worktree lié → set vide. Best-effort (echec → set vide, prudent)."""
        out = _git(sot, "worktree", "list", "--porcelain").stdout
        return {
            line[len("branch "):].strip().removeprefix("refs/heads/")
            for line in out.splitlines() if line.startswith("branch ")
        }

    def _worktree_for_branch(self, sot: Path, branch: str) -> Path | None:
        """Chemin du worktree actif où `branch` est SORTIE (`worktree list --porcelain`), ou `None` si aucune
        (cas `dev`/bare pur). Une branche ne peut être sortie que dans UN worktree (git l'impose) → au plus un
        résultat. Best-effort (échec de la commande → aucune ligne → `None`, prudent)."""
        out = _git(sot, "worktree", "list", "--porcelain").stdout
        current: str | None = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                current = line[len("worktree "):].strip()
            elif line.startswith("branch ") and \
                    line[len("branch "):].strip().removeprefix("refs/heads/") == branch:
                return Path(current) if current else None
        return None

    def reconcile(
        self,
        sot: Path,
        *,
        remote: str = "mirror",
        branches: Sequence[str] = ("dev", "main"),
        creds_ref: str | None = None,
    ) -> dict:
        """Réconcilie le SoT avec son miroir **ff-only**, sans jamais de merge non-ff ni de `--force`.
        **Recalcule d'abord la divergence** (fetch + compare — l'état frais fait autorité, pas un snapshot
        d'UI périmé), puis agit par branche selon son état :
        - `remote_ahead` → **ff** la branche locale vers sa ref de suivi (`merge_ff` = avance de ref sur un
          bare) ;
        - `local_ahead` → **push ff** cette seule branche vers le miroir (refspec explicite, best-effort,
          jamais `--force`) ;
        - `synced` → rien ;
        - `diverged` (non-ff) → **BLOQUÉ**, aucune mutation, raison honnête (spec forge-sot-local : jamais
          d'auto-merge).

        **Garde-fou** : on ne ff **jamais** une branche sortie dans un worktree actif (`branch -f` la
        refuserait) → `blocked_worktree`, aucune mutation. **Dégradation honnête** héritée : `no_mirror` /
        `unreachable` (fetch KO) → aucune action, `fetched=False`.

        Renvoie `{remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state}` :
        `action` ∈ `already_synced | fast_forward | pushed | push_failed | blocked_worktree |
        blocked_diverged` ; `changed` = au moins une ref a bougé ; `blocked` = branches non réconciliables ;
        `state` = rollup **pré-action** (l'UI re-fetch `/git/sync` pour le badge frais). Idempotent en régime
        `synced` (rien à faire) ; un ff est ré-appliquable sans dommage."""
        div = self.remote_divergence(sot, remote=remote, branches=branches, creds_ref=creds_ref)
        if not div["fetched"]:   # no_mirror / unreachable → rien à réconcilier, honnête
            return {"remote": remote, "fetched": False, "actions": {}, "changed": False,
                    "blocked": [], "state": div["state"]}
        checked_out = self._checked_out_branches(sot)
        authed = self._authed_env(creds_ref)
        actions: dict[str, dict] = {}
        blocked: list[str] = []
        changed = False
        for b, info in div["branches"].items():
            state = info["state"]
            if state == "synced":
                actions[b] = {"action": "already_synced"}
            elif state == "remote_ahead":
                if b in checked_out:
                    actions[b] = {"action": "blocked_worktree",
                                  "reason": f"{b} est sortie dans un worktree actif — ff refusé"}
                    blocked.append(b)
                    continue
                old = self.feature_sha(sot, b)
                self.merge_ff(sot, into=b, source=f"refs/remotes/{remote}/{b}")  # ff garanti (into ancêtre)
                actions[b] = {"action": "fast_forward", "from": old, "to": self.feature_sha(sot, b)}
                changed = True
            elif state == "local_ahead":
                pushed = _git(sot, "push", remote, f"refs/heads/{b}:refs/heads/{b}", env=authed).ok
                actions[b] = {"action": "pushed" if pushed else "push_failed"}
                changed = changed or pushed
                if not pushed:
                    blocked.append(b)
            else:  # diverged → jamais d'auto-merge non-ff
                actions[b] = {"action": "blocked_diverged",
                              "reason": f"{b} a divergé (non-ff) — réconciliation manuelle requise"}
                blocked.append(b)
        return {"remote": remote, "fetched": True, "actions": actions, "changed": changed,
                "blocked": blocked, "state": div["state"]}

    def sync_tracking(
        self,
        sot: Path,
        *,
        remote: str = "origin",
        branches: Sequence[str] = ("dev", "main"),
        creds_ref: str | None = None,
    ) -> dict:
        """Re-synchronise un SoT **read-only** (outil adopté, `kind=tool`) avec son amont : **pull-only, ff
        seulement**. L'amont FAIT autorité (à l'inverse de `reconcile` où le SoT local est autoritatif) → la
        mutation est **uniquement descendante**. Auto-répare d'abord le refspec de fetch
        (`ensure_fetch_refspec` — un outil cloné bare n'en a pas), recalcule la divergence (fetch + compare),
        puis par branche :
        - `remote_ahead` → **ff** la branche locale vers sa ref de suivi (`merge_ff` = avance de ref bare) ;
        - `local_ahead` → **JAMAIS de push** (`local_ahead_skipped`) : un outil read-only n'a aucune autorité
          d'écriture sur son amont — on laisse les commits locaux (anomalie signalée, pas propagée) ;
        - `synced` → rien (`already_synced`) ;
        - `diverged` (non-ff) → `blocked_diverged`, aucune mutation.

        **Garde-fou** hérité : jamais de ff sur une branche sortie dans un worktree actif → `blocked_worktree`
        (aucune mutation).
        **Dégradation honnête** héritée de `remote_divergence` : `no_mirror`/`unreachable` → `fetched=False`,
        aucune action. Renvoie `{remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed,
        blocked, state}` ; `action` ∈ `already_synced | fast_forward | local_ahead_skipped | blocked_worktree`
        `| blocked_diverged`. `changed` = au moins une ref a avancé ; `state` = rollup pré-action. Idempotent
        en régime `synced` ; un ff est ré-appliquable sans dommage."""
        self.ensure_fetch_refspec(sot, remote)   # auto-répare le clone bare (origin sans refspec de fetch)
        div = self.remote_divergence(sot, remote=remote, branches=branches, creds_ref=creds_ref)
        if not div["fetched"]:   # no_mirror / unreachable → rien à synchroniser, honnête
            return {"remote": remote, "fetched": False, "actions": {}, "changed": False,
                    "blocked": [], "state": div["state"]}
        checked_out = self._checked_out_branches(sot)
        actions: dict[str, dict] = {}
        blocked: list[str] = []
        changed = False
        for b, info in div["branches"].items():
            state = info["state"]
            if state == "synced":
                actions[b] = {"action": "already_synced"}
            elif state == "remote_ahead":
                if b in checked_out:
                    actions[b] = {"action": "blocked_worktree",
                                  "reason": f"{b} est sortie dans un worktree actif — ff refusé"}
                    blocked.append(b)
                    continue
                old = self.feature_sha(sot, b)
                self.merge_ff(sot, into=b, source=f"refs/remotes/{remote}/{b}")  # ff garanti (into ancêtre)
                actions[b] = {"action": "fast_forward", "from": old, "to": self.feature_sha(sot, b)}
                changed = True
            elif state == "local_ahead":   # outil read-only : on NE POUSSE JAMAIS l'amont (frontière stricte)
                actions[b] = {"action": "local_ahead_skipped",
                              "reason": f"{b} a des commits locaux — outil read-only, aucun push amont"}
            else:  # diverged (non-ff) → jamais d'auto-merge
                actions[b] = {"action": "blocked_diverged",
                              "reason": f"{b} a divergé (non-ff) — outil read-only, résolution manuelle"}
                blocked.append(b)
        return {"remote": remote, "fetched": True, "actions": actions, "changed": changed,
                "blocked": blocked, "state": div["state"]}

    # -- exploration read-only du dépôt (arbre + contenu, bare-safe) ---------------------------------

    def ls_tree(self, sot: Path, ref: str, path: str = "") -> list[dict]:
        """Entrées d'un dossier du dépôt à une réf, **sans working-tree** : `git ls-tree --long <ref>:<path>`
        (le treeish `<ref>:<path>` EST l'objet arbre → liste son contenu non-récursif). Renvoie
        `[{name, type:blob|tree|commit, size, sha}]`, **dossiers d'abord** puis alphabétique. `path` vide =
        racine. `size` = `None` pour un arbre. Read-only, bare-safe. Lève (`GitOpError`) si la réf est
        introuvable ou si `path` n'est pas un dossier (ex. un blob) → l'appelant mappe en 404."""
        treeish = f"{ref}:{path.strip('/')}"
        out = _checked(sot, "ls-tree", "--long", "--abbrev=10", treeish).stdout
        rows: list[dict] = []
        for line in out.splitlines():
            meta, _, name = line.partition("\t")
            parts = meta.split()  # [mode, type, sha, size] — `--long` colle la taille (ou '-' pour un arbre)
            if len(parts) < 4 or not name:
                continue
            _mode, otype, osha, osize = parts[0], parts[1], parts[2], parts[3]
            rows.append({"name": name, "type": otype, "sha": osha,
                         "size": None if osize == "-" else int(osize)})
        rows.sort(key=lambda e: (e["type"] != "tree", e["name"]))
        return rows

    def list_paths(self, sot: Path, ref: str) -> dict:
        """Liste **plate et récursive** de tous les fichiers (blobs) d'une réf : `git ls-tree -r --name-only
        <ref>` → `{paths: [...], truncated}`, pour la palette « go to file » (filtrage fuzzy client-side).
        **Cap signalé** : au-delà de `_MAX_TREE_PATHS` on tronque ET on DIT `truncated=True` (jamais de cap
        silencieux, invariant). Read-only, bare-safe. Lève (`GitOpError`) si la réf est introuvable."""
        out = _checked(sot, "ls-tree", "-r", "--name-only", ref).stdout
        paths = [line for line in out.splitlines() if line.strip()]
        return {"paths": paths[:_MAX_TREE_PATHS], "truncated": len(paths) > _MAX_TREE_PATHS}

    def read_blob(self, sot: Path, ref: str, path: str) -> dict:
        """Contenu d'un fichier du dépôt à une réf, **sans working-tree** : type + taille via `cat-file`,
        contenu via `cat-file -p <ref>:<path>` (bytes bornés). Renvoie
        `{path, ref, size, binary, truncated, too_large, content}`. Garde-fous (L4) : au-delà de
        `_MAX_BLOB_READ` → `too_large` (aucune lecture) ; un NUL dans les premiers octets → `binary`
        (contenu vide, on n'émet jamais d'octets bruts) ; au-delà de `_MAX_BLOB_DISPLAY` → `truncated`.
        Read-only, bare-safe. Lève (`GitOpError`) si la réf/chemin est introuvable ou n'est pas un blob."""
        treeish = f"{ref}:{path.strip('/')}"
        kind = _checked(sot, "cat-file", "-t", treeish).stdout.strip()
        if kind != "blob":
            raise GitOpError(f"{path!r} @ {ref} n'est pas un fichier (type git : {kind or 'inconnu'})")
        size = int(_checked(sot, "cat-file", "-s", treeish).stdout.strip() or 0)
        base = {"path": path, "ref": ref, "size": size}
        if size > _MAX_BLOB_READ:
            return {**base, "binary": False, "truncated": True, "too_large": True, "content": ""}
        raw = self._blob_bytes(sot, treeish)
        if b"\x00" in raw[:_BINARY_SNIFF]:
            return {**base, "binary": True, "truncated": False, "too_large": False, "content": ""}
        truncated = len(raw) > _MAX_BLOB_DISPLAY
        content = raw[:_MAX_BLOB_DISPLAY].decode("utf-8", errors="replace")
        return {**base, "binary": False, "truncated": truncated, "too_large": False, "content": content}

    def read_blob_raw(self, sot: Path, ref: str, path: str) -> bytes:
        """Octets **bruts** d'un fichier à une réf — sert binaire ET texte **tels quels** (là où `read_blob`
        blanchit binaire/too_large pour son JSON). Même ordre de gardes : `cat-file -t` (non-blob →
        `GitOpError`) → `-s` (taille) → au-delà de `_MAX_BLOB_READ` **lève** `BlobTooLargeError` (refus
        signalé, jamais un flux tronqué en silence) → sinon les octets via `_blob_bytes`. Read-only,
        bare-safe. Lève (`GitOpError`) si la réf/chemin est introuvable ou n'est pas un blob."""
        treeish = f"{ref}:{path.strip('/')}"
        kind = _checked(sot, "cat-file", "-t", treeish).stdout.strip()
        if kind != "blob":
            raise GitOpError(f"{path!r} @ {ref} n'est pas un fichier (type git : {kind or 'inconnu'})")
        size = int(_checked(sot, "cat-file", "-s", treeish).stdout.strip() or 0)
        if size > _MAX_BLOB_READ:
            raise BlobTooLargeError(
                f"{path!r} @ {ref} : {size} octets > plafond {_MAX_BLOB_READ} (téléchargement refusé)")
        return self._blob_bytes(sot, treeish)

    def blame(self, sot: Path, ref: str, path: str) -> list[dict]:
        """Blame ligne-à-ligne d'un fichier à une réf (`git blame --line-porcelain`) → **une entrée par
        ligne** `[{sha, author, date, summary}]` (sha court, date ISO UTC auteur). `--line-porcelain`
        répète les métadonnées à chaque ligne (pas d'état à cacher). Gardes calquées sur `read_blob` :
        non-blob → `GitOpError` ; au-delà de `_MAX_BLOB_READ` → `BlobTooLargeError` (413 signalé) ; binaire
        (NUL) → `GitOpError` (blame d'un binaire = non-sens). Read-only, bare-safe."""
        treeish = f"{ref}:{path.strip('/')}"
        kind = _checked(sot, "cat-file", "-t", treeish).stdout.strip()
        if kind != "blob":
            raise GitOpError(f"{path!r} @ {ref} n'est pas un fichier (type git : {kind or 'inconnu'})")
        size = int(_checked(sot, "cat-file", "-s", treeish).stdout.strip() or 0)
        if size > _MAX_BLOB_READ:
            raise BlobTooLargeError(
                f"{path!r} @ {ref} : {size} octets > plafond {_MAX_BLOB_READ} (blame refusé)")
        if b"\x00" in self._blob_bytes(sot, treeish)[:_BINARY_SNIFF]:
            raise GitOpError(f"{path!r} @ {ref} est binaire — blame indisponible")
        out = _checked(sot, "blame", "--line-porcelain", ref, "--", path.strip("/")).stdout
        rows: list[dict] = []
        cur: dict = {}
        for line in out.split("\n"):
            if line.startswith("\t"):          # ligne de contenu → clôt l'entrée courante
                rows.append(cur)
                cur = {}
            elif _is_blame_header(line):        # <sha40> <orig> <final> [<n>] → nouvelle entrée
                cur = {"sha": line[:10]}
            elif line.startswith("author "):
                cur["author"] = line[len("author "):]
            elif line.startswith("author-time "):
                cur["date"] = datetime.fromtimestamp(
                    int(line[len("author-time "):] or 0), UTC).isoformat()
            elif line.startswith("summary "):
                cur["summary"] = line[len("summary "):]
        return rows

    def search(self, sot: Path, ref: str, query: str, *, max_results: int = _MAX_GREP_RESULTS) -> dict:
        """Recherche plein-texte dans tous les fichiers d'une réf : `git grep -z -n -I -F -i` (fixed-string,
        insensible à la casse, binaires exclus) → `{results: [{path, line, text}], truncated, count}`, pour la
        palette « rechercher dans le code ». **Cap signalé** : au-delà de `max_results` on tronque ET on DIT
        `truncated=True` (`count` = total avant cap ; jamais de cap silencieux, invariant). Requête
        vide/blanche → résultat vide (un motif vide matcherait toute ligne). Seule primitive à invoquer `_git`
        (PAS `_checked`) : `git grep` **sort en code 1 quand il n'y a AUCUN match** — un vide légitime, pas
        une erreur ; un code ≥2 (réf introuvable, motif invalide) lève `GitOpError`. Read-only, bare-safe.
        `-z` NUL-délimite lineno+contenu (le contenu, dernier champ, ne se confond plus avec un `:`)."""
        if not query.strip():
            return {"results": [], "truncated": False, "count": 0}
        r = _git(sot, "grep", "-z", "-n", "-I", "-F", "-i", "--no-color", "-e", query, ref)
        if r.returncode == 1:               # git grep : 1 = aucun match (résultat vide, pas une erreur)
            return {"results": [], "truncated": False, "count": 0}
        if r.returncode != 0:               # ≥2 = réf introuvable / motif invalide → erreur dure
            raise GitOpError(f"git grep @ {ref}: {r.stderr.strip()[:200]}")
        rows: list[dict] = []
        for line in r.stdout.split("\n"):
            if not line:
                continue
            prefix, _, rest = line.partition("\x00")   # prefix = "<ref>:<path>" (une réf git n'a pas de ':')
            lineno, _, text = rest.partition("\x00")
            path = prefix.split(":", 1)[1] if ":" in prefix else prefix
            rows.append({"path": path, "line": int(lineno) if lineno.isdigit() else 0,
                         "text": text[:_MAX_MATCH_LEN]})
        return {"results": rows[:max_results], "truncated": len(rows) > max_results, "count": len(rows)}

    def archive(self, sot: Path, ref: str, dest_dir: Path) -> None:
        """Matérialise l'**arbre complet** d'une réf dans `dest_dir`, **sans working-tree** : `git archive`
        écrit un tar de l'arbre `<ref>`, détendu dans `dest_dir`. Sert à fournir un répertoire source réel à
        un outil externe (ex. `codemap build --root`) qui a besoin de fichiers sur disque — là où
        `ls_tree`/`read_blob` ne lisent que fichier-par-fichier. Extraction sûre (`filter="data"` :
        rejette chemins absolus/traversants) ; la source est de toute façon un SoT local de confiance.
        Read-only côté SoT. Lève (`GitOpError`) si la réf est introuvable ou l'extraction échoue."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".tar")
        os.close(fd)
        tar_path = Path(tmp_name)
        try:
            r = _git(sot, "archive", "--format=tar", f"--output={tar_path}", ref)
            if not r.ok:
                raise GitOpError(f"git archive {ref} @ {sot}: {r.stderr.strip()[:200]}")
            try:
                with tarfile.open(tar_path) as tf:
                    # `filter="data"` (extraction durcie) n'existe qu'à partir de 3.11.4 ; l'hôte de prod
                    # tourne en 3.11.2 → repli sur l'extraction simple (la source est un `git archive` d'un
                    # SoT local de confiance, jamais de chemin absolu/traversant).
                    if sys.version_info >= (3, 11, 4):
                        tf.extractall(dest_dir, filter="data")
                    else:
                        tf.extractall(dest_dir)
            except (tarfile.TarError, OSError) as exc:
                raise GitOpError(f"extraction archive {ref} @ {sot}: {exc}") from exc
        finally:
            tar_path.unlink(missing_ok=True)

    @staticmethod
    def _blob_bytes(sot: Path, treeish: str) -> bytes:
        """Lit un blob en **octets bruts** (le seam texte `core.run` décoderait en UTF-8 strict et
        planterait sur un binaire) — argv-liste, sans shell, même sûreté que `run`. Borné en amont par la
        garde de taille de `read_blob` (`size <= _MAX_BLOB_READ`)."""
        proc = subprocess.run(  # noqa: S603 — argv liste sans shell
            ["git", "-C", str(sot), "cat-file", "-p", treeish], capture_output=True, check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
            raise GitOpError(f"git cat-file -p {treeish} @ {sot}: {err}")
        return proc.stdout

    # -- intelligence git read-only (détail de commit + historique d'un fichier, bare-safe) ----------

    def commit_detail(self, sot: Path, sha: str) -> dict:
        """Détail d'un commit sur le SoT bare (read-only, bare-safe). Métadonnées via
        `git log -1 --format` (champs séparés par NUL → robuste aux retours-ligne du corps) ; fichiers
        touchés + `+/-` par fichier via `git show --numstat` (`add\\tdel\\tpath`, `-` pour un binaire).
        Renvoie `{sha, short, author, email, date, subject, body, files:[{path, additions, deletions,
        binary}]}`. Lève (`GitOpError`) si le sha/réf est introuvable → l'appelant mappe en 404."""
        fmt = "%H%x00%h%x00%an%x00%ae%x00%aI%x00%s%x00%b"
        meta = _checked(sot, "log", "-1", f"--format={fmt}", sha).stdout
        parts = meta.split("\x00")
        if len(parts) < 7:
            raise GitOpError(f"commit {sha} : métadonnées illisibles")
        full, short, author, email, date, subject, body = parts[:7]
        numstat = _checked(sot, "show", "--numstat", "--format=", sha).stdout
        files: list[dict] = []
        for line in numstat.splitlines():
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            adds, dels, path = cols[0], cols[1], cols[2]
            binary = adds == "-" or dels == "-"
            files.append({"path": path, "binary": binary,
                          "additions": None if binary else int(adds or 0),
                          "deletions": None if binary else int(dels or 0)})
        return {"sha": full, "short": short, "author": author, "email": email, "date": date,
                "subject": subject, "body": body.rstrip("\n"), "files": files}

    def file_history(self, sot: Path, ref: str, path: str, *, n: int = 50) -> list[dict]:
        """Historique des commits touchant un fichier à une réf (`log --format -n <n> <ref> -- <path>`),
        récents d'abord → `[{sha, short, author, date, subject}]`. Read-only, bare-safe. Lève si la réf est
        introuvable ; un chemin sans historique (fichier inconnu à cette réf) → liste vide (pas une erreur —
        c'est une réponse valide, l'appelant n'a pas à 404)."""
        fmt = "%H%x00%h%x00%an%x00%aI%x00%s"
        out = _checked(sot, "log", f"--max-count={n}", f"--format={fmt}", ref, "--", path).stdout
        rows: list[dict] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            cols = line.split("\x00")
            if len(cols) < 5:
                continue
            rows.append({"sha": cols[0], "short": cols[1], "author": cols[2],
                         "date": cols[3], "subject": cols[4]})
        return rows

    def entry_last_commits(self, sot: Path, ref: str, path: str, names: list[str]) -> dict[str, dict]:
        """Dernier commit touchant CHAQUE entrée d'un dossier (`log -1 --format <ref> -- <path/name>`),
        pour peupler la liste de fichiers façon GitHub. Renvoie `{name: {short, date, subject}}`. Read-only,
        bare-safe. `log -1 -- <path>` **s'arrête au premier commit** (borné : ne lit pas tout l'historique) ;
        une entrée sans commit à cette réf (edge) est simplement absente du dict (pas une erreur). O(N) appels
        bornés, N = entrées du dossier — aucun cap, rien de tronqué. Lève (`GitOpError`) si la réf est
        introuvable."""
        fmt = "%h%x00%aI%x00%s"
        prefix = path.strip("/")
        out: dict[str, dict] = {}
        for name in names:
            entry_path = f"{prefix}/{name}" if prefix else name
            meta = _checked(sot, "log", "-1", f"--format={fmt}", ref, "--", entry_path).stdout
            cols = meta.strip().split("\x00")
            if len(cols) < 3 or not cols[0]:
                continue
            out[name] = {"short": cols[0], "date": cols[1], "subject": cols[2]}
        return out

    def latest_commit(self, sot: Path, ref: str, path: str = "") -> dict | None:
        """Dernier commit de la réf (scopé au `path` si fourni) + nombre total de commits — coiffe la liste de
        fichiers (barre « latest commit » façon GitHub). Renvoie `{short, author, date, subject, count}`, ou
        `None` si aucun commit ne matche (réf/dossier vide à cette réf). Read-only, bare-safe. Lève
        (`GitOpError`) si la réf est introuvable."""
        fmt = "%h%x00%an%x00%aI%x00%s"
        pathspec = ["--", path.strip("/")] if path.strip("/") else []
        meta = _checked(sot, "log", "-1", f"--format={fmt}", ref, *pathspec).stdout
        cols = meta.strip().split("\x00")
        if len(cols) < 4 or not cols[0]:
            return None
        count = _checked(sot, "rev-list", "--count", ref, *pathspec).stdout.strip()
        return {"short": cols[0], "author": cols[1], "date": cols[2], "subject": cols[3],
                "count": int(count or 0)}

    def commit_worktree(self, worktree: Path, *, message: str, identity: tuple[str, str]) -> str | None:
        """Committe le travail de l'ouvrier dans son worktree (`add -A` puis `commit`). Le worker `claude -p`
        écrit le code mais **ne touche pas au cycle git** (mandat) : la forge committe après son run. Identité
        INJECTÉE le temps du commit (non persistée — spec merge-writeback). Rien à committer (arbre propre) →
        `None` (no-op propre, la feature reste alignée sur sa base). Sinon le SHA du commit créé."""
        env = writeback_env(identity)
        _checked(worktree, "add", "-A")
        if _git(worktree, "diff", "--cached", "--quiet").ok:   # rc 0 = rien de stagé → no-op
            return None
        _checked(worktree, "commit", "-m", message, env=env)
        return _checked(worktree, "rev-parse", "HEAD").stdout.strip()
