"""Tests du verdict d'autorité — `projects_root` est hors instantané, et ce module dit à quel prix.

Le piège que ces tests gardent n'est pas de mal classer un projet : c'est de **bloquer ce qui est normal**.
« Aucun remote » est le cas ordinaire d'un utilisateur du forgemaster distribué — son `sot.git` bare local est
la seule copie *par choix*. Un garde qui refuserait dessus lui interdirait toute mise à jour, définitivement.
D'où l'arbitrage : seul le travail **non commité** bloque ; le reste est dit, et se voit.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from forgemaster import update
from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.projects import authority

_INSERT = "INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?, ?, ?, ?, ?)"


class GitDeDecor:
    """Un `GitBackend` de décor : il ne connaît que ce qu'on lui a dit. L'injection est l'invariant du repo —
    ces tests ne doivent toucher ni un vrai dépôt, ni le réseau."""

    def __init__(self, *, statuses: dict[Path, dict] | None = None,
                 divergences: dict[Path, dict] | None = None) -> None:
        self.statuses = statuses or {}
        self.divergences = divergences or {}
        self.fetched: list[Path] = []

    def status(self, workdir: Path) -> dict:
        return self.statuses.get(workdir, {"clean": True, "files": []})

    def remote_divergence(self, sot: Path, *, remote: str, branches, creds_ref=None) -> dict:
        self.fetched.append(sot)
        return self.divergences.get(sot, {"remote": remote, "fetched": True, "branches": {},
                                          "state": "synced"})


@pytest.fixture
def instance(tmp_path: Path) -> Settings:
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projets")
    store.open_db(settings).close()
    return settings


def _sème(settings: Settings, slug: str, *, worktrees: tuple[str, ...] = ()) -> Path:
    """Un projet sur le disque : son SoT bare et, éventuellement, des worktrees. Noms fictifs, comme partout
    ici — un vrai basename de projet polluerait le graphe."""
    sot = settings.projects_root / slug / "sot.git"
    sot.mkdir(parents=True, exist_ok=True)
    for wt in worktrees:
        (settings.projects_root / slug / "worktrees" / wt).mkdir(parents=True, exist_ok=True)
    conn = store.connect(settings.db_path)
    conn.execute(_INSERT, (f"id-{slug}", slug, slug, str(sot), "2026-08-06T00:00:00Z"))
    conn.commit()
    conn.close()
    return sot


# --- les trois cas qui comptent -----------------------------------------------------------------------

def test_du_travail_non_commite_est_le_seul_etat_bloquant(instance: Settings):
    """Ce que la MAJ ne peut pas rendre : `projects_root` n'entre pas dans l'instantané, donc du non commité
    qui disparaît ne revient pas. C'est le seul motif de refus."""
    _sème(instance, "atelier-fictif", worktrees=("essai",))
    sale = instance.projects_root / "atelier-fictif" / "worktrees" / "essai"
    git = GitDeDecor(statuses={sale: {"clean": False, "files": [{"path": "a"}, {"path": "b"}]}})

    conn = store.connect(instance.db_path)
    verdicts = authority.survey(conn, instance, git)
    conn.close()

    assert [v["state"] for v in verdicts] == ["uncommitted"]
    assert "essai (2 fichiers)" in verdicts[0]["detail"]
    assert authority.blocking(verdicts) == verdicts


def test_aucun_remote_est_dit_jamais_bloque(instance: Settings):
    """LE test de l'arbitrage. Un projet sans miroir est le cas NORMAL d'un utilisateur distribué : le
    produit doit le lui dire — « cette machine est la seule copie » — et le laisser passer."""
    sot = _sème(instance, "carnet-fictif")
    git = GitDeDecor(divergences={sot: {"remote": "mirror", "fetched": False, "branches": {},
                                        "state": "no_mirror"}})

    conn = store.connect(instance.db_path)
    verdicts = authority.survey(conn, instance, git)
    conn.close()

    assert [v["state"] for v in verdicts] == ["no_remote"]
    assert "SEULE copie" in verdicts[0]["detail"]
    assert authority.blocking(verdicts) == []              # ← rouge si « sans remote » devenait bloquant


