"""Tests de git/internal — primitives sur un SoT bare LOCAL réel (worktree, ff merge) + parsers purs +
injection d'identité writeback. Git est requis (présent en CI/dev)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cockpit.core import run
from cockpit.git.backend import GitBackend
from cockpit.git.internal import (
    BlobTooLargeError,
    GitOpError,
    InternalGit,
    classify_push_error,
    credential_env,
    is_protected_branch,
    parse_log,
    parse_status,
    writeback_env,
)

_ENV = writeback_env(("Test", "test@example.invalid"), base={"PATH": os.environ.get("PATH", "")})


def _run(*args: str, cwd: Path) -> None:
    r = run.run(["git", *args], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr


def _seed_bare(tmp: Path) -> Path:
    """Construit un SoT bare seedé (branches main + dev, 1 commit) via un repo seed jetable."""
    seed = tmp / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "main", cwd=seed)
    (seed / "readme.txt").write_text("seed\n", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "seed", cwd=seed)
    _run("branch", "dev", cwd=seed)
    sot = tmp / "sot"
    r = run.run(["git", "clone", "--bare", "-q", str(seed), str(sot)], env=_ENV)
    assert r.ok, r.stderr
    return sot


def test_init_sot_idempotent(tmp_path: Path):
    git = InternalGit()
    sot = tmp_path / "bare"
    git.init_sot(sot)
    assert run.run(["git", "-C", str(sot), "rev-parse", "--is-bare-repository"]).stdout.strip() == "true"
    # sans payload → arbre racine vide (compat historique)
    assert run.run(["git", "-C", str(sot), "ls-tree", "dev"]).stdout.strip() == ""
    git.init_sot(sot)  # 2e appel : no-op, ne lève pas


def test_init_sot_seeds_payload_tree_on_dev_and_main(tmp_path: Path):
    git = InternalGit()
    sot = tmp_path / "bare"
    payload = {
        "CLAUDE.md": "# rules\n",
        ".docsmap.toml": '[sources]\ndocs = "docs"\n',       # dotfile à la racine
        "docs/architecture.md": "# arch\n",                   # sous-arbre imbriqué
        ".claude/skills/work-loop/SKILL.md": "# work-loop\n",  # sous-arbre profond sous dotdir
    }
    git.init_sot(sot, payload=payload)
    for branch in ("dev", "main"):
        listed = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", branch]).stdout.split()
        assert set(listed) == set(payload), f"{branch}: {listed}"
    # contenu fidèle (le blob = le contenu exact fourni)
    for rel, content in payload.items():
        assert run.run(["git", "-C", str(sot), "show", f"dev:{rel}"]).stdout == content
    # idempotence : un 2e init (autre payload) ne clobbere pas la racine déjà semée
    git.init_sot(sot, payload={"OTHER.md": "x\n"})
    again = run.run(["git", "-C", str(sot), "ls-tree", "-r", "--name-only", "dev"]).stdout.split()
    assert "OTHER.md" not in again and "CLAUDE.md" in again


def test_init_sot_points_head_at_main_so_clone_checks_out_seed(tmp_path: Path):
    """Non-régression : le HEAD du bare doit résoudre `refs/heads/main` (pas le `master` par défaut d'un
    `git init --bare`, jamais créé par la forge) — sinon `git clone` du SoT sort « remote HEAD refers to
    nonexistent ref » et ne matérialise AUCUN fichier (le client tombe sur un arbre vide)."""
    git = InternalGit()
    sot = tmp_path / "bare"
    git.init_sot(sot, payload={"README.md": "# seed\n"})
    # 1. le HEAD du bare pointe main (la branche promue), pas master
    head = run.run(["git", "-C", str(sot), "symbolic-ref", "HEAD"]).stdout.strip()
    assert head == "refs/heads/main"
    # 2. un `git clone` matérialise l'arbre du seed, sans warning HEAD-inexistant
    wt = tmp_path / "clone"
    cloned = run.run(["git", "clone", str(sot), str(wt)])
    assert cloned.ok, cloned.stderr
    assert "nonexistent ref" not in cloned.stderr
    assert (wt / "README.md").read_text(encoding="utf-8") == "# seed\n"


def test_worktree_lifecycle_and_branch(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare(tmp_path)
    wt = tmp_path / "wt-feature"
    git.add_worktree(sot, wt, branch="feature/x", base="dev")
    assert git.current_branch(wt) == "feature/x"
    assert git.status(wt)["clean"] is True
    # cleanup : remove worktree AVANT delete branch (spec worktree-cleanup)
    git.remove_worktree(sot, wt)
    git.delete_branch(sot, "feature/x")
    branches = run.run(["git", "-C", str(sot), "branch", "--format=%(refname:short)"]).stdout.split()
    assert "feature/x" not in branches


def test_merge_ff_advances_and_rejects_non_ff(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare(tmp_path)
    wt = tmp_path / "wt"
    git.add_worktree(sot, wt, branch="feature/x", base="dev")
    (wt / "f.txt").write_text("work\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "feature work", cwd=wt)
    feat_sha = run.run(["git", "-C", str(wt), "rev-parse", "HEAD"]).stdout.strip()

    # ff : dev est ancêtre de feature/x → avance
    git.merge_ff(sot, into="dev", source="feature/x")
    dev_sha = run.run(["git", "-C", str(sot), "rev-parse", "dev"]).stdout.strip()
    assert dev_sha == feat_sha

    # non-ff : feature/x n'est pas ancêtre de dev (identiques désormais, mais on force une divergence)
    (wt / "g.txt").write_text("more\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "more", cwd=wt)
    with pytest.raises(GitOpError):
        git.merge_ff(sot, into="feature/x", source="dev")  # dev derrière feature/x → non-ff


def test_git_view_reads_branches_log_and_ahead_behind(tmp_path: Path):
    """Les primitives read-only de la vue git : branches (nom·sha·sujet), log par réf, et ahead/behind
    `main` vs `dev` — bare-safe (aucun working-tree du SoT), et le signal « main rattrape dev »."""
    git = InternalGit()
    sot = _seed_bare(tmp_path)  # main + dev sur le même commit « seed »

    # branches : dev + main, triées, chacune avec sha court + sujet du commit de tête
    branches = git.branches(sot)
    assert [b["name"] for b in branches] == ["dev", "main"]
    assert all(b["sha"] and b["subject"] == "seed" for b in branches)

    # log court d'une réf → [{sha, subject}] (parser pur)
    log_dev = git.log(sot, "dev", n=5)
    assert len(log_dev) == 1 and log_dev[0]["subject"] == "seed"

    # au départ dev == main → 0 ahead / 0 behind
    assert git.ahead_behind(sot, base="main", head="dev") == {
        "base": "main", "head": "dev", "ahead": 0, "behind": 0}

    # dev avance d'un commit (feature ff'd dans dev) → dev en avance de 1 sur main (main doit rattraper)
    wt = tmp_path / "wt"
    git.add_worktree(sot, wt, branch="feature/x", base="dev")
    (wt / "f.txt").write_text("work\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "feature work", cwd=wt)
    git.merge_ff(sot, into="dev", source="feature/x")
    ab = git.ahead_behind(sot, base="main", head="dev")
    assert ab == {"base": "main", "head": "dev", "ahead": 1, "behind": 0}
    assert len(git.log(sot, "dev", n=5)) == 2  # seed + feature work


def test_git_read_ops_raise_on_missing_ref(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare(tmp_path)
    with pytest.raises(GitOpError):
        git.log(sot, "nope")
    with pytest.raises(GitOpError):
        git.ahead_behind(sot, base="main", head="nope")


def test_writeback_env_injects_identity_without_mutating_os_environ():
    before = dict(os.environ)
    env = writeback_env(("Vault Writeback", "wb@example.invalid"))
    assert env["GIT_AUTHOR_NAME"] == "Vault Writeback"
    assert env["GIT_AUTHOR_EMAIL"] == "wb@example.invalid"
    assert env["GIT_COMMITTER_NAME"] == "Vault Writeback"
    assert "GIT_AUTHOR_NAME" not in os.environ  # non persisté (spec merge-writeback)
    assert dict(os.environ) == before


def test_credential_env_injects_token_transiently_without_mutating_base():
    base = {"PATH": "/x", "GIT_AUTHOR_NAME": "wb"}
    env = credential_env("gh-token-123", base=base)
    # le token est injecté via GIT_CONFIG_* url.insteadOf github (jamais dans un .gitconfig, jamais l'argv)
    assert env["GIT_CONFIG_KEY_0"] == "url.https://x-access-token:gh-token-123@github.com/.insteadOf"
    assert env["GIT_CONFIG_VALUE_0"] == "https://github.com/"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"      # fail-loud, jamais de prompt interactif
    assert env["GIT_AUTHOR_NAME"] == "wb"         # composé au-dessus de l'identité writeback
    assert base == {"PATH": "/x", "GIT_AUTHOR_NAME": "wb"}   # base NON mutée (transitoire)


def test_credential_env_composes_over_existing_git_config_count():
    # un GIT_CONFIG_COUNT préexistant → on APPEND à l'index libre, sans écraser l'entrée 0
    base = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "x"}
    env = credential_env("tok", base=base)
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "user.name"           # l'entrée existante intacte
    assert env["GIT_CONFIG_KEY_1"] == "url.https://x-access-token:tok@github.com/.insteadOf"


def test_merge_writeback_resolves_creds_ref_then_pushes_mirror(tmp_path: Path):
    """Le writeback résout `creds_ref` → token À L'USAGE (via le résolveur injecté) et pousse le miroir.
    Le remote local n'est pas github → l'`insteadOf` est inerte, mais on PROUVE que la réf est bien résolue
    au moment du writeback (couture credential — invisible aux fakes de transport, cf. spec)."""
    sot = _seed_bare(tmp_path)
    mirror = tmp_path / "mirror.git"
    assert run.run(["git", "init", "--bare", "-q", str(mirror)], env=_ENV).ok
    run.run(["git", "-C", str(sot), "remote", "remove", "origin"], env=_ENV)   # clone bare → origin=seed
    run.run(["git", "-C", str(sot), "remote", "add", "mirror", str(mirror)], env=_ENV)

    seen: list[str] = []
    def resolver(ref: str) -> str:
        seen.append(ref)
        return "gh-token-xyz"

    git = InternalGit(cred_resolver=resolver)
    git.merge_writeback(sot, creds_ref="ref-abc", identity=("Writeback", "wb@example.invalid"))
    assert seen == ["ref-abc"]                                    # résolu au writeback, une fois
    # le miroir a bien reçu les branches (push best-effort réussi)
    assert run.run(["git", "-C", str(mirror), "rev-parse", "dev"], env=_ENV).ok


def test_merge_writeback_without_resolver_ignores_creds_ref(tmp_path: Path):
    """Sans résolveur injecté (défaut), une `creds_ref` est ignorée sans erreur : push ambiant best-effort
    (compat historique). Aucun remote configuré → no-op propre, ne lève pas."""
    sot = _seed_bare(tmp_path)
    InternalGit().merge_writeback(sot, creds_ref="ref-abc", identity=("Wb", "wb@example.invalid"))


def test_merge_writeback_missing_secret_degrades_to_ambient_push(tmp_path: Path):
    """Un résolveur total qui renvoie '' (secret absent/illisible) → push ambiant best-effort, jamais une
    exception (le miroir ne bloque jamais le merge — spec forge-sot-local)."""
    sot = _seed_bare(tmp_path)
    mirror = tmp_path / "mirror.git"
    assert run.run(["git", "init", "--bare", "-q", str(mirror)], env=_ENV).ok
    run.run(["git", "-C", str(sot), "remote", "remove", "origin"], env=_ENV)   # clone bare → origin=seed
    run.run(["git", "-C", str(sot), "remote", "add", "mirror", str(mirror)], env=_ENV)
    git = InternalGit(cred_resolver=lambda ref: "")           # secret introuvable → ''
    git.merge_writeback(sot, creds_ref="ref-gone", identity=("Wb", "wb@example.invalid"))
    assert run.run(["git", "-C", str(mirror), "rev-parse", "dev"], env=_ENV).ok


def test_set_remote_is_idempotent_add_then_seturl(tmp_path: Path):
    """set_remote ajoute le remote absent, puis bascule en `set-url` au ré-appel (idempotent)."""
    git = InternalGit()
    sot = _seed_bare(tmp_path)
    run.run(["git", "-C", str(sot), "remote", "remove", "origin"], env=_ENV)   # clone bare → origin=seed
    git.set_remote(sot, "mirror", "https://example.invalid/a.git")
    assert run.run(["git", "-C", str(sot), "remote", "get-url", "mirror"], env=_ENV).stdout.strip() \
        == "https://example.invalid/a.git"
    git.set_remote(sot, "mirror", "https://example.invalid/b.git")             # ré-appel → set-url
    assert run.run(["git", "-C", str(sot), "remote", "get-url", "mirror"], env=_ENV).stdout.strip() \
        == "https://example.invalid/b.git"


def test_remove_remote_is_best_effort(tmp_path: Path):
    """remove_remote retire un remote présent et ne lève pas sur un remote absent."""
    git = InternalGit()
    sot = _seed_bare(tmp_path)
    git.set_remote(sot, "mirror", "https://example.invalid/a.git")
    git.remove_remote(sot, "mirror")
    assert not run.run(["git", "-C", str(sot), "remote", "get-url", "mirror"], env=_ENV).ok
    git.remove_remote(sot, "mirror")                          # absent → no-op, ne lève pas


def test_fetch_remote_updates_tracking_and_resolves_creds_transiently(tmp_path: Path):
    """fetch_remote met à jour les refs de suivi `refs/remotes/<remote>/*` ET résout `creds_ref` À L'USAGE
    (la couture d'auth, invisible aux fakes de transport). Remote local → l'insteadOf github est inerte, mais
    le fetch réussit et on PROUVE la résolution + que la ref de suivi reflète l'upstream."""
    up_root = tmp_path / "up"                                # _seed_bare suppose un parent existant
    down_root = tmp_path / "down"
    up_root.mkdir()
    down_root.mkdir()
    upstream = _seed_bare(up_root)                           # « GitHub » local (bare seedé, dev+main)
    sot = _seed_bare(down_root)                              # SoT local à re-synchroniser
    wt = tmp_path / "up-wt"                                  # avance `dev` sur l'upstream → écart à fetcher
    run.run(["git", "-C", str(upstream), "worktree", "add", "-q", str(wt), "dev"], env=_ENV)
    (wt / "new.txt").write_text("ahead\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "ahead", cwd=wt)
    up_dev = run.run(["git", "-C", str(upstream), "rev-parse", "dev"], env=_ENV).stdout.strip()

    seen: list[str] = []
    git = InternalGit(cred_resolver=lambda ref: (seen.append(ref) or "gh-token"))
    git.set_remote(sot, "mirror", str(upstream))
    assert git.fetch_remote(sot, "mirror", creds_ref="ref-1") is True
    assert seen == ["ref-1"]                                 # résolu à l'usage, une fois
    tracked = run.run(
        ["git", "-C", str(sot), "rev-parse", "refs/remotes/mirror/dev"], env=_ENV).stdout.strip()
    assert tracked == up_dev                                 # la ref de suivi reflète bien l'upstream


def test_fetch_remote_missing_remote_returns_false_without_raising(tmp_path: Path):
    """Remote inexistant → fetch_remote retourne False, ne lève jamais (dégradation honnête → `unreachable`
    côté divergence P2). Best-effort strict : le miroir ne bloque jamais (spec forge-sot-local)."""
    sot = _seed_bare(tmp_path)
    assert InternalGit().fetch_remote(sot, "nope") is False


def _advance(bare: Path, branch: str, wt: Path) -> str:
    """Avance `branch` d'un commit sur un dépôt bare (worktree jetable). Renvoie le nouveau SHA."""
    run.run(["git", "-C", str(bare), "worktree", "add", "-q", str(wt), branch], env=_ENV)
    (wt / "change.txt").write_text(f"{wt.name}\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", f"advance {branch}", cwd=wt)
    return run.run(["git", "-C", str(bare), "rev-parse", branch], env=_ENV).stdout.strip()


def _sot_with_mirror(tmp_path: Path) -> tuple[Path, Path, InternalGit]:
    """Un upstream « GitHub » bare seedé + un SoT cloné DE lui (histoire partagée) + remote `mirror` câblé."""
    up_root = tmp_path / "up"
    down_root = tmp_path / "down"
    up_root.mkdir()
    down_root.mkdir()
    upstream = _seed_bare(up_root)
    sot = down_root / "sot"
    assert run.run(["git", "clone", "--bare", "-q", str(upstream), str(sot)], env=_ENV).ok
    git = InternalGit()
    git.set_remote(sot, "mirror", str(upstream))
    return upstream, sot, git


def test_internal_git_satisfies_extended_backend_contract():
    """InternalGit honore le contrat `GitBackend` étendu (bump P2 : `remote_divergence`) — garde-fou du
    contrat figé runtime_checkable après ajout d'une méthode."""
    assert isinstance(InternalGit(), GitBackend)


def test_remote_divergence_all_synced(tmp_path: Path):
    """SoT fraîchement cloné de l'upstream, rien n'a bougé → chaque branche `synced`, rollup `synced`."""
    _upstream, sot, git = _sot_with_mirror(tmp_path)
    d = git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))
    assert d["fetched"] is True and d["state"] == "synced"
    assert d["branches"]["dev"] == {"ahead": 0, "behind": 0, "state": "synced"}
    assert d["branches"]["main"] == {"ahead": 0, "behind": 0, "state": "synced"}


