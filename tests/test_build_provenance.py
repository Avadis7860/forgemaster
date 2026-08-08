"""Tests de build_provenance — le signal honnête de fraîcheur du build. Fonctions pures (`staleness`,
`read_stamp`) sans I/O + `provenance`/`stale_type_hint` contre un SoT bare LOCAL réel (transport local).
Git requis (présent en CI/dev)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from forgemaster import __version__
from forgemaster import build_provenance as bp
from forgemaster.core import run
from forgemaster.git.internal import writeback_env

_ENV = writeback_env(("Test", "test@example.invalid"), base={"PATH": os.environ.get("PATH", "")})
_TYPES = "src/forgemaster/provision/bundles/types"


def _run(*args: str, cwd: Path) -> None:
    r = run.run(["git", *args], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr


def _rev(ref: str, cwd: Path) -> str:
    r = run.run(["git", "rev-parse", ref], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr
    return r.stdout.strip()


def _seed_mirror(tmp: Path) -> tuple[Path, str, str]:
    """Bare avec 2 commits : commit1 pose le type `cli-tool`, commit2 ajoute `site-vitrine`. Retourne
    `(sot_bare, sha_commit1, sha_head)`. Simule un forgemaster bâti au commit1 (sans site-vitrine) alors que
    le
    miroir a avancé au commit2 (avec)."""
    seed = tmp / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "main", cwd=seed)
    (seed / _TYPES / "cli-tool").mkdir(parents=True)
    (seed / _TYPES / "cli-tool" / ".forgemaster").write_text("", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "commit1 cli-tool", cwd=seed)
    sha1 = _rev("HEAD", cwd=seed)
    (seed / _TYPES / "site-vitrine").mkdir(parents=True)
    (seed / _TYPES / "site-vitrine" / ".forgemaster").write_text("", encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "commit2 site-vitrine", cwd=seed)
    head = _rev("HEAD", cwd=seed)
    sot = tmp / "forgemaster" / "sot.git"
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
    assert v == {"comparable": True, "stale": False, "behind_by": 0, "missing_types": [],
                 "head": "a1b2"}


def test_staleness_rend_le_head_MEME_quand_il_ne_peut_pas_comparer():
    # « Je connais la référence, mais pas mon propre build » est un état, et il vaut mieux que deux `null` :
    # sans ce champ, une surface ne peut pas DIRE contre quoi elle aurait comparé. Le verdict, lui, ne bouge
    # pas d'un iota — c'est le contre-témoin qui empêche ce champ de devenir un faux-vert par la bande.
    v = bp.staleness(None, "a1b2")
    assert v["head"] == "a1b2"
    assert v["comparable"] is False and v["stale"] is None
    assert bp.staleness("a1b2", None)["head"] is None            # rien lu → rien affirmé


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
    # forgemaster bâti au commit1 (sans site-vitrine)
    stamp = _stamp_file(tmp_path, sha1)
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
    # `None` dit « aucune référence sur ce disque », pas « je n'ai pas regardé » — c'est ce qui autorise la
    # surface à écrire « je ne peux pas savoir » plutôt qu'un tiret muet.
    assert p["reference"] is None and p["head"] is None


def test_provenance_NOMME_la_reference_contre_laquelle_elle_compare(tmp_path: Path):
    # Le miroir est LOCAL, donc lui-même vieillissant : un verdict qui ne nomme pas ce qu'il a comparé laisse
    # l'utilisateur sans moyen d'en juger la portée. Les DEUX moitiés — où, et à quel commit.
    sot, sha1, head = _seed_mirror(tmp_path)
    p = bp.provenance(None, installed_types=("generic", "cli-tool"),
                      stamp=_stamp_file(tmp_path, sha1), mirror_git_dir=sot)
    assert p["reference"] == str(sot)
    assert p["head"] == head


def test_provenance_never_raises_on_broken_mirror(tmp_path: Path):
    broken = tmp_path / "broken" / "sot.git"                     # existe mais n'est pas un dépôt git
    broken.mkdir(parents=True)
    stamp = _stamp_file(tmp_path, "abc123")
    p = bp.provenance(None, installed_types=(), stamp=stamp, mirror_git_dir=broken)
    assert p["comparable"] is False                             # dégrade honnête, ne lève pas
    # La référence EXISTE sur le disque et reste nommée — c'est justement ce qui rend le refus lisible :
    # « il y a bien un miroir là, je n'ai pas su le lire » ≠ « il n'y a pas de miroir ».
    assert p["reference"] == str(broken) and p["head"] is None


# -- cartes hôte servies (la 2e moitié de la provenance d'une instance) ------------------------------

def test_provenance_reports_the_served_maps(tmp_path: Path):
    """Une instance sait DIRE quelles cartes elle sert — ce que `/api/version` ne savait pas faire : il ne
    parlait que du wheel, et les 3 cartes vieillissaient sans un mot."""
    served = [{"name": "code-map", "sha": "775117a0", "requested_ref": "main",
               "source": "vcs", "reason": None}]
    p = bp.provenance(None, installed_types=(), stamp=_stamp_file(tmp_path, "abc123"),
                      mirror_git_dir=tmp_path / "absent", maps=served)
    assert p["maps"] == served


def test_provenance_keeps_the_two_halves_separate(tmp_path: Path):
    """Le wheel et les cartes bougent INDÉPENDAMMENT (réinjection vs `tools install`). Le SHA du wheel ne
    doit donc jamais se confondre avec celui d'une carte : deux champs, deux vies."""
    served = [{"name": "code-map", "sha": "775117a0", "requested_ref": "main",
               "source": "vcs", "reason": None}]
    p = bp.provenance(None, installed_types=(), stamp=_stamp_file(tmp_path, "abc123"),
                      mirror_git_dir=tmp_path / "absent", maps=served)
    assert p["sha"] == "abc123" and p["maps"][0]["sha"] == "775117a0"


def test_provenance_survives_an_unreadable_toolchain(tmp_path: Path):
    """Lecture des cartes impossible (pas de venv d'outils, settings inutilisable) → `maps` vide, jamais un
    500 sur la sonde. `/api/version` doit rester répondable même quand l'outillage n'est pas là."""
    p = bp.provenance(None, installed_types=(), stamp=_stamp_file(tmp_path, "abc123"),
                      mirror_git_dir=tmp_path / "absent")
    assert p["maps"] == [] and p["sha"] == "abc123"


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