def test_des_commits_non_pousses_sont_dits_avec_leur_compte(instance: Settings):
    """Distinct de « non commité » : le travail EST dans git, il n'est simplement pas ailleurs. On le
    nomme — c'est ce que l'utilisateur doit savoir avant de dire oui — mais on ne bloque pas."""
    sot = _sème(instance, "grimoire-fictif")
    git = GitDeDecor(divergences={sot: {"remote": "mirror", "fetched": True, "state": "local_ahead",
                                        "branches": {"dev": {"ahead": 4, "behind": 0,
                                                             "state": "local_ahead"},
                                                     "main": {"ahead": 0, "behind": 0, "state": "synced"}}}})

    conn = store.connect(instance.db_path)
    verdicts = authority.survey(conn, instance, git)
    conn.close()

    assert [v["state"] for v in verdicts] == ["unpushed"]
    assert "dev (+4)" in verdicts[0]["detail"]
    assert authority.blocking(verdicts) == []


# --- dégradations honnêtes ----------------------------------------------------------------------------

def test_un_remote_injoignable_ne_devient_jamais_un_faux_vert(instance: Settings):
    sot = _sème(instance, "atlas-fictif")
    git = GitDeDecor(divergences={sot: {"remote": "mirror", "fetched": False, "branches": {},
                                        "state": "unreachable"}})

    conn = store.connect(instance.db_path)
    (verdict,) = authority.survey(conn, instance, git)
    conn.close()

    assert verdict["state"] == "unreachable"
    assert authority.blocking([verdict]) == []             # ne pas savoir n'est pas un motif de refus


def test_un_worktree_que_git_refuse_de_lire_compte_comme_sale(instance: Settings):
    """On ne conclut pas au propre sur une erreur : le seul état qu'on ne peut pas se permettre de rater est
    justement celui qui bloque."""
    _sème(instance, "vitrail-fictif", worktrees=("muet",))
    muet = instance.projects_root / "vitrail-fictif" / "worktrees" / "muet"

    class GitQuiLeve(GitDeDecor):
        def status(self, workdir: Path) -> dict:
            raise RuntimeError("dépôt illisible")

    conn = store.connect(instance.db_path)
    (verdict,) = authority.survey(conn, instance, GitQuiLeve())
    conn.close()

    assert verdict["state"] == "uncommitted"
    assert muet.name in verdict["detail"]


def test_un_projet_dont_le_sot_a_disparu_est_dit_pas_tu(instance: Settings):
    _sème(instance, "fantome-fictif")
    import shutil
    shutil.rmtree(instance.projects_root / "fantome-fictif" / "sot.git")

    conn = store.connect(instance.db_path)
    (verdict,) = authority.survey(conn, instance, GitDeDecor())
    conn.close()

    assert verdict["state"] == "missing"
    assert authority.blocking([verdict]) == []


# --- le câblage du refus -------------------------------------------------------------------------------

