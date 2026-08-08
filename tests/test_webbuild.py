"""Tests de `webbuild` — localisation de `web/`, fail-loud sans Node, mise à dispo de code-map (from-clone),
et décision d'embarquement du hook de packaging. On ne lance JAMAIS npm/pip en test (lent, réseau) : on
prouve le câblage et le message actionnable, pas le build/install lui-même (couvert par l'acceptance
venv-neuf / CT vierge)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from forgemaster import webbuild

# hatch_build.py vit à la RACINE du repo (hook de packaging, volontairement hors du package forgemaster — il
# tourne au build quand `forgemaster` n'est pas encore importable). On l'importe en ajoutant la racine au
# path.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
import hatch_build  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_pip(monkeypatch):
    """GARDE-FOU DE MODULE : aucun test d'ici ne lance un vrai `pip`/`npm`.

    L'invariant est écrit en tête de fichier depuis toujours — rien ne le tenait. Le 2026-08-01, un test qui
    neutralisait `find_spec` (« la carte est déjà là, donc pas d'install ») a cessé d'être protégé quand
    `ensure_map` a commencé à chercher le sibling AVANT de se satisfaire d'un module importable : il a lancé
    4 `pip install -e` **réels** dans le venv du développeur, et mis 8 s à passer au vert.

    Un test qui mute l'environnement de celui qui le lance est un test qui ment sur ce qu'il prouve. Ici, on
    échoue FORT et on nomme l'argv — un test qui a besoin d'un `subprocess.run` le patche explicitement (le
    `monkeypatch` du test l'emporte, il est appliqué après cette fixture)."""
    def _interdit(cmd, *a, **k):
        raise AssertionError(f"subprocess réel interdit en test — patche-le explicitement : {cmd}")
    monkeypatch.setattr(webbuild.subprocess, "run", _interdit)


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
    assert "Node" in str(exc.value) and "forgemaster setup" in str(exc.value)


# -- code-map (from-clone : rendre `python -m codemap` dispo, requis par l'onglet Flow) -------------------

def _seed_codemap_sibling(root: Path) -> Path:
    """Crée `root/code-map/src/codemap/__main__.py` (checkout code-map sibling minimal) et renvoie le path."""
    cm = root / "code-map" / "src" / "codemap"
    cm.mkdir(parents=True)
    (cm / "__main__.py").write_text("")
    return root / "code-map"


def test_find_codemap_src_locates_sibling(tmp_path: Path):
    _seed_codemap_sibling(tmp_path)
    start = tmp_path / "forgemaster" / "src" / "forgemaster" / "webbuild.py"
    start.parent.mkdir(parents=True)
    start.write_text("")
    assert webbuild.find_codemap_src(start) == tmp_path / "code-map"


def test_find_codemap_src_none_when_no_sibling(tmp_path: Path):
    start = tmp_path / "forgemaster" / "webbuild.py"
    start.parent.mkdir(parents=True)
    start.write_text("")
    assert webbuild.find_codemap_src(start) is None


def test_ensure_codemap_noop_when_already_importable(monkeypatch):
    """Chemin WHEEL : code-map est empaqueté, aucun sibling n'existe → on se contente de l'importable."""
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: object())
    monkeypatch.setattr(webbuild, "find_codemap_src", lambda: None)
    called: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda *a, **k: called.append(a))
    msg = webbuild.ensure_codemap()
    assert "déjà disponible" in msg and called == []                 # aucun pip lancé


def test_ensure_codemap_noop_si_deja_editable_depuis_le_sibling(monkeypatch, tmp_path: Path):
    """Idempotence SANS relancer pip : `pip install -e` reconstruit un wheel à chaque appel, et
    `forgemaster setup` le paierait × 4 pour rien."""
    src = tmp_path / "code-map"
    monkeypatch.setattr(webbuild, "find_codemap_src", lambda: src)
    monkeypatch.setattr(webbuild, "served_from", lambda _m, _s: True)
    called: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda *a, **k: called.append(a))
    msg = webbuild.ensure_codemap()
    assert "déjà éditable" in msg and called == []


def test_une_COPIE_figee_est_remplacee_par_un_editable(monkeypatch, tmp_path: Path):
    """LE test de non-régression du 2026-08-01. Le module s'importait parfaitement — depuis une copie de
    `site-packages` — et le câblage court-circuitait donc sur sa seule présence. Résultat : le venv de ce
    repo a servi pendant une journée un `codemap` SANS le verbe `check` que sa constitution prescrit, avec
    exactement le même numéro de version que la source. Un module importable ne prouve pas qu'il est à
    jour."""
    src = tmp_path / "code-map"
    copie = tmp_path / "venv" / "site-packages" / "codemap" / "__init__.py"
    copie.parent.mkdir(parents=True)
    copie.write_text("")
    monkeypatch.setattr(webbuild, "find_codemap_src", lambda: src)
    monkeypatch.setattr(webbuild.importlib.util, "find_spec",
                        lambda _n: SimpleNamespace(origin=str(copie)))
    seen: list = []
    monkeypatch.setattr(webbuild.subprocess, "run", lambda cmd, **k: seen.append(cmd))
    msg = webbuild.ensure_codemap()
    assert "éditable" in msg
    assert seen == [[sys.executable, "-m", "pip", "install", "-e", str(src)]]


