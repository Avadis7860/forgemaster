"""Tests du domaine codemap — matérialisation SHA-cachée (`git archive`) + requêtes flow (boîte-noire CLI).

Deux niveaux : **unitaire** avec runner injecté (logique de cache/erreur, déterministe, sans code-map) et
**intégration** avec le VRAI `codemap` (installé dans le venv) sur un SoT python seedé (contrat de bout en
bout : opérations découvertes + flot résolu). Plus le câblage des routes via TestClient."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.codemap import flow as flow_svc
from cockpit.codemap.index import (
    _BUILT_MARKER,
    CodemapError,
    IndexHandle,
    codemap_argv,
    ensure_index,
    index_dir_for,
)
from cockpit.config import Settings
from cockpit.core.run import RunResult, run
from cockpit.daemon import app as app_mod
from cockpit.git.internal import GitOpError, InternalGit, writeback_env

_ENV = writeback_env(("Test", "test@example.invalid"), base={"PATH": os.environ.get("PATH", "")})

# fixture : un verbe CLI `run` dont le flot appelle helper→compute (arêtes résolues, ordre observable).
_APP_PY = '''\
"""fixture app : un verbe CLI (table de dispatch) dont le flot appelle un helper local."""


def compute():
    return 1


def helper():
    return compute()


def _h_run():
    return helper()


HANDLERS = {"run": _h_run}
'''


def _run(*args: str, cwd: Path) -> None:
    r = run(["git", *args], cwd=cwd, env=_ENV)
    assert r.ok, r.stderr


def _seed_py_bare(tmp: Path) -> Path:
    """SoT bare seedé (`dev`) avec un petit projet python (1 verbe CLI `run`)."""
    seed = tmp / "seed"
    seed.mkdir()
    _run("init", "-q", "-b", "dev", cwd=seed)
    (seed / "app.py").write_text(_APP_PY, encoding="utf-8")
    _run("add", "-A", cwd=seed)
    _run("commit", "-q", "-m", "seed py", cwd=seed)
    sot = tmp / "sot"
    r = run(["git", "clone", "--bare", "-q", str(seed), str(sot)], env=_ENV)
    assert r.ok, r.stderr
    return sot


def _settings(tmp: Path) -> Settings:
    return Settings.resolve(home=tmp / "home", projects_root=tmp / "projects")


class _FakeRunner:
    """Runner injecté : répond `--schema-version`, simule `codemap build` (rc), et relaie une sortie JSON
    canned pour les requêtes `flow`. Enregistre les argv vus (assertions de cache). `schema` permet de
    simuler un upgrade de contrat de code-map (deux runners = deux versions)."""

    def __init__(self, *, build_ok: bool = True, flow_stdout: str = "{}", schema: str = "1.3.0") -> None:
        self.calls: list[list[str]] = []
        self.build_ok = build_ok
        self.flow_stdout = flow_stdout
        self.schema = schema

    def __call__(self, argv, *, cwd=None, timeout=None, **_kw) -> RunResult:  # noqa: ANN001
        self.calls.append(list(argv))
        if "--schema-version" in argv:
            return RunResult(argv=list(argv), returncode=0, stdout=self.schema + "\n", stderr="")
        if "build" in argv:
            if self.build_ok and cwd is not None:
                # simule l'écriture de l'index par code-map (nom interne) — le cockpit ne le lit PLUS pour
                # décider du cache-hit (il écrit son propre marqueur), mais un vrai build le produirait.
                marker = Path(cwd) / ".codemap" / "calls.manifest.json"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("{}", encoding="utf-8")
                return RunResult(argv=list(argv), returncode=0, stdout="", stderr="")
            return RunResult(argv=list(argv), returncode=1, stdout="", stderr="boom")
        return RunResult(argv=list(argv), returncode=0, stdout=self.flow_stdout, stderr="")

    @property
    def builds(self) -> int:
        return sum(1 for c in self.calls if "build" in c)


# -- git archive (matérialisation d'arbre complet, bare-safe) --------------------------------------

def test_archive_materializes_full_tree(tmp_path: Path):
    sot = _seed_py_bare(tmp_path)
    dest = tmp_path / "out"
    InternalGit().archive(sot, "dev", dest)
    assert (dest / "app.py").read_text(encoding="utf-8") == _APP_PY


def test_archive_bad_ref_raises(tmp_path: Path):
    sot = _seed_py_bare(tmp_path)
    with pytest.raises(GitOpError):
        InternalGit().archive(sot, "nope", tmp_path / "out2")


# -- codemap_argv : jamais un lookup PATH nu -------------------------------------------------------

def test_codemap_argv_uses_current_python_not_bare_lookup():
    argv = codemap_argv("build", "--root", "/x")
    assert argv[1:] == ["-m", "codemap", "build", "--root", "/x"]
    assert argv[0].endswith("python") or "python" in Path(argv[0]).name  # sys.executable, pas "codemap"


# -- ensure_index : matérialise + build, puis cache par SHA ----------------------------------------

def test_ensure_index_materializes_builds_then_caches(tmp_path: Path):
    settings = _settings(tmp_path)
    sot = _seed_py_bare(tmp_path)
    fake = _FakeRunner()
    h = ensure_index(settings, "p", sot, runner=fake)
    assert isinstance(h, IndexHandle) and h.ref == "dev" and h.sha
    assert (h.root / "app.py").is_file()                       # arbre matérialisé
    assert (h.root / _BUILT_MARKER).is_file()                  # marqueur PROPRE au cockpit (découplé)
    assert index_dir_for(settings, "p", h.sha, "1.3.0") == h.root   # clé = (SHA, schema)
    assert fake.builds == 1
    # 2e appel, même SHA + même schema → cache hit : ni re-build ni re-matérialisation
    h2 = ensure_index(settings, "p", sot, runner=fake)
    assert h2.root == h.root and fake.builds == 1


def test_index_cache_key_includes_schema_version(tmp_path: Path):
    """Trou porté fermé : un upgrade de code-map (schema_version différent) ouvre un dossier de cache neuf →
    rebuild automatique, l'ancien index n'est jamais servi périmé (plus de vidage manuel post-déploiement)."""
    settings, sot = _settings(tmp_path), _seed_py_bare(tmp_path)
    old, new = _FakeRunner(schema="1.2.0"), _FakeRunner(schema="1.3.0")  # gardés vivants → ids distincts
    h_old = ensure_index(settings, "p", sot, runner=old)
    h_new = ensure_index(settings, "p", sot, runner=new)
    assert h_old.root != h_new.root                            # schema dans la clé → dossiers distincts
    assert h_old.root.name == "1.2.0" and h_new.root.name == "1.3.0"
    assert old.builds == 1 and new.builds == 1                 # rebuild pour le nouveau schéma


def test_ensure_index_build_failure_raises(tmp_path: Path):
    sot = _seed_py_bare(tmp_path)
    with pytest.raises(CodemapError):
        ensure_index(_settings(tmp_path), "p", sot, runner=_FakeRunner(build_ok=False))


def test_ensure_index_bad_ref_raises(tmp_path: Path):
    sot = _seed_py_bare(tmp_path)
    with pytest.raises(CodemapError):
        ensure_index(_settings(tmp_path), "p", sot, ref="nope", runner=_FakeRunner())


# -- flow : relaie le contrat JSON de code-map (ok / ok:false / illisible) --------------------------

def test_list_operations_relays_cli_json(tmp_path: Path):
    sot = _seed_py_bare(tmp_path)
    canned = json.dumps({
        "operations": [{"operation": "cli:run", "entry": "app.py::_h_run", "kind": "cli"}],
        "engine": "calls-py-v1",
    })
    ops = flow_svc.list_operations(_settings(tmp_path), "p", sot, runner=_FakeRunner(flow_stdout=canned))
    assert ops["engine"] == "calls-py-v1"
    assert ops["operations"][0]["operation"] == "cli:run"


def test_flow_relays_ok_and_okfalse_verbatim(tmp_path: Path):
    settings, sot = _settings(tmp_path), _seed_py_bare(tmp_path)
    ok_graph = json.dumps({"ok": True, "operation": "cli:run", "entry": "app.py::_h_run",
                           "nodes": [], "edges": [], "stats": {"indirect_ratio": 0.0}})
    res = flow_svc.flow(settings, "p", sot, operation="cli:run", runner=_FakeRunner(flow_stdout=ok_graph))
    assert res["ok"] is True and res["operation"] == "cli:run"
    # ok:false (opération introuvable/ambiguë) relayé tel quel, pas une erreur
    nope = json.dumps({"ok": False, "reason": "entrée introuvable ou ambiguë"})
    res2 = flow_svc.flow(settings, "p", sot, operation="x", runner=_FakeRunner(flow_stdout=nope))
    assert res2["ok"] is False and "reason" in res2


def test_flow_illegible_output_raises(tmp_path: Path):
    sot = _seed_py_bare(tmp_path)
    with pytest.raises(CodemapError):
        flow_svc.flow(_settings(tmp_path), "p", sot, operation="x",
                      runner=_FakeRunner(flow_stdout="pas du json"))


# -- intégration : le VRAI codemap sur un SoT python seedé (contrat de bout en bout) ----------------

@pytest.mark.skipif(importlib.util.find_spec("codemap") is None,
                    reason="code-map non installé dans le venv (pip install -e ~/projects/code-map)")
def test_integration_real_codemap_discovers_and_flows(tmp_path: Path):
    settings, sot = _settings(tmp_path), _seed_py_bare(tmp_path)
    ops = flow_svc.list_operations(settings, "p", sot)          # runner réel (sys.executable -m codemap)
    assert "cli:run" in [o["operation"] for o in ops["operations"]]
    res = flow_svc.flow(settings, "p", sot, operation="cli:run")
    assert res["ok"] is True
    # le flot RÉEL : _h_run → helper → compute (arêtes résolues, ≥ 2 nœuds)
    tos = {e["to"] for e in res["edges"] if e["to"]}
    assert any(t.endswith("::helper") for t in tos)
    assert res["stats"]["nodes"] >= 2
    # 2e appel = cache hit (le dossier du SHA existe déjà) → réponse identique
    assert flow_svc.flow(settings, "p", sot, operation="cli:run")["stats"]["nodes"] == res["stats"]["nodes"]


# -- câblage des routes (TestClient) ---------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    return TestClient(app_mod.build_app(settings)), settings


def test_flow_operations_route_wiring_and_404(client):
    c, _ = client
    assert c.post("/api/projects", json={"slug": "proj"}).status_code == 201
    r = c.get("/api/projects/proj/flow/operations")
    assert r.status_code == 200
    body = r.json()
    assert "operations" in body and "engine" in body
    assert c.get("/api/projects/nope/flow/operations").status_code == 404   # KeyError → 404 global


def test_flow_route_okfalse_on_unknown_operation(client):
    c, _ = client
    c.post("/api/projects", json={"slug": "proj"})
    r = c.get("/api/projects/proj/flow", params={"operation": "cli:does-not-exist"})
    assert r.status_code == 200 and r.json()["ok"] is False
