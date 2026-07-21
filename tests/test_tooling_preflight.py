"""Tests du preflight de présence (P1 tooling-fulfillment) : le parseur `allowedTools`, la résolution
`missing_bins`, la greffe au dispatch (un binaire hôte déclaré absent → dispatch refusé AVANT le spawn, le
runner n'est JAMAIS appelé), et la sonde `cockpit doctor`. Runner injecté, coffre/worktree réels.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cockpit import doctor
from cockpit.config import Settings
from cockpit.core import run
from cockpit.db import store
from cockpit.dispatch import worker
from cockpit.projects import registry
from cockpit.roadmap import model
from cockpit.tools import missing_bins, required_bins, tools_bin


def _settings(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def _seed_project(conn, settings, *, project="proj", feature="feat", task="t") -> None:
    registry.create_project(conn, settings, slug=project)   # type générique → facette `doc` (exige docsmap)
    model.add_feature(conn, project_slug=project, slug=feature)
    model.add_task(conn, feature_ref=f"{project}/{feature}", slug=task)


def _seed_runner(settings: Settings) -> None:
    """Pose le runner Tier-1.5 à son chemin défaut → la sonde `runner verify` passe au vert."""
    from cockpit.gate.verify import runner_path
    p = runner_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("// stub runner\n")


def _force_runtime_green(monkeypatch) -> None:
    """Rend déterministes runtime + provider compose + device TUN (le vrai `which`/`/dev/net/tun`
    dépendrait du podman hôte)."""
    monkeypatch.setattr("cockpit.runtime.backend.runtime_available", lambda *a, **k: True)
    monkeypatch.setattr("cockpit.runtime.backend.compose_provider_available", lambda *a, **k: True)
    monkeypatch.setattr("cockpit.doctor._tun_present", lambda: True)


# -- parseur `allowedTools` -------------------------------------------------------------------------

def test_required_bins_extracts_bash_ignores_rest(tmp_path: Path):
    p = tmp_path / "settings.local.json"
    p.write_text(json.dumps({"permissions": {"allow": [
        "Read", "Glob", "Edit", "Bash(node:*)", "Bash(codemap:*)", "Bash(pip install:*)"]}}))
    assert required_bins(p) == {"node", "codemap", "pip"}   # `pip install:*` → `pip` ; non-Bash ignorés


def test_required_bins_failsoft_on_absent_or_malformed(tmp_path: Path):
    assert required_bins(tmp_path / "nope.json") == set()          # absent → set() (rien à exiger)
    bad = tmp_path / "bad.json"
    bad.write_text("{ pas du json")
    assert required_bins(bad) == set()                            # mal formé → set()


def test_missing_bins_resolves_present_lists_absent(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for n in ("codemap", "node"):
        exe = bindir / n
        exe.write_text("#!/bin/sh\n:\n")
        exe.chmod(0o755)
    env = {"PATH": str(bindir)}
    assert missing_bins({"codemap", "node", "frontmap"}, env) == ["frontmap"]   # trié, seul l'absent


# -- greffe au dispatch -----------------------------------------------------------------------------

def test_preflight_blocks_dispatch_when_host_tool_missing(tmp_path: Path, fake_tools, monkeypatch):
    # Hôte provisionné SAUF `docsmap` (exigé par la facette doc) → dispatch refusé, runner jamais appelé.
    # HERMÉTIQUE : on sème tout-sauf-docsmap dans `tools_bin` ET on épingle le PATH du worker à ce seul
    # répertoire (sinon `tools_env` appende `os.environ` → un `docsmap` du venv de test fuiterait et le
    # preflight passerait à tort — la version « PAS de fake_tools » dépendait de l'absence AMBIANTE).
    settings = _settings(tmp_path)
    fake_tools(settings, omit={"docsmap"})
    monkeypatch.setattr(worker, "tools_env", lambda s, **_k: {"PATH": str(tools_bin(s))})
    conn = store.open_db(settings)
    try:
        _seed_project(conn, settings)
        calls: list = []

        def runner(argv, *, cwd, input_text, timeout, env=None):
            calls.append(argv)                               # ne devrait JAMAIS s'exécuter
            return run.RunResult(argv=list(argv), returncode=0, stdout="{}", stderr="")

        report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=runner)
        assert report["dispatched"] is True
        assert report["result"]["ok"] is False
        assert "docsmap" in report["result"]["error"]        # message actionnable nommant l'outil
        assert calls == []                                   # runner JAMAIS appelé (échec tôt)
        # task re-dispatchable (revenue todo)
        row = conn.execute("SELECT status FROM tasks WHERE slug = 't'").fetchone()
        assert row["status"] == "todo"
    finally:
        conn.close()


def test_preflight_passes_when_tools_present(tmp_path: Path, fake_tools):
    settings = _settings(tmp_path)
    conn = store.open_db(settings)
    try:
        fake_tools(settings)                                 # hôte provisionné → docsmap résout
        _seed_project(conn, settings)
        called: list = []

        def runner(argv, *, cwd, input_text, timeout, env=None):
            called.append(1)
            sid = argv[argv.index("--session-id") + 1]
            out = json.dumps({"is_error": False, "result": "ok", "session_id": sid})
            return run.RunResult(argv=list(argv), returncode=0, stdout=out, stderr="")

        report = worker.dispatch_next(conn, settings, feature_ref="proj/feat", runner=runner)
        assert report["dispatched"] and report["result"]["ok"]
        assert called == [1]                                 # runner atteint (preflight vert)
    finally:
        conn.close()


# -- sonde `cockpit doctor` -------------------------------------------------------------------------

def test_doctor_green_when_all_host_tools_present(tmp_path: Path, fake_tools, capsys, monkeypatch):
    settings = _settings(tmp_path)
    fake_tools(settings)
    _force_runtime_green(monkeypatch)                        # podman + provider compose présents
    _seed_runner(settings)                                   # runner Tier-1.5 présent
    rc = doctor.cli_dispatch(settings, argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "outillage complet" in out
    assert "base/doc" in out                                 # rapport par type/facette
    assert "provider compose" in out and "runner verify" in out   # les sondes deploy rapportent
    assert "device TUN" in out                                     # sonde TUN (build réseau deploy)


def test_doctor_red_and_names_missing_tool(tmp_path: Path, capsys):
    settings = _settings(tmp_path)                           # aucun outil provisionné
    rc = doctor.cli_dispatch(settings, argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "INCOMPLET" in out and "docsmap" in out           # nomme au moins un manquant


def test_doctor_red_when_verify_runner_absent(tmp_path: Path, fake_tools, capsys, monkeypatch):
    """Outils hôte OK + runtime OK mais runner Tier-1.5 absent → 🔴 (sinon `gate verify` fail-close muet)."""
    settings = _settings(tmp_path)
    fake_tools(settings)
    _force_runtime_green(monkeypatch)                        # runner NON semé → sonde rouge
    rc = doctor.cli_dispatch(settings, argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "runner verify" in out and "absent" in out


def test_doctor_red_when_compose_provider_missing(tmp_path: Path, fake_tools, capsys, monkeypatch):
    """`podman` présent mais provider compose absent → 🔴 (faux-vert que `_report_runtime` seul rate)."""
    settings = _settings(tmp_path)
    fake_tools(settings)
    _seed_runner(settings)
    # podman présent (moteur), mais son provider compose absent
    monkeypatch.setattr("cockpit.runtime.backend.runtime_available", lambda *a, **k: True)
    monkeypatch.setattr("cockpit.runtime.backend.compose_provider_available", lambda *a, **k: False)
    rc = doctor.cli_dispatch(settings, argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "provider compose" in out and "podman-compose" in out


def test_doctor_red_when_tun_absent(tmp_path: Path, fake_tools, capsys, monkeypatch):
    """Outils + runtime + runner OK mais `/dev/net/tun` absent → 🔴 (sinon le build `cockpit deploy` meurt
    sur `slirp4netns EOF` sans que le doctor prévienne)."""
    settings = _settings(tmp_path)
    fake_tools(settings)
    _force_runtime_green(monkeypatch)                        # runtime + provider + tun forcés verts…
    _seed_runner(settings)
    monkeypatch.setattr("cockpit.doctor._tun_present", lambda: False)   # …puis TUN absent
    rc = doctor.cli_dispatch(settings, argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "device TUN" in out and "absent" in out
