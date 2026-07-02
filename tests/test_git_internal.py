"""Tests de git/internal — primitives sur un SoT bare LOCAL réel (worktree, ff merge) + parsers purs +
injection d'identité writeback. Git est requis (présent en CI/dev)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cockpit.core import run
from cockpit.git.internal import (
    GitOpError,
    InternalGit,
    classify_push_error,
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
    git.init_sot(sot)  # 2e appel : no-op, ne lève pas


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


def test_writeback_env_injects_identity_without_mutating_os_environ():
    before = dict(os.environ)
    env = writeback_env(("Vault Writeback", "wb@example.invalid"))
    assert env["GIT_AUTHOR_NAME"] == "Vault Writeback"
    assert env["GIT_AUTHOR_EMAIL"] == "wb@example.invalid"
    assert env["GIT_COMMITTER_NAME"] == "Vault Writeback"
    assert "GIT_AUTHOR_NAME" not in os.environ  # non persisté (spec merge-writeback)
    assert dict(os.environ) == before


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