def test_remote_divergence_remote_ahead(tmp_path: Path):
    """GitHub prend de l'avance sur `dev` (cas vécu : travail hors cockpit) → `dev` `remote_ahead` (behind>0,
    ahead=0), `main` `synced`, rollup `remote_ahead`."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    d = git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))
    assert d["branches"]["dev"] == {"ahead": 0, "behind": 1, "state": "remote_ahead"}
    assert d["branches"]["main"]["state"] == "synced"
    assert d["state"] == "remote_ahead"


def test_remote_divergence_local_ahead(tmp_path: Path):
    """Le SoT local prend de l'avance sur `dev` → `dev` `local_ahead` (ahead>0, behind=0), rollup
    `local_ahead` (à pousser, pas à ff)."""
    _upstream, sot, git = _sot_with_mirror(tmp_path)
    _advance(sot, "dev", tmp_path / "s1")
    d = git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))
    assert d["branches"]["dev"] == {"ahead": 1, "behind": 0, "state": "local_ahead"}
    assert d["state"] == "local_ahead"


def test_remote_divergence_true_non_ff_on_one_branch(tmp_path: Path):
    """`dev` avancé DES DEUX côtés → vraie divergence non-ff sur la branche (ahead>0 ET behind>0) → `diverged`
    (spec forge-sot-local : signalé, jamais auto-mergé)."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    _advance(sot, "dev", tmp_path / "s1")
    d = git.remote_divergence(sot, remote="mirror", branches=("dev",))
    assert d["branches"]["dev"] == {"ahead": 1, "behind": 1, "state": "diverged"}
    assert d["state"] == "diverged"


