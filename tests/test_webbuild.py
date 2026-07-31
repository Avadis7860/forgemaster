"""Tests de `webbuild` — localisation de `web/`, fail-loud sans Node, mise à dispo de code-map (from-clone),
et décision d'embarquement du hook de packaging. On ne lance JAMAIS npm/pip en test (lent, réseau) : on
prouve le câblage et le message actionnable, pas le build/install lui-même (couvert par l'acceptance
venv-neuf / CT vierge)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cockpit import webbuild

# hatch_build.py vit à la RACINE du repo (hook de packaging, volontairement hors du package cockpit — il
# tourne au build quand `cockpit` n'est pas encore importable). On l'importe en ajoutant la racine au path.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
import hatch_build  # noqa: E402


def test_find_web_dir_locates_source_checkout():
    # Depuis ce module (tests/), on doit remonter jusqu'au `web/` du repo (porte package.json).
    web = webbuild.find_web_dir(Path(__file__))
    assert web is not None
    assert (web / "package.json").is_file()
    assert web.name == "web"


def test_find_web_dir_none_when_no_sources(tmp_path: Path):
    # Arbre sans `web/package.json` (simule une install wheel pure) → None.
    assert webbuild.find_web_dir(tmp_path) is None


def test_build_front_fails_loud_without_node(tmp_path: Path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text("{}")
    monkeypatch.setattr(webbuild.shutil, "which", lambda _name: None)  # Node absent
    with pytest.raises(webbuild.FrontBuildError) as exc:
        webbuild.build_front(web)
    assert "Node" in str(exc.value) and "cockpit setup" in str(exc.value)


# -- code-map (from-clone : rendre `python -m codemap` dispo, requis par l'onglet Flow) -------------------

def _seed_codemap_sibling(root: Path) -> Path:
    """Crée `root/code-map/src/codemap/__main__.py` (checkout code-map sibling minimal) et renvoie le path."""
    cm = root / "code-map" / "src" / "codemap"
    cm.mkdir(parents=True)
    (cm / "__main__.py").write_text("")
    return root / "code-map"


def test_find_codemap_src_locates_sibling(tmp_path: Path):
    _seed_codemap_sibling(tmp_path)
    start = tmp_path / "cockpit" / "src" / "cockpit" / "webbuild.py"
    start.parent.mkdir(parents=True)
    start.write_text("")
    assert webbuild.find_codemap_src(start) == tmp_path / "code-map"


def test_find_codemap_src_none_when_no_sibling(tmp_path: Path):
    start = tmp_path / "cockpit" / "webbuild.py"
    start.parent.mkdir(parents=True)
    start.write_text("")
    assert webbuild.find_codemap_src(start) is None


def test_ensure_codemap_noop_when_already_importable(monkeypatch):
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: object())
    called: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda *a, **k: called.append(a))
    msg = webbuild.ensure_codemap()
    assert "déjà disponible" in msg and called == []                 # aucun pip lancé


def test_ensure_codemap_warns_when_no_sibling(monkeypatch):
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: None)
    monkeypatch.setattr(webbuild, "find_codemap_src", lambda: None)
    called: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda *a, **k: called.append(a))
    msg = webbuild.ensure_codemap()
    assert "introuvable" in msg and "Flow" in msg and called == []    # actionnable, jamais fatal, pas de pip


def test_ensure_codemap_installs_from_sibling(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: None)
    src = tmp_path / "code-map"
    monkeypatch.setattr(webbuild, "find_codemap_src", lambda: src)
    seen: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda cmd, **k: seen.append(cmd))
    msg = webbuild.ensure_codemap()
    assert "installé depuis" in msg
    assert seen == [[sys.executable, "-m", "pip", "install", str(src)]]


# -- cartes siblings (docs-map/front-map/task-map : from-clone → CLI dispo dans le venv de dev) ----------

def _seed_map_sibling(root: Path, repo: str, module: str) -> Path:
    """Crée `root/<repo>/src/<module>/__init__.py` (checkout carte sibling minimal)."""
    pkg = root / repo / "src" / module
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    return root / repo


def test_find_map_src_locates_sibling(tmp_path: Path):
    _seed_map_sibling(tmp_path, "front-map", "frontmap")
    start = tmp_path / "cockpit" / "src" / "cockpit" / "webbuild.py"
    start.parent.mkdir(parents=True)
    start.write_text("")
    assert webbuild.find_map_src("front-map", "frontmap", start) == tmp_path / "front-map"
    assert webbuild.find_map_src("docs-map", "docsmap", start) is None       # sibling absent → None


def test_ensure_map_noop_when_already_importable(monkeypatch):
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: object())
    called: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda *a, **k: called.append(a))
    assert "déjà disponible" in webbuild.ensure_map("front-map", "frontmap") and called == []


def test_ensure_map_warns_when_no_sibling(monkeypatch):
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: None)
    monkeypatch.setattr(webbuild, "find_map_src", lambda *a, **k: None)
    called: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda *a, **k: called.append(a))
    msg = webbuild.ensure_map("front-map", "frontmap")
    assert "introuvable" in msg and "frontmap" in msg and called == []       # actionnable, jamais fatal


def test_ensure_map_installs_from_sibling(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: None)
    monkeypatch.setattr(webbuild, "find_map_src", lambda *a, **k: tmp_path / "front-map")
    seen: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda cmd, **k: seen.append(cmd))
    assert "installé depuis" in webbuild.ensure_map("front-map", "frontmap")
    assert str(tmp_path / "front-map") in seen[0]                            # pip a visé le sibling


def test_ensure_maps_covers_the_four_framework_maps(monkeypatch):
    """`ensure_maps` câble les 4 cartes : code-map (Flow) + docs-map/front-map/task-map. Le trou historique
    (seul code-map câblé → frontmap absent en dev) est fermé."""
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: object())   # présentes → no-op
    report = webbuild.ensure_maps()
    assert len(report) == 4
    joined = " ".join(report)
    for module in ("code-map", "docsmap", "frontmap", "taskmap"):
        assert module in joined                                             # les 4 sont rapportées


# -- hook de packaging : décision d'embarquement (SPA + code-map + taskmap) dans le wheel ----------------

def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")


def _stage_all(root: Path) -> None:
    _touch(root / "web" / "dist" / "index.html")
    _touch(root / "build" / "vendor" / "codemap" / "__main__.py")
    _touch(root / "build" / "vendor" / "taskmap" / "__init__.py")
    _touch(root / "build" / "vendor" / "verify-runner" / "render_check.js")
    _touch(root / "src" / "cockpit" / "_build.json")


def test_plan_force_includes_embeds_all_when_staged(tmp_path: Path):
    _stage_all(tmp_path)
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert force == {
        "web/dist": "cockpit/_web_dist",
        "build/vendor/codemap": "codemap",
        "build/vendor/taskmap": "taskmap",
        "build/vendor/verify-runner": "cockpit/_verify_runner",
        "src/cockpit/_build.json": "cockpit/_build.json",
    }
    assert warnings == []


def test_plan_force_includes_warns_when_runner_absent(tmp_path: Path):
    _touch(tmp_path / "web" / "dist" / "index.html")
    _touch(tmp_path / "build" / "vendor" / "codemap" / "__main__.py")
    _touch(tmp_path / "build" / "vendor" / "taskmap" / "__init__.py")   # tout sauf le runner
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert "build/vendor/verify-runner" not in force                    # runner NON embarqué
    assert any("verify-runner" in w or "Tier-1.5" in w for w in warnings)


def test_plan_force_includes_warns_when_codemap_absent(tmp_path: Path):
    _touch(tmp_path / "web" / "dist" / "index.html")            # SPA présente, code-map absent
    _touch(tmp_path / "build" / "vendor" / "taskmap" / "__init__.py")
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert "build/vendor/codemap" not in force                  # codemap NON embarqué
    assert any("code-map" in w for w in warnings)


def test_plan_force_includes_warns_when_taskmap_absent(tmp_path: Path):
    _touch(tmp_path / "web" / "dist" / "index.html")            # SPA + code-map présents, taskmap absent
    _touch(tmp_path / "build" / "vendor" / "codemap" / "__main__.py")
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert "build/vendor/taskmap" not in force                  # taskmap NON embarqué
    assert any("taskmap" in w for w in warnings)


def test_plan_force_includes_warns_when_build_stamp_absent(tmp_path: Path):
    _touch(tmp_path / "web" / "dist" / "index.html")            # tout sauf le tampon de provenance
    _touch(tmp_path / "build" / "vendor" / "codemap" / "__main__.py")
    _touch(tmp_path / "build" / "vendor" / "taskmap" / "__init__.py")
    _touch(tmp_path / "build" / "vendor" / "verify-runner" / "render_check.js")
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert "src/cockpit/_build.json" not in force               # provenance NON embarquée
    assert any("_build.json" in w or "provenance" in w for w in warnings)


def test_plan_force_includes_warns_when_all_absent(tmp_path: Path):
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert force == {}
    assert len(warnings) == 5                                   # SPA+codemap+taskmap+runner+provenance
