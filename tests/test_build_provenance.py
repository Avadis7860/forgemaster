"""Tests de build_provenance — le signal honnête de fraîcheur du build. Fonctions pures (`staleness`,
`read_stamp`) sans I/O + `provenance`/`stale_type_hint` contre un SoT bare LOCAL réel (transport local).
Git requis (présent en CI/dev)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from cockpit import __version__
from cockpit import build_provenance as bp
from cockpit.core import run
from cockpit.git.internal import writeback_env

_ENV = writeback_env(("Test", "test@example.invalid"), base={"PATH": os.environ.get("PATH", "")})
_TYPES = "src/cockpit/provision/bundles/types"


def _run(*args: str, cwd: Path) -> None:
    r = run.run(["git", *args], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr


def _rev(ref: str, cwd: Path) -> str:
    r = run.run(["git", "rev-parse", ref], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr
    return r.stdout.strip()


def _seed_mirror(tmp: Path) -> tuple[Path, str, str]:
    """Bare avec 2 commits : commit1 pose le type `cli-tool`, commit2 ajoute `site-vitrine`. Retourne
    `(sot_bare, sha_commit1, sha_head)`. Simule un cockpit bâti au commit1 (sans site-vitrine) alors que le
    miroir a avancé au commit2 (avec)."""
    seed = tmp / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "main", cwd=seed)
    (seed / _TYPES / "cli-tool").mkdir(parents=True)
    (seed / _TYPES / "cli-tool" / ".cockpit").write_text("", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "commit1 cli-tool", cwd=seed)
    sha1 = _rev("HEAD", cwd=seed)
    (seed / _TYPES / "site-vitrine").mkdir(parents=True)
    (seed / _TYPES / "site-vitrine" / ".cockpit").write_text("", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "commit2 site-vitrine", cwd=seed)
    head = _rev("HEAD", cwd=seed)
    sot = tmp / "cockpit" / "sot.git"
    sot.parent.mkdir(parents=True)
    r = run.run(["git", "clone", "--bare", "-q", str(seed), str(sot)], env=_ENV)
    assert r.ok, r.stderr
    return sot, sha1, head


def _stamp_file(tmp: Path, sha: str | None) -> Path:
    p = tmp / "_build.json"
    p.write_text(json.dumps({"sha": sha, "committed_at": "2026-07-30T00:00:00+00:00"}), encoding="utf-8")
    return p


# -- fonctions pures --------------------------------------------------------------------------------

def test_staleness_incomparable_when_sha_or_head_missing():
    assert bp.staleness(None, "abc")["comparable"] is False
    assert bp.staleness("abc", None)["comparable"] is False
    assert bp.staleness(None, None)["stale"] is None            # on ne prétend rien


def test_staleness_fresh_when_equal():
    v = bp.staleness("a1b2", "a1b2")
    assert v == {"comparable": True, "stale": False, "behind_by": 0, "missing_types": []}


def test_staleness_stale_names_missing_types():
    v = bp.staleness("old", "new", behind_by=3,
                     installed_types=("generic", "cli-tool"),
                     remote_types=("generic", "cli-tool", "site-vitrine"))
    assert v["stale"] is True
    assert v["behind_by"] == 3
    assert v["missing_types"] == ["site-vitrine"]


def test_staleness_no_missing_claim_without_remote_types():
    # HEAD lisible mais remote_types illisible (build hors-miroir) → stale déduit, missing NON inventé.
    v = bp.staleness("old", "new", behind_by=None, installed_types=("cli-tool",), remote_types=None)
    assert v["stale"] is True and v["missing_types"] == [] and v["behind_by"] is None


def test_read_stamp_absent_is_honest_not_raising(tmp_path: Path):
    assert bp.read_stamp(tmp_path / "nope.json") == {"sha": None, "committed_at": None}


def test_read_stamp_present(tmp_path: Path):
    p = _stamp_file(tmp_path, "deadbeef")
    assert bp.read_stamp(p)["sha"] == "deadbeef"


# -- provenance live contre un miroir bare réel -----------------------------------------------------

def test_provenance_stale_against_advanced_mirror(tmp_path: Path):
    sot, sha1, head = _seed_mirror(tmp_path)
    stamp = _stamp_file(tmp_path, sha1)                          # cockpit bâti au commit1 (sans site-vitrine)
    p = bp.provenance(None, installed_types=("generic", "cli-tool"),
                      stamp=stamp, mirror_git_dir=sot)
    assert p["version"] == __version__ and p["sha"] == sha1
    assert p["comparable"] is True and p["stale"] is True
    assert p["behind_by"] == 1
    assert p["missing_types"] == ["site-vitrine"]


def test_provenance_fresh_when_built_at_head(tmp_path: Path):
    sot, _sha1, head = _seed_mirror(tmp_path)
    stamp = _stamp_file(tmp_path, head)
    p = bp.provenance(None, installed_types=("generic", "cli-tool", "site-vitrine"),
                      stamp=stamp, mirror_git_dir=sot)
    assert p["stale"] is False and p["behind_by"] == 0 and p["missing_types"] == []


def test_provenance_incomparable_without_mirror(tmp_path: Path):
    stamp = _stamp_file(tmp_path, "abc123")
    p = bp.provenance(None, installed_types=(), stamp=stamp, mirror_git_dir=tmp_path / "absent" / "sot.git")
    assert p["comparable"] is False and p["sha"] == "abc123"     # provenance seule, aucun faux-vert


def test_provenance_never_raises_on_broken_mirror(tmp_path: Path):
    broken = tmp_path / "broken" / "sot.git"                     # existe mais n'est pas un dépôt git
    broken.mkdir(parents=True)
    stamp = _stamp_file(tmp_path, "abc123")
    p = bp.provenance(None, installed_types=(), stamp=stamp, mirror_git_dir=broken)
    assert p["comparable"] is False                             # dégrade honnête, ne lève pas


# -- hint de préflight ------------------------------------------------------------------------------

def test_stale_type_hint_fires_on_missing_type():
    prov = {"stale": True, "behind_by": 1, "sha": "abcdef1234567890", "missing_types": ["site-vitrine"]}
    hint = bp.stale_type_hint(None, "site-vitrine", prov=prov)
    assert hint and "site-vitrine" in hint and "1 commit" in hint and "réinjecte" in hint.lower()


def test_stale_type_hint_silent_when_type_present_or_fresh():
    fresh = {"stale": False, "missing_types": []}
    assert bp.stale_type_hint(None, "cli-tool", prov=fresh) is None
    stale_other = {"stale": True, "behind_by": 1, "sha": "x", "missing_types": ["site-vitrine"]}
    assert bp.stale_type_hint(None, "cli-tool", prov=stale_other) is None   # type connu local → pas de hint
