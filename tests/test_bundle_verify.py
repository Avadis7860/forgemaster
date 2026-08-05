"""Tests de `provision.verify` — la preuve d'installabilité d'un bundle template.

Ce que ces tests gardent : **aucun chemin ne rend vert pour une autre raison que « ça s'installe et le gate
passe »**. Une installation ratée, un npm absent, un bundle sans manifeste : trois non-verts distincts, et
aucun ne doit se confondre avec un succès. Purs — le runner et le résolveur de `npm` sont injectés, donc ni
réseau ni npm réel.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forgemaster.core.run import RunResult
from forgemaster.provision import verify as bverify

NPM = "/usr/bin/npm"


def _res(argv, rc: int = 0, err: str = "") -> RunResult:
    return RunResult(argv=list(argv), returncode=rc, stdout="", stderr=err)


class FakeRunner:
    """Runner injecté : enregistre les appels, et rend rouge ceux dont l'argv matche `fail_on`."""

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, argv, *, cwd=None, timeout=None, **_kw) -> RunResult:
        self.calls.append(list(argv))
        rc = 1 if any(tok in argv for tok in self.fail_on) else 0
        return _res(argv, rc, err="échec simulé" if rc else "")


def _seed(tmp_path: Path, files: dict[str, str], monkeypatch) -> None:
    """Fait semer `files` par `verify_bundle` (patch de `load_bundle`) dans un dossier jetable qu'il crée
    lui-même — on teste le vrai chemin de matérialisation, pas un dossier préfabriqué."""
    monkeypatch.setattr(bverify, "load_bundle", lambda _t: files)


_GATED = '{"name": "x", "scripts": {"gate": "echo ok"}}'


# -- 1. npm absent : la preuve n'est pas montable, et RIEN n'est lancé -------------------------------

def test_npm_absent_est_non_montable_et_ne_lance_rien(monkeypatch):
    _seed(Path("."), {"package.json": _GATED}, monkeypatch)
    runner = FakeRunner()
    verdict = bverify.verify_bundle("x", runner=runner, which=lambda _n: None)
    assert verdict.state == bverify.UNMOUNTABLE
    assert verdict.exit_code == 2, "npm absent ne doit JAMAIS rendre 0"
    assert runner.calls == [], "rien ne s'exécute quand la preuve n'est pas montable"
    assert "npm" in verdict.reason, "un état non-vert porte toujours son motif"


# -- 2. LE test : une installation ratée n'ouvre pas la porte au gate --------------------------------

def test_install_rouge_ne_lance_pas_le_gate_et_ne_rend_pas_vert(monkeypatch):
    _seed(Path("."), {"package.json": _GATED, "package-lock.json": "{}"}, monkeypatch)
    runner = FakeRunner(fail_on=("ci",))
    verdict = bverify.verify_bundle("x", runner=runner, which=lambda _n: NPM)
    assert verdict.state == bverify.RED
    assert verdict.exit_code == 1
    assert [c[1:] for c in runner.calls] == [["ci"]], \
        "le gate ne doit pas tourner sur un node_modules absent — son rouge serait inattribuable"
    assert verdict.units[0].gate_ok is None, "`non lancé` n'est pas `échoué`"
    assert verdict.units[0].detail, "un rouge doit porter la queue de sa sortie"


# -- 3. un gate rouge est rouge (et l'install, elle, a bien tourné) ----------------------------------

def test_gate_rouge_rend_rouge(monkeypatch):
    _seed(Path("."), {"package.json": _GATED, "package-lock.json": "{}"}, monkeypatch)
    runner = FakeRunner(fail_on=("gate",))
    verdict = bverify.verify_bundle("x", runner=runner, which=lambda _n: NPM)
    assert verdict.state == bverify.RED
    assert verdict.units[0].install_ok is True and verdict.units[0].gate_ok is False


# -- 4. tout vert -----------------------------------------------------------------------------------