def test_remote_divergence_cross_branch_rolls_up_diverged(tmp_path: Path):
    """Branches tirant en sens opposés (`dev` remote-avance, `main` local-avance) → aucune réconciliation ff
    unique possible → rollup `diverged` même si chaque branche est ff-able isolément."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    _advance(sot, "main", tmp_path / "s1")
    d = git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))
    assert d["branches"]["dev"]["state"] == "remote_ahead"
    assert d["branches"]["main"]["state"] == "local_ahead"
    assert d["state"] == "diverged"


def test_remote_divergence_branch_absent_on_remote_is_local_ahead(tmp_path: Path):
    """Branche locale absente du remote → tout le local est « en avance » (`local_ahead`), jamais `synced`
    (0/0 faux-vert interdit sur une branche non poussée)."""
    _upstream, sot, git = _sot_with_mirror(tmp_path)
    assert run.run(["git", "-C", str(sot), "branch", "feature-x", "dev"], env=_ENV).ok
    d = git.remote_divergence(sot, remote="mirror", branches=("feature-x",))
    fx = d["branches"]["feature-x"]
    assert fx["behind"] == 0 and fx["ahead"] >= 1 and fx["state"] == "local_ahead"
    assert d["state"] == "local_ahead"


def test_remote_divergence_degrades_honestly_no_mirror_and_unreachable(tmp_path: Path):
    """Dégradation honnête — jamais 0/0 faux-vert : pas de remote → `no_mirror` ; remote injoignable →
    `unreachable`. Dans les deux cas `fetched=False`, `branches={}` (rien à comparer, on le DIT)."""
    sot = _seed_bare(tmp_path)
    git = InternalGit()
    d0 = git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))
    assert d0 == {"remote": "mirror", "fetched": False, "branches": {}, "state": "no_mirror"}
    git.set_remote(sot, "mirror", str(tmp_path / "does-not-exist.git"))
    d1 = git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))
    assert d1 == {"remote": "mirror", "fetched": False, "branches": {}, "state": "unreachable"}


def _sha(bare: Path, ref: str) -> str:
    return run.run(["git", "-C", str(bare), "rev-parse", ref], env=_ENV).stdout.strip()


def test_reconcile_fast_forwards_remote_ahead(tmp_path: Path):
    """GitHub a pris de l'avance sur `dev` (cas vécu) → reconcile **ff** la branche locale vers la ref de
    suivi (jamais de merge). `dev` passe `fast_forward` (from→to), `changed=True`, et une re-mesure de la
    divergence retombe `synced` (la réconciliation a bien rattrapé)."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    up_dev = _advance(upstream, "dev", tmp_path / "u1")
    before = _sha(sot, "dev")
    rep = git.reconcile(sot, remote="mirror", branches=("dev", "main"))
    assert rep["fetched"] is True and rep["changed"] is True and rep["blocked"] == []
    assert rep["actions"]["dev"] == {"action": "fast_forward", "from": before, "to": up_dev}
    assert rep["actions"]["main"] == {"action": "already_synced"}
    assert _sha(sot, "dev") == up_dev                       # la ref locale a bien avancé au niveau GitHub
    assert git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))["state"] == "synced"