def test_served_from_distingue_la_copie_de_l_editable(tmp_path: Path, monkeypatch):
    """Le discriminant du correctif, isolé : l'origine du module vient-elle des sources du sibling ?"""
    src = tmp_path / "front-map"
    (src / "src" / "frontmap").mkdir(parents=True)
    editable = src / "src" / "frontmap" / "__init__.py"
    editable.write_text("")
    monkeypatch.setattr(webbuild.importlib.util, "find_spec",
                        lambda _n: SimpleNamespace(origin=str(editable)))
    assert webbuild.served_from("frontmap", src) is True
    monkeypatch.setattr(webbuild.importlib.util, "find_spec",
                        lambda _n: SimpleNamespace(origin=str(tmp_path / "sp" / "frontmap" / "__init__.py")))
    assert webbuild.served_from("frontmap", src) is False
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: None)
    assert webbuild.served_from("frontmap", src) is False        # absent ≠ servi depuis les sources


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
    assert "installé (éditable) depuis" in msg
    assert seen == [[sys.executable, "-m", "pip", "install", "-e", str(src)]]


# -- cartes siblings (docs-map/front-map/task-map : from-clone → CLI dispo dans le venv de dev) ----------

def _seed_map_sibling(root: Path, repo: str, module: str) -> Path:
    """Crée `root/<repo>/src/<module>/__init__.py` (checkout carte sibling minimal)."""
    pkg = root / repo / "src" / module
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    return root / repo


def test_find_map_src_locates_sibling(tmp_path: Path):
    _seed_map_sibling(tmp_path, "front-map", "frontmap")
    start = tmp_path / "forgemaster" / "src" / "forgemaster" / "webbuild.py"
    start.parent.mkdir(parents=True)
    start.write_text("")
    assert webbuild.find_map_src("front-map", "frontmap", start) == tmp_path / "front-map"
    assert webbuild.find_map_src("docs-map", "docsmap", start) is None       # sibling absent → None


def test_ensure_map_noop_when_already_importable(monkeypatch):
    """Chemin WHEEL : la carte vient du provisioning, aucun sibling → on se contente de l'importable."""
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: object())
    monkeypatch.setattr(webbuild, "find_map_src", lambda *a, **k: None)
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
    assert "installé (éditable) depuis" in webbuild.ensure_map("front-map", "frontmap")
    assert seen[0][-2:] == ["-e", str(tmp_path / "front-map")]               # pip a visé le sibling, éditable


def test_ensure_maps_covers_the_four_framework_maps(monkeypatch):
    """`ensure_maps` câble les 4 cartes : code-map (Flow) + docs-map/front-map/task-map. Le trou historique
    (seul code-map câblé → frontmap absent en dev) est fermé.

    `served_from` est neutralisé, pas `find_spec` : depuis que le court-circuit passe APRÈS la recherche du
    sibling, « le module s'importe » ne suffit plus à éviter pip — et ce test tournait dans un vrai checkout,
    avec de vrais siblings. Il a effectivement lancé 4 `pip install -e` dans le venv du développeur avant
    d'être corrigé ; d'où aussi le garde-fou `_no_real_pip` en tête de module."""
    monkeypatch.setattr(webbuild, "served_from", lambda _m, _s: True)                # déjà à jour → no-op
    monkeypatch.setattr(webbuild.importlib.util, "find_spec", lambda _n: object())   # présentes
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
    _touch(root / "build" / "vendor" / "edition-maps" / "maps.json")
    _touch(root / "src" / "forgemaster" / "_build.json")


def test_plan_force_includes_embeds_all_when_staged(tmp_path: Path):
    _stage_all(tmp_path)
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert force == {
        "web/dist": "forgemaster/_web_dist",
        "build/vendor/codemap": "codemap",
        "build/vendor/taskmap": "taskmap",
        "build/vendor/verify-runner": "forgemaster/_verify_runner",
        "build/vendor/edition-maps": "forgemaster/_maps",
        "src/forgemaster/_build.json": "forgemaster/_build.json",
    }
    assert warnings == []


def test_plan_force_includes_warns_when_the_edition_maps_are_absent(tmp_path: Path):
    """Sans elles, `forgemaster toolchain install` REFUSE de poser (fail-loud, pas de repli git) : l'hôte
    resterait sans code-map/docsmap/frontmap. Le wheel se bâtit quand même — c'est le cas normal d'un build
    dev — mais il le DIT."""
    _stage_all(tmp_path)
    (tmp_path / "build" / "vendor" / "edition-maps" / "maps.json").unlink()
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert "build/vendor/edition-maps" not in force
    assert any("_maps" in w or "cartes" in w for w in warnings)


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
    assert "src/forgemaster/_build.json" not in force               # provenance NON embarquée
    assert any("_build.json" in w or "provenance" in w for w in warnings)