def test_install_et_gate_verts_rendent_vert(monkeypatch):
    _seed(Path("."), {"package.json": _GATED, "package-lock.json": "{}"}, monkeypatch)
    runner = FakeRunner()
    verdict = bverify.verify_bundle("x", runner=runner, which=lambda _n: NPM)
    assert verdict.state == bverify.GREEN and verdict.exit_code == 0
    assert [c[1:] for c in runner.calls] == [["ci"], ["run", "gate"]]


# -- 5. le repli `npm install` est DIT, jamais silencieux --------------------------------------------

def test_sans_lockfile_on_replie_sur_install_et_le_verdict_le_dit(monkeypatch):
    _seed(Path("."), {"package.json": _GATED}, monkeypatch)          # pas de package-lock.json
    runner = FakeRunner()
    verdict = bverify.verify_bundle("x", runner=runner, which=lambda _n: NPM)
    assert verdict.units[0].locked is False, \
        "sans verrou, la preuve est plus faible (plages résolues à l'install) — elle doit se lire ainsi"
    assert verdict.units[0].install_argv[1:] == ("install",)
    assert verdict.state == bverify.GREEN                              # verte, mais annoncée sans verrou


# -- 6. aucun manifeste : `sans unité`, surtout pas vert ---------------------------------------------

def test_bundle_sans_unite_npm_nest_pas_vert(monkeypatch):
    _seed(Path("."), {"README.md": "prose", "package.json": '{"name": "x"}'}, monkeypatch)
    runner = FakeRunner()
    verdict = bverify.verify_bundle("x", runner=runner, which=lambda _n: NPM)
    assert verdict.state == bverify.NO_UNIT, "un package.json SANS script `gate` n'est pas une unité"
    assert verdict.exit_code == 2 and not verdict.ok
    assert runner.calls == []


# -- 7. le semis jetable est démonté, même quand ça explose ------------------------------------------

def test_le_semis_jetable_est_demonte_meme_sur_exception(monkeypatch):
    _seed(Path("."), {"package.json": _GATED}, monkeypatch)
    seen: list[Path] = []
    real_mkdtemp = bverify.mkdtemp

    def spy(**kwargs):
        path = real_mkdtemp(**kwargs)
        seen.append(Path(path))
        return path

    monkeypatch.setattr(bverify, "mkdtemp", spy)

    def boom(*_a, **_kw):
        raise RuntimeError("npm a explosé")

    with pytest.raises(RuntimeError):
        bverify.verify_bundle("x", runner=boom, which=lambda _n: NPM)
    assert seen and not seen[0].exists(), "un semis jetable ne survit pas à une exception"


def test_keep_conserve_le_semis_et_le_dit(monkeypatch):
    _seed(Path("."), {"package.json": _GATED}, monkeypatch)
    verdict = bverify.verify_bundle("x", runner=FakeRunner(), which=lambda _n: NPM, keep=True)
    workdir = Path(verdict.workdir)
    assert workdir.is_dir(), "`--keep` sert à inspecter un rouge : le semis doit être là"
    assert (workdir / "package.json").is_file()


# -- 8. les unités des types RÉELS viennent du vrai semis, pas d'une liste écrite à la main -----------

@pytest.mark.parametrize(("project_type", "expected"), [
    ("browser-game", ["."]),
    ("front-ts", ["."]),
    ("site-vitrine", ["web"]),
])
def test_unites_npm_des_types_reels(tmp_path, project_type, expected):
    """Ancre la découverte sur les bundles vendorés : si un template déplace son manifeste ou perd son
    script `gate`, ce test tombe — au lieu que la preuve devienne silencieusement vide."""
    root = bverify.materialize(project_type, tmp_path / project_type)
    assert sorted(str(u.relative_to(root)) for u in bverify.npm_units(root)) == expected


def test_materialize_ecrit_le_semis_verbatim(tmp_path):
    """`load_bundle` est écrit tel quel par `create_project` — la preuve doit porter sur CE contenu."""
    from forgemaster.provision import load_bundle
    root = bverify.materialize("front-ts", tmp_path / "seed")
    payload = load_bundle("front-ts")
    assert (root / "package.json").read_text(encoding="utf-8") == payload["package.json"]