def test_reconcile_pushes_local_ahead_to_mirror(tmp_path: Path):
    """Le SoT local est en avance sur `dev` → reconcile **push ff** cette branche vers le miroir (best-effort,
    jamais `--force`). Le miroir reçoit le commit ; action `pushed`, `changed=True`."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    sot_dev = _advance(sot, "dev", tmp_path / "s1")
    rep = git.reconcile(sot, remote="mirror", branches=("dev", "main"))
    assert rep["actions"]["dev"] == {"action": "pushed"} and rep["changed"] is True
    assert rep["blocked"] == []
    assert _sha(upstream, "dev") == sot_dev                 # le miroir a bien reçu l'avance locale
    assert git.remote_divergence(sot, remote="mirror", branches=("dev", "main"))["state"] == "synced"


def test_reconcile_blocks_diverged_without_mutation(tmp_path: Path):
    """`dev` a divergé (avancé DES DEUX côtés, non-ff) → **bloqué**, AUCUNE mutation : ni ff ni push (spec
    forge-sot-local, jamais d'auto-merge). La ref locale ne bouge pas, `blocked` liste `dev`."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    local_dev = _advance(sot, "dev", tmp_path / "s1")
    up_dev = _sha(upstream, "dev")
    rep = git.reconcile(sot, remote="mirror", branches=("dev",))
    assert rep["actions"]["dev"]["action"] == "blocked_diverged" and rep["blocked"] == ["dev"]
    assert rep["changed"] is False
    assert _sha(sot, "dev") == local_dev                    # ref locale INTACTE (aucune mutation)
    assert _sha(upstream, "dev") == up_dev                  # miroir INTACT (rien poussé)


def test_reconcile_guards_checked_out_branch(tmp_path: Path):
    """Garde-fou : `dev` est en avance côté GitHub (ff-able) MAIS sortie dans un worktree actif → on ne la ff
    JAMAIS (`branch -f` la refuserait) → `blocked_worktree`, aucune mutation de la ref locale."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    wt = tmp_path / "sot-dev-wt"                            # sort `dev` sur le SoT → checked-out
    assert run.run(["git", "-C", str(sot), "worktree", "add", "-q", str(wt), "dev"], env=_ENV).ok
    local_dev = _sha(sot, "dev")
    rep = git.reconcile(sot, remote="mirror", branches=("dev",))
    assert rep["actions"]["dev"]["action"] == "blocked_worktree" and rep["blocked"] == ["dev"]
    assert rep["changed"] is False and _sha(sot, "dev") == local_dev   # ref locale INTACTE


def test_reconcile_cross_branch_reconciles_each_independently(tmp_path: Path):
    """Rollup `diverged` cross-branche (`dev` remote-avance, `main` local-avance) MAIS chaque branche est
    ff-able **isolément** → reconcile agit **par branche** : `dev` ff'd, `main` pushed. La granularité
    par-branche bat le rollup conservateur — rien n'est bloqué car aucune branche n'est vraiment non-ff."""
    upstream, sot, git = _sot_with_mirror(tmp_path)
    up_dev = _advance(upstream, "dev", tmp_path / "u1")
    sot_main = _advance(sot, "main", tmp_path / "s1")
    rep = git.reconcile(sot, remote="mirror", branches=("dev", "main"))
    assert rep["actions"]["dev"]["action"] == "fast_forward" and rep["actions"]["dev"]["to"] == up_dev
    assert rep["actions"]["main"] == {"action": "pushed"}
    assert rep["changed"] is True and rep["blocked"] == []
    assert _sha(sot, "dev") == up_dev and _sha(upstream, "main") == sot_main


def test_reconcile_degrades_honestly_no_mirror_and_unreachable(tmp_path: Path):
    """Dégradation honnête (héritée de `remote_divergence`) : pas de miroir → `no_mirror` ; injoignable →
    `unreachable`. Dans les deux cas `fetched=False`, aucune action, rien de bloqué (rien à réconcilier)."""
    sot = _seed_bare(tmp_path)
    git = InternalGit()
    r0 = git.reconcile(sot, remote="mirror", branches=("dev", "main"))
    assert r0 == {"remote": "mirror", "fetched": False, "actions": {}, "changed": False,
                  "blocked": [], "state": "no_mirror"}
    git.set_remote(sot, "mirror", str(tmp_path / "does-not-exist.git"))
    r1 = git.reconcile(sot, remote="mirror", branches=("dev", "main"))
    assert r1["fetched"] is False and r1["state"] == "unreachable" and r1["actions"] == {}


# -- sync_tracking (P6 : re-sync pull-only ff d'un outil adopté, remote `origin` sans refspec) -------

def _tool_clone(tmp_path: Path) -> tuple[Path, Path, InternalGit]:
    """Un upstream « GitHub » bare seedé + un SoT **cloné bare** de lui — exactement comme un outil adopté par
    `clone_sot` : `origin` est posé par le clone MAIS **sans refspec de fetch** (gotcha bare-clone que
    `sync_tracking` doit auto-réparer). Contraste avec `_sot_with_mirror` (remote `mirror` via `remote add`,
    qui, lui, pose le refspec)."""
    up_root = tmp_path / "up"
    down_root = tmp_path / "down"
    up_root.mkdir()
    down_root.mkdir()
    upstream = _seed_bare(up_root)
    sot = down_root / "tool.git"
    assert run.run(["git", "clone", "--bare", "-q", str(upstream), str(sot)], env=_ENV).ok
    return upstream, sot, InternalGit()


def test_ensure_fetch_refspec_self_heals_bare_clone(tmp_path: Path):
    """Un `git clone --bare` ne pose AUCUN refspec de fetch → un `fetch origin` nu ne peuple pas
    `refs/remotes/origin/*`. `ensure_fetch_refspec` le répare (idempotent) ; après, le fetch peuple bien les
    refs de suivi. Garde-fou du gotcha central de P6 (dégèle un outil déjà adopté)."""
    _upstream, sot, git = _tool_clone(tmp_path)
    assert not run.run(["git", "-C", str(sot), "config", "--get", "remote.origin.fetch"], env=_ENV).ok
    git.ensure_fetch_refspec(sot, "origin")
    got = run.run(["git", "-C", str(sot), "config", "--get", "remote.origin.fetch"], env=_ENV).stdout.strip()
    assert got == "+refs/heads/*:refs/remotes/origin/*"
    git.ensure_fetch_refspec(sot, "origin")   # idempotent : ré-appliquer ne duplique pas
    all_specs = run.run(["git", "-C", str(sot), "config", "--get-all", "remote.origin.fetch"],
                        env=_ENV).stdout.strip().splitlines()
    assert all_specs == ["+refs/heads/*:refs/remotes/origin/*"]
    assert git.fetch_remote(sot, "origin") is True
    assert _sha(sot, "refs/remotes/origin/dev") == _sha(sot, "dev")


def test_sync_tracking_fast_forwards_remote_ahead(tmp_path: Path):
    """L'amont a pris de l'avance sur `dev` (cas vécu : outil figé au commit d'adoption, travaillé en amont) →
    `sync_tracking` **ff** la branche locale (auto-réparant le refspec au passage). `dev` passe `fast_forward`
    (from→to), `changed=True`, et une re-mesure retombe `synced`."""
    upstream, sot, git = _tool_clone(tmp_path)
    up_dev = _advance(upstream, "dev", tmp_path / "u1")
    before = _sha(sot, "dev")
    rep = git.sync_tracking(sot, remote="origin", branches=("dev", "main"))
    assert rep["fetched"] is True and rep["changed"] is True and rep["blocked"] == []
    assert rep["actions"]["dev"] == {"action": "fast_forward", "from": before, "to": up_dev}
    assert rep["actions"]["main"] == {"action": "already_synced"}
    assert _sha(sot, "dev") == up_dev
    assert git.remote_divergence(sot, remote="origin", branches=("dev", "main"))["state"] == "synced"


def test_sync_tracking_skips_local_ahead_never_pushes(tmp_path: Path):
    """Frontière read-only stricte : le SoT local a des commits d'avance sur `dev` → `sync_tracking` NE
    POUSSE JAMAIS l'amont (contraste avec `reconcile` qui, lui, pousse). Action `local_ahead_skipped`,
    `changed=False`, l'amont reste INTACT."""
    upstream, sot, git = _tool_clone(tmp_path)
    up_dev_before = _sha(upstream, "dev")
    _advance(sot, "dev", tmp_path / "s1")
    rep = git.sync_tracking(sot, remote="origin", branches=("dev", "main"))
    assert rep["actions"]["dev"] == {
        "action": "local_ahead_skipped",
        "reason": "dev a des commits locaux — outil read-only, aucun push amont"}
    assert rep["changed"] is False and rep["blocked"] == []
    assert _sha(upstream, "dev") == up_dev_before   # amont INTACT (aucun push, jamais)


def test_sync_tracking_blocks_diverged_without_mutation(tmp_path: Path):
    """`dev` a divergé (avancé DES DEUX côtés, non-ff) → **bloqué**, AUCUNE mutation (ni ff ni push). La ref
    locale ne bouge pas, `blocked` liste `dev` (spec forge-sot-local : jamais d'auto-merge)."""
    upstream, sot, git = _tool_clone(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    local_dev = _advance(sot, "dev", tmp_path / "s1")
    rep = git.sync_tracking(sot, remote="origin", branches=("dev",))
    assert rep["actions"]["dev"]["action"] == "blocked_diverged" and rep["blocked"] == ["dev"]
    assert rep["changed"] is False
    assert _sha(sot, "dev") == local_dev            # ref locale INTACTE


def test_sync_tracking_guards_checked_out_branch(tmp_path: Path):
    """Garde-fou : `dev` est ff-able (amont en avance) MAIS sortie dans un worktree actif → on ne la ff JAMAIS
    (`branch -f` la refuserait) → `blocked_worktree`, aucune mutation de la ref locale."""
    upstream, sot, git = _tool_clone(tmp_path)
    _advance(upstream, "dev", tmp_path / "u1")
    wt = tmp_path / "tool-dev-wt"
    assert run.run(["git", "-C", str(sot), "worktree", "add", "-q", str(wt), "dev"], env=_ENV).ok
    local_dev = _sha(sot, "dev")
    rep = git.sync_tracking(sot, remote="origin", branches=("dev",))
    assert rep["actions"]["dev"]["action"] == "blocked_worktree" and rep["blocked"] == ["dev"]
    assert rep["changed"] is False and _sha(sot, "dev") == local_dev


def test_sync_tracking_degrades_honestly_when_unreachable(tmp_path: Path):
    """Dégradation honnête : `origin` pointant sur un chemin inexistant → fetch KO → `unreachable`,
    `fetched=False`, aucune action, rien de bloqué (jamais un 0/0 faux-vert)."""
    _upstream, sot, git = _tool_clone(tmp_path)
    git.set_remote(sot, "origin", str(tmp_path / "does-not-exist.git"))
    rep = git.sync_tracking(sot, remote="origin", branches=("dev", "main"))
    assert rep["fetched"] is False and rep["state"] == "unreachable"
    assert rep["actions"] == {} and rep["changed"] is False and rep["blocked"] == []


def test_pure_parsers_and_classifiers():
    status = parse_status(
        "# branch.head feature/x\n# branch.ab +2 -1\n1 M. N... 100644 100644 100644 aa bb file.py\n"
    )
    assert status["branch"] == "feature/x"
    assert status["ahead"] == 2 and status["behind"] == 1
    assert status["files"][0] == {"path": "file.py", "index": "M", "worktree": ".", "staged": True}
    assert status["clean"] is False

    log = parse_log("abc123 fix: bug\ndef456 feat: thing\n")
    assert log == [{"sha": "abc123", "subject": "fix: bug"}, {"sha": "def456", "subject": "feat: thing"}]

    assert classify_push_error("", "! [rejected] non-fast-forward") == "behind"
    assert classify_push_error("", "write access to repository not granted") == "pat-scope"
    assert classify_push_error("", "could not read Username") == "auth"
    assert classify_push_error("", "") == "other"
    assert is_protected_branch("main") and is_protected_branch("dev")
    assert not is_protected_branch("feature/x")


# -- exploration read-only : arbre + contenu (ls_tree / read_blob) ---------------------------------

def _seed_bare_rich(tmp: Path) -> Path:
    """SoT bare seedé avec un sous-dossier, un fichier texte et un fichier binaire (NUL) sur `dev`."""
    seed = tmp / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "dev", cwd=seed)
    (seed / "README.md").write_text("# projet\nligne 2\n", encoding="utf-8")
    (seed / "src").mkdir()
    (seed / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (seed / "data.bin").write_bytes(b"\x00\x01\x02binaire\x00")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "rich seed", cwd=seed)
    sot = tmp / "sot"
    r = run.run(["git", "clone", "--bare", "-q", str(seed), str(sot)], env=_ENV)
    assert r.ok, r.stderr
    return sot


def test_ls_tree_lists_root_and_subdir_dirs_first(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    root = git.ls_tree(sot, "dev")
    names = [e["name"] for e in root]
    # src (tree) d'abord, puis les blobs alphabétiques
    assert names == ["src", "README.md", "data.bin"]
    src = next(e for e in root if e["name"] == "src")
    assert src["type"] == "tree" and src["size"] is None and src["sha"]
    readme = next(e for e in root if e["name"] == "README.md")
    assert readme["type"] == "blob" and readme["size"] == len("# projet\nligne 2\n")
    # descente dans un sous-dossier
    sub = git.ls_tree(sot, "dev", "src")
    assert [e["name"] for e in sub] == ["app.py"] and sub[0]["type"] == "blob"


def test_ls_tree_bad_ref_or_blob_path_raises(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    with pytest.raises(GitOpError):
        git.ls_tree(sot, "nexiste-pas")
    with pytest.raises(GitOpError):        # README.md est un blob, pas un dossier
        git.ls_tree(sot, "dev", "README.md")


def test_tags_lists_refs_tags_with_subjects(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    assert git.tags(sot) == []                                  # aucun tag au départ
    # tag annoté (sujet = message du tag) + tag léger (sujet = sujet du commit pointé), créés sur le SoT bare
    _run("tag", "-a", "v1.0", "-m", "release 1.0", "dev", cwd=sot)
    _run("tag", "v0.9-lw", "dev", cwd=sot)
    tags = git.tags(sot)
    assert {t["name"] for t in tags} == {"v1.0", "v0.9-lw"}     # les deux tags listés
    assert all(t["sha"] for t in tags)
    annotated = next(t for t in tags if t["name"] == "v1.0")
    assert annotated["subject"] == "release 1.0"               # message du tag annoté
    lightweight = next(t for t in tags if t["name"] == "v0.9-lw")
    assert lightweight["subject"] == "rich seed"               # sujet du commit pointé (tag léger)


def test_list_paths_flat_recursive_with_signaled_cap(tmp_path: Path, monkeypatch):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    result = git.list_paths(sot, "dev")
    assert set(result["paths"]) == {"README.md", "data.bin", "src/app.py"}   # plate + récursive
    assert result["truncated"] is False
    # cap SIGNALÉ : au-delà du seuil, on tronque ET on le dit (jamais silencieux)
    from cockpit.git import internal
    monkeypatch.setattr(internal, "_MAX_TREE_PATHS", 2)
    capped = git.list_paths(sot, "dev")
    assert len(capped["paths"]) == 2 and capped["truncated"] is True
    with pytest.raises(GitOpError):                            # réf inconnue → lève (appelant → 404)
        git.list_paths(sot, "nexiste-pas")


def test_read_blob_text(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    blob = git.read_blob(sot, "dev", "README.md")
    assert blob == {"path": "README.md", "ref": "dev", "size": len("# projet\nligne 2\n"),
                    "binary": False, "truncated": False, "too_large": False,
                    "content": "# projet\nligne 2\n"}


def test_read_blob_binary_emits_no_bytes(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    blob = git.read_blob(sot, "dev", "data.bin")
    assert blob["binary"] is True and blob["content"] == "" and blob["size"] == 11


def test_read_blob_on_dir_raises(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    with pytest.raises(GitOpError):        # src est un arbre, pas un fichier
        git.read_blob(sot, "dev", "src")


def test_read_blob_display_truncation(tmp_path: Path, monkeypatch):
    from cockpit.git import internal
    monkeypatch.setattr(internal, "_MAX_BLOB_DISPLAY", 5)
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    blob = git.read_blob(sot, "dev", "README.md")
    assert blob["truncated"] is True and blob["content"] == "# pro" and blob["too_large"] is False


def test_read_blob_too_large_reads_nothing(tmp_path: Path, monkeypatch):
    from cockpit.git import internal
    monkeypatch.setattr(internal, "_MAX_BLOB_READ", 4)   # README fait 17 octets > 4
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    blob = git.read_blob(sot, "dev", "README.md")
    assert blob["too_large"] is True and blob["content"] == "" and blob["truncated"] is True


def test_read_blob_raw_serves_binary_and_text_bytes_verbatim(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    # là où read_blob blanchit un binaire (content=""), read_blob_raw sert les octets exacts
    assert git.read_blob_raw(sot, "dev", "data.bin") == b"\x00\x01\x02binaire\x00"
    assert git.read_blob_raw(sot, "dev", "README.md") == b"# projet\nligne 2\n"


def test_read_blob_raw_bad_ref_or_dir_raises(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    with pytest.raises(GitOpError):
        git.read_blob_raw(sot, "nexiste-pas", "README.md")
    with pytest.raises(GitOpError):        # src est un arbre, pas un fichier
        git.read_blob_raw(sot, "dev", "src")


def test_read_blob_raw_too_large_raises_signaled(tmp_path: Path, monkeypatch):
    from cockpit.git import internal
    monkeypatch.setattr(internal, "_MAX_BLOB_READ", 4)   # README fait 17 octets > 4
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    # refus SIGNALÉ (jamais un flux tronqué) ; BlobTooLargeError EST un GitOpError
    with pytest.raises(BlobTooLargeError):
        git.read_blob_raw(sot, "dev", "README.md")
    assert issubclass(BlobTooLargeError, GitOpError)


# -- intelligence git : détail de commit + historique fichier (commit_detail / file_history) --------

def test_commit_detail_metadata_and_touched_files(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)   # 1 commit racine : README.md(2), src/app.py(1), data.bin(binaire)
    head = git.feature_sha(sot, "dev")
    detail = git.commit_detail(sot, head)
    assert detail["sha"] == head and detail["subject"] == "rich seed"
    assert detail["author"] == "Test" and detail["email"] == "test@example.invalid"
    assert detail["date"] and detail["short"] and head.startswith(detail["short"])
    by_path = {f["path"]: f for f in detail["files"]}
    assert by_path["README.md"]["additions"] == 2 and by_path["README.md"]["binary"] is False
    assert by_path["src/app.py"]["additions"] == 1
    # un binaire → additions/deletions None + drapeau binary (numstat émet `-`)
    assert by_path["data.bin"]["binary"] is True and by_path["data.bin"]["additions"] is None


def test_commit_detail_bad_sha_raises(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    with pytest.raises(GitOpError):
        git.commit_detail(sot, "deadbeefdeadbeef")


def test_file_history_tracks_a_path_across_commits(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    # un 2e commit qui ne touche QUE README.md → l'historique de README a 2 entrées, celui de app.py 1
    wt = tmp_path / "wt"
    git.add_worktree(sot, wt, branch="feature/x", base="dev")
    (wt / "README.md").write_text("# projet\nligne 2\nligne 3\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "doc: 3e ligne", cwd=wt)
    git.merge_ff(sot, into="dev", source="feature/x")

    hist_readme = git.file_history(sot, "dev", "README.md")
    assert [c["subject"] for c in hist_readme] == ["doc: 3e ligne", "rich seed"]
    assert all(c["sha"] and c["short"] and c["author"] == "Test" and c["date"] for c in hist_readme)
    hist_app = git.file_history(sot, "dev", "src/app.py")
    assert [c["subject"] for c in hist_app] == ["rich seed"]
    # un fichier inconnu à cette réf → liste vide (pas une erreur : réponse valide)
    assert git.file_history(sot, "dev", "n-existe-pas.txt") == []


def test_entry_last_commits_maps_each_entry_to_its_last_touching_commit(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    # un 2e commit qui ne touche QUE README.md → son dernier commit diffère de celui de src/data.bin
    wt = tmp_path / "wt"
    git.add_worktree(sot, wt, branch="feature/x", base="dev")
    (wt / "README.md").write_text("# projet\nligne 2\nligne 3\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "doc: 3e ligne", cwd=wt)
    git.merge_ff(sot, into="dev", source="feature/x")

    root = git.ls_tree(sot, "dev")
    last = git.entry_last_commits(sot, "dev", "", [e["name"] for e in root])
    assert last["README.md"]["subject"] == "doc: 3e ligne"   # touché par le 2e commit
    assert last["src"]["subject"] == "rich seed"             # sous-dossier figé au seed
    assert last["data.bin"]["subject"] == "rich seed"
    assert all(v["short"] and v["date"] for v in last.values())
    # scopé à un sous-dossier : le dernier commit de app.py
    sub = git.entry_last_commits(sot, "dev", "src", ["app.py"])
    assert sub["app.py"]["subject"] == "rich seed"
    # aucun nom → dict vide, aucun appel git (pas d'erreur même sans entrée)
    assert git.entry_last_commits(sot, "dev", "", []) == {}


def test_latest_commit_head_and_count_scoped_to_path(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    wt = tmp_path / "wt"
    git.add_worktree(sot, wt, branch="feature/x", base="dev")
    (wt / "README.md").write_text("# projet\nligne 2\nligne 3\n", encoding="utf-8")
    _run("add", "-A", cwd=wt)
    _run("commit", "-q", "-m", "doc: 3e ligne", cwd=wt)
    git.merge_ff(sot, into="dev", source="feature/x")

    head = git.feature_sha(sot, "dev")
    latest = git.latest_commit(sot, "dev")
    assert latest is not None
    assert latest["subject"] == "doc: 3e ligne" and head.startswith(latest["short"])
    assert latest["author"] == "Test" and latest["date"] and latest["count"] == 2
    # scopé à src/ : dernier commit = le seed, 1 seul commit y touche
    scoped = git.latest_commit(sot, "dev", "src")
    assert scoped is not None and scoped["subject"] == "rich seed" and scoped["count"] == 1
    # un chemin sans historique → None (réponse valide, pas d'erreur)
    assert git.latest_commit(sot, "dev", "n-existe-pas") is None


def test_latest_commit_bad_ref_raises(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    with pytest.raises(GitOpError):
        git.latest_commit(sot, "nexiste-pas")


def test_file_history_bad_ref_raises(tmp_path: Path):
    git = InternalGit()
    sot = _seed_bare_rich(tmp_path)
    with pytest.raises(GitOpError):
        git.file_history(sot, "nexiste-pas", "README.md")


# -- adoption : clone_sot (bare clone d'un upstream + normalisation dev/main) -----------------------

def _seed_master_only(tmp: Path) -> Path:
    """Un repo git normal avec pour seule branche `master` (upstream d'adoption mal formé pour la forge)."""
    seed = tmp / "seed-master"
    seed.mkdir()
    _run("init", "-q", "-b", "master", cwd=seed)
    (seed / "f.txt").write_text("x\n", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "c", cwd=seed)
    return seed


def test_clone_sot_adopts_bare_with_real_content(tmp_path: Path):
    git = InternalGit()
    upstream = _seed_bare(tmp_path)          # bare avec main+dev, contenu readme.txt
    dest = tmp_path / "adopted.git"
    git.clone_sot(dest, str(upstream))
    assert run.run(["git", "-C", str(dest), "rev-parse", "--is-bare-repository"]).stdout.strip() == "true"
    assert {"dev", "main"} <= {b["name"] for b in git.branches(dest)}
    assert run.run(["git", "-C", str(dest), "show", "dev:readme.txt"]).stdout == "seed\n"


def test_clone_sot_normalizes_master_only_to_dev_main(tmp_path: Path):
    git = InternalGit()
    upstream = _seed_master_only(tmp_path)   # ni dev ni main en amont
    dest = tmp_path / "norm.git"
    git.clone_sot(dest, str(upstream))
    names = {b["name"] for b in git.branches(dest)}
    assert {"dev", "main"} <= names          # synthétisées depuis master
    assert run.run(["git", "-C", str(dest), "show", "dev:f.txt"]).stdout == "x\n"


def test_clone_sot_with_creds_env_does_not_break_clone(tmp_path: Path):
    """Un `creds_env` composé par `credential_env` (inject `GIT_CONFIG_* url.insteadOf`) ne casse pas un clone
    local sans auth → prouve que l'auth optionnelle est un pass-through inoffensif quand elle est inutile."""
    git = InternalGit()
    upstream = _seed_bare(tmp_path)
    env = credential_env("x-token", base={"PATH": os.environ.get("PATH", "")})
    dest = tmp_path / "authed.git"
    git.clone_sot(dest, str(upstream), creds_env=env)
    assert run.run(["git", "-C", str(dest), "show", "dev:readme.txt"]).stdout == "seed\n"


def test_clone_sot_rejects_nonempty_dest_and_bad_url(tmp_path: Path):
    git = InternalGit()
    upstream = _seed_bare(tmp_path)
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("occupied\n", encoding="utf-8")
    with pytest.raises(GitOpError):                       # destination non-vide
        git.clone_sot(full, str(upstream))
    with pytest.raises(GitOpError):                       # URL inexistante
        git.clone_sot(tmp_path / "nope.git", str(tmp_path / "does-not-exist"))
