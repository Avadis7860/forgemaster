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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from cockpit.core import run

PROTECTED_BRANCHES = ("main", "dev")
_LOCK_NAME = "cockpit-worktree.lock"


class GitOpError(RuntimeError):
    """Échec dur d'une op git (le message porte stderr). Les échecs best-effort ne lèvent pas."""


def _git(repo: str | Path, *args: str, env: Mapping[str, str] | None = None) -> run.RunResult:
    """`git -C <repo> <args>` local, sans shell. Ne lève pas (l'appelant inspecte `.ok`/classifie)."""
    return run.run(["git", "-C", str(repo), *args], env=env)


def _checked(repo: str | Path, *args: str, env: Mapping[str, str] | None = None) -> run.RunResult:
    r = _git(repo, *args, env=env)
    if not r.ok:
        raise GitOpError(f"git {' '.join(args)} @ {repo}: {r.stderr.strip()[:200]}")
    return r


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
    """Adapter GitBackend sur repo bare local. Zéro réseau (le miroir est best-effort et opt-in)."""

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

    def merge_ff(self, sot: Path, *, into: str, source: str) -> None:
        """Merge **fast-forward** `source` dans `into` sur le SoT bare : `into` doit être ancêtre de
        `source` (sinon `GitOpError` non-ff). Sur un bare, un ff = avancer la ref (`branch -f`)."""
        if not _git(sot, "merge-base", "--is-ancestor", into, source).ok:
            raise GitOpError(f"merge non-ff : {into} n'est pas ancêtre de {source}")
        _checked(sot, "branch", "-f", into, source)

    def merge_writeback(self, sot: Path, *, creds_ref: str | None, identity: tuple[str, str]) -> None:
        """Writeback post-merge avec identité injectée (non persistée). En sot:local le SoT bare EST la
        vérité (les refs sont déjà à jour après `merge_ff`) : le writeback se réduit à l'identité + un push
        miroir **best-effort** si un remote est configuré (`creds_ref` résolu en amont, via l'env). Ne lève
        jamais sur échec miroir (spec forge-sot-local)."""
        env = writeback_env(identity)
        for remote in ("origin", "mirror"):
            if _git(sot, "remote", "get-url", remote).ok:
                _git(sot, "push", remote, "--all", env=env)  # best-effort, non bloquant
                break

    def push_mirror(self, sot: Path, remote: str) -> bool:
        """Pousse toutes les branches vers `remote`. **Best-effort** : retourne False sur échec, ne lève
        jamais (la vérité est le SoT local — spec forge-sot-local)."""
        return _git(sot, "push", remote, "--all").ok

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