@pytest.fixture
def prete_a_maj(tmp_path: Path) -> tuple[Settings, dict]:
    """Une instance dont TOUT le reste passe : wheel présent, unité qui lance le lien stable, lien posé.
    Sans ça, le preflight s'arrêterait à un refus antérieur et le test ne prouverait rien du nôtre — piège
    dans lequel la première version de ce test était tombée."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projets")
    store.open_db(settings).close()
    venv = tmp_path / "venvs" / "courant"
    (venv / "bin").mkdir(parents=True)
    (settings.home / "current").symlink_to(venv)
    unit = tmp_path / "forgemaster.service"
    unit.write_text(f"[Service]\nExecStart={settings.home / 'current' / 'bin' / 'forgemaster'} serve "
                    f"--host 127.0.0.1 --port 8700\n", encoding="utf-8")
    whl = tmp_path / "forgemaster-9.9.9-py3-none-any.whl"
    whl.write_bytes(b"PK\x03\x04")
    return settings, {"wheel": str(whl), "unit": str(unit), "scope": "user"}


def test_le_preflight_refuse_sur_du_non_commite_et_le_nomme(prete_a_maj):
    """Le quatrième refus du verbe : levé AVANT tout effet, et il NOMME le projet — pas un « impossible » nu.
    Le message doit aussi dire le geste qui débloque, comme les trois autres."""
    settings, args = prete_a_maj
    verdicts = [{"slug": "atelier-fictif", "state": "uncommitted",
                 "detail": "travail NON COMMITÉ : essai (2 fichiers)"},
                {"slug": "carnet-fictif", "state": "no_remote", "detail": "aucun remote"}]

    with pytest.raises(update.UpdateRefused) as exc:
        update.preflight(settings, **args, authority=verdicts)

    message = str(exc.value)
    assert "atelier-fictif" in message and "essai (2 fichiers)" in message
    assert "carnet-fictif" not in message                   # ce qui ne bloque pas ne figure pas au refus
    assert "commite" in message                             # le geste qui débloque, comme les 3 autres refus


def test_sans_travail_non_commite_le_preflight_passe_et_porte_les_verdicts(prete_a_maj):
    """La contre-épreuve : le refus ne doit pas s'allumer sur « aucun remote » — sinon un utilisateur sans
    miroir ne pourrait plus jamais se mettre à jour."""
    settings, args = prete_a_maj
    verdicts = [{"slug": "carnet-fictif", "state": "no_remote",
                 "detail": "aucun remote — cette machine est la SEULE copie de ce projet"}]

    plan = update.preflight(settings, **args, authority=verdicts)

    assert plan["authority"] == verdicts
    assert "SEULE copie" in "\n".join(update.describe(plan))


def test_le_plan_imprime_ce_qui_na_pas_bloque():
    """« Aucun remote » ne bloque pas — donc il DOIT se voir, sinon l'exclusion de `projects_root` reste une
    hypothèse silencieuse au lieu d'une constatation dite."""
    plan = {"wheel": "w.whl", "venv": "v", "link": "l", "unit": "u", "scope": "user",
            "base_url": "http://127.0.0.1:8700", "projects_root": "/projets",
            "authority": [{"slug": "carnet-fictif", "state": "no_remote",
                           "detail": "aucun remote — cette machine est la SEULE copie de ce projet"},
                          {"slug": "propre-fictif", "state": "clean_pushed", "detail": "propre et poussé"}]}

    rendu = "\n".join(update.describe(plan))

    assert "carnet-fictif" in rendu and "SEULE copie" in rendu
    assert "propre-fictif" not in rendu                    # le serein ne fait pas de bruit


def test_la_base_absente_ne_produit_aucun_verdict(tmp_path: Path):
    """Un preflight ne doit rien ouvrir en écriture avant d'avoir le droit de refuser : pas de base sur le
    disque → pas de verdict, et surtout pas de base créée pour l'occasion."""
    settings = Settings.resolve(home=tmp_path / "vide", projects_root=tmp_path / "p")

    assert update.survey_authority(settings) == []
    assert not settings.db_path.exists()


def test_une_base_illisible_ne_bloque_pas(tmp_path: Path):
    """Ce module ne bloque que sur ce qu'il SAIT. Une base d'un schéma qu'on ne lit pas rend « je ne sais
    pas » — un refus là-dessus serait un garde qui s'allume sur son propre aveuglement."""
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "p")
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.db_path.write_bytes(b"ceci n'est pas une base SQLite")

    with pytest.raises(sqlite3.DatabaseError):             # prémisse : elle est bien illisible
        store.connect(settings.db_path).execute("SELECT 1 FROM projects")

    assert update.survey_authority(settings) == []