def test_plan_force_includes_warns_when_all_absent(tmp_path: Path):
    force, warnings = hatch_build.plan_force_includes(tmp_path)
    assert force == {}
    assert len(warnings) == 6                          # SPA+codemap+taskmap+runner+cartes+provenance


# -- garde d'INVENTAIRE : « et rien d'autre » (ce qu'une garde de présence ne peut pas dire) ---------------

_DECLARE = dict(                                                # ce que `git ls-files` rendrait, en miniature
    src_files={"cli.py", "daemon/app.py"},
    codemap_files={"__main__.py", "core/graph.py"},
    taskmap_files={"__init__.py"},
    runner_files={"render_check.js", "package.json"},
    maps_files={"code_map-0.1.0-py3-none-any.whl", "docs_map-0.1.0-py3-none-any.whl",
                "front_map-0.1.0-py3-none-any.whl", "maps.json"},
)
_SAIN = [
    "forgemaster/cli.py",
    "forgemaster/daemon/app.py",
    "forgemaster/_build.json",
    "forgemaster/_web_dist/index.html",
    "forgemaster/_web_dist/assets/index-D4f2a9.js",             # nom HASHÉ par Vite : admis par préfixe
    "forgemaster/_verify_runner/render_check.js",
    "forgemaster/_verify_runner/package.json",
    "forgemaster/_maps/maps.json",
    "forgemaster/_maps/code_map-0.1.0-py3-none-any.whl",
    "forgemaster/_maps/docs_map-0.1.0-py3-none-any.whl",
    "forgemaster/_maps/front_map-0.1.0-py3-none-any.whl",
    "codemap/__main__.py",
    "codemap/core/graph.py",
    "codemap/_vendored_from.txt",
    "taskmap/__init__.py",
    "taskmap/_vendored_from.txt",
    "forgemaster-0.1.0.dist-info/METADATA",
    "forgemaster-0.1.0.dist-info/licenses/NOTICE",
]


def test_inventaire_clos_ne_signale_rien_sur_un_wheel_sain():
    assert hatch_build.unexpected_wheel_members(list(_SAIN), **_DECLARE) == []


def test_inventaire_nomme_le_node_modules_du_poste_de_dev():
    """La régression MESURÉE le 2026-08-08 : 135 membres partis par `force_include`, jamais lus par
    personne (`seed_verify_runner` npm-installe sur la CIBLE)."""
    names = [*_SAIN, "forgemaster/_verify_runner/node_modules/playwright-core/cli.js"]
    assert hatch_build.unexpected_wheel_members(names, **_DECLARE) == [
        "forgemaster/_verify_runner/node_modules/playwright-core/cli.js"
    ]


def test_inventaire_nomme_le_bytecode_dun_sibling_vendore():
    """Le risque LATENT : le staging de codemap/taskmap porte bien des `.pyc` ; aujourd'hui c'est hatchling
    qui les écarte — un tiers non épinglé. La garde rend la propriété NÔTRE."""
    names = [*_SAIN, "codemap/__pycache__/cli.cpython-311.pyc"]
    assert hatch_build.unexpected_wheel_members(names, **_DECLARE) == [
        "codemap/__pycache__/cli.cpython-311.pyc"
    ]


def test_inventaire_refuse_une_racine_inconnue():
    names = [*_SAIN, "frontmap/__init__.py"]                    # un 3ᵉ vendoring qu'on n'aurait pas déclaré
    assert hatch_build.unexpected_wheel_members(names, **_DECLARE) == ["frontmap/__init__.py"]


def test_inventaire_nomme_un_module_non_suivi_sous_src():
    names = [*_SAIN, "forgemaster/scratch.py"]                  # un fichier gitignoré qui passerait le walk
    assert hatch_build.unexpected_wheel_members(names, **_DECLARE) == ["forgemaster/scratch.py"]


def test_inventaire_ferme_ledition_des_cartes_au_lieu_de_louvrir_par_prefixe():
    """`_maps/` est une liste FERMÉE, pas un préfixe : le staging d'un build précédent, un wheel de carte
    renommé, un `.whl` qui traîne — tout ça entrerait par un préfixe. `_web_dist/` reste le SEUL endroit où
    l'inventaire est ouvert, parce que ses noms sont hashés par le build front et ne se déclarent pas."""
    names = [*_SAIN, "forgemaster/_maps/task_map-0.1.0-py3-none-any.whl"]
    assert hatch_build.unexpected_wheel_members(names, **_DECLARE) == [
        "forgemaster/_maps/task_map-0.1.0-py3-none-any.whl"
    ]
