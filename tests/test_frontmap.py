"""Tests du domaine frontmap — matérialisation SHA-cachée (`git archive`) + requêtes catalogue (boîte-noire
CLI front-map).

Deux niveaux : **unitaire** avec runner injecté (logique de cache/erreur, déterministe, sans front-map) et
**intégration** avec le VRAI `frontmap` (installé dans le venv) sur un SoT front seedé (contrat de bout en
bout : tokens extraits du CSS, stdlib-pur, sans tree-sitter). Plus le câblage des routes via TestClient.

Jumeau de `test_codemap`, adapté aux écarts front-map : négociation via `--version` (pas `--schema-version`)
et sortie déjà-JSON (pas de `--format`)."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.config import Settings
from cockpit.core.run import RunResult, run
from cockpit.daemon import app as app_mod
from cockpit.frontmap import catalog as catalog_svc
from cockpit.frontmap.index import (
    _BUILT_MARKER,
    FrontmapError,
    IndexHandle,
    ensure_index,
    frontmap_argv,
    index_dir_for,
)
from cockpit.git.internal import writeback_env

_ENV = writeback_env(("Test", "test@example.invalid"), base={"PATH": os.environ.get("PATH", "")})

# fixture : un front minimal — 3 design tokens dans un CSS `@theme` (extraction stdlib, sans tree-sitter).
_INDEX_CSS = """\
@theme {
  --color-accent-500: #3b82f6;
  --color-danger-500: #ef4444;
  --font-sans: ui-sans-serif, system-ui;
}
"""


def _run(*args: str, cwd: Path) -> None:
    r = run(["git", *args], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr


def _seed_front_bare(tmp: Path) -> Path:
    """SoT bare seedé (`dev`) avec un front minimal (`web/src/index.css`, 3 tokens)."""
    seed = tmp / "seed"
    (seed / "web" / "src").mkdir(parents=True)
    _run("init", "-q", "-b", "dev", cwd=seed)
    (seed / "web" / "src" / "index.css").write_text(_INDEX_CSS, encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "seed front", cwd=seed)
    sot = tmp / "sot"
    r = run(["git", "clone", "--bare", "-q", str(seed), str(sot)], env=_ENV)
    assert r.ok, r.stderr
    return sot


def _settings(tmp: Path) -> Settings:
    return Settings.resolve(home=tmp / "home", projects_root=tmp / "projects")


class _FakeRunner:
    """Runner injecté : répond `--version`, simule `frontmap build` (rc), et relaie une sortie JSON canned
    pour les requêtes catalogue. Enregistre les argv vus (assertions de cache). `version` permet de simuler un
    upgrade de front-map (deux runners = deux versions)."""

    def __init__(self, *, build_ok: bool = True, out: str = "{}", version: str = "frontmap 0.1.0") -> None:
        self.calls: list[list[str]] = []
        self.build_ok = build_ok
        self.out = out
        self.version = version

    def __call__(self, argv, *, cwd=None, timeout=None, **_kw) -> RunResult:  # noqa: ANN001
        self.calls.append(list(argv))
        if "--version" in argv:
            return RunResult(argv=list(argv), returncode=0, stdout=self.version + "\n", stderr="")
        if "build" in argv:
            if self.build_ok:
                return RunResult(argv=list(argv), returncode=0, stdout='{"skipped": false}', stderr="")
            return RunResult(argv=list(argv), returncode=1, stdout="", stderr="boom")
        return RunResult(argv=list(argv), returncode=0, stdout=self.out, stderr="")

    @property
    def builds(self) -> int:
        return sum(1 for c in self.calls if "build" in c)


# -- frontmap_argv : jamais un lookup PATH nu (exige le __main__ de front-map) ----------------------

def test_frontmap_argv_uses_current_python_not_bare_lookup():
    argv = frontmap_argv("build", "--root", "/x")
    assert argv[1:] == ["-m", "frontmap", "build", "--root", "/x"]
    assert argv[0].endswith("python") or "python" in Path(argv[0]).name  # sys.executable, pas "frontmap"


# -- ensure_index : matérialise + build, puis cache par (SHA, version) ------------------------------

def test_ensure_index_materializes_builds_then_caches(tmp_path: Path):
    settings = _settings(tmp_path)
    sot = _seed_front_bare(tmp_path)
    fake = _FakeRunner()
    h = ensure_index(settings, "p", sot, runner=fake)
    assert isinstance(h, IndexHandle) and h.ref == "dev" and h.sha
    assert (h.root / "web" / "src" / "index.css").is_file()    # arbre matérialisé
    assert (h.root / _BUILT_MARKER).is_file()                  # marqueur PROPRE au cockpit (découplé)
    assert index_dir_for(settings, "p", h.sha, "frontmap-0.1.0") == h.root  # clé = (SHA, version normalisée)
    assert fake.builds == 1
    # 2e appel, même SHA + même version → cache hit : ni re-build ni re-matérialisation
    h2 = ensure_index(settings, "p", sot, runner=fake)
    assert h2.root == h.root and fake.builds == 1


def test_index_cache_key_includes_tool_version(tmp_path: Path):
    """Un upgrade de front-map (version différente) ouvre un dossier de cache neuf → rebuild automatique,
    l'ancien index n'est jamais servi périmé (parité avec le schema_version de code-map)."""
    settings, sot = _settings(tmp_path), _seed_front_bare(tmp_path)
    old = _FakeRunner(version="frontmap 0.1.0")               # gardés vivants → ids distincts
    new = _FakeRunner(version="frontmap 0.2.0")
    h_old = ensure_index(settings, "p", sot, runner=old)
    h_new = ensure_index(settings, "p", sot, runner=new)
    assert h_old.root != h_new.root                           # version dans la clé → dossiers distincts
    assert h_old.root.name == "frontmap-0.1.0" and h_new.root.name == "frontmap-0.2.0"
    assert old.builds == 1 and new.builds == 1                # rebuild pour la nouvelle version


def test_ensure_index_build_failure_raises(tmp_path: Path):
    sot = _seed_front_bare(tmp_path)
    with pytest.raises(FrontmapError):
        ensure_index(_settings(tmp_path), "p", sot, runner=_FakeRunner(build_ok=False))


def test_ensure_index_bad_ref_raises(tmp_path: Path):
    sot = _seed_front_bare(tmp_path)
    with pytest.raises(FrontmapError):
        ensure_index(_settings(tmp_path), "p", sot, ref="nope", runner=_FakeRunner())


# -- catalog : relaie le contrat JSON de front-map (ok / illisible) ---------------------------------

def test_catalog_verbs_relay_cli_json(tmp_path: Path):
    settings, sot = _settings(tmp_path), _seed_front_bare(tmp_path)
    canned = json.dumps({"tokens": [{"name": "--color-accent-500", "group": "accent"}],
                         "count": 1, "engine": "frontmap"})
    res = catalog_svc.tokens(settings, "p", sot, runner=_FakeRunner(out=canned))
    assert res["count"] == 1 and res["tokens"][0]["group"] == "accent"
    # primitives/routes passent par le même relais (verbe distinct dans l'argv)
    prim = json.dumps({"primitives": [], "count": 0, "engine": "frontmap"})
    assert catalog_svc.primitives(settings, "p", sot, runner=_FakeRunner(out=prim))["count"] == 0


def test_catalog_illegible_output_raises(tmp_path: Path):
    sot = _seed_front_bare(tmp_path)
    with pytest.raises(FrontmapError):
        catalog_svc.routes(_settings(tmp_path), "p", sot, runner=_FakeRunner(out="pas du json"))


# -- intégration : le VRAI frontmap sur un SoT front seedé (contrat de bout en bout) ----------------

@pytest.mark.skipif(importlib.util.find_spec("frontmap") is None,
                    reason="front-map non installé dans le venv (pip install -e ~/projects/front-map)")
def test_integration_real_frontmap_extracts_tokens(tmp_path: Path):
    settings, sot = _settings(tmp_path), _seed_front_bare(tmp_path)
    res = catalog_svc.tokens(settings, "p", sot)               # runner réel (sys.executable -m frontmap)
    names = {t["name"] for t in res["tokens"]}
    assert "--color-accent-500" in names and "--font-sans" in names
    assert {t["group"] for t in res["tokens"]} >= {"accent", "status"}   # dérivation de groupe réelle
    # 2e appel = cache hit (le dossier du SHA existe déjà) → réponse identique
    assert catalog_svc.tokens(settings, "p", sot)["count"] == res["count"]


# -- câblage des routes (TestClient) ---------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings)), settings


def test_frontmap_routes_wiring_and_404(client):
    c, _ = client
    assert c.post("/api/projects", json={"slug": "proj"}).status_code == 201
    for verb in ("tokens", "primitives", "routes"):
        r = c.get(f"/api/projects/proj/frontmap/{verb}")
        assert r.status_code == 200, r.text
        assert verb in r.json()                                 # la clé du verbe est présente
    assert c.get("/api/projects/nope/frontmap/tokens").status_code == 404   # KeyError → 404 global
