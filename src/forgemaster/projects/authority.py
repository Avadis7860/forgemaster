"""authority — pour chaque projet, **où est l'autorité sur son travail** : ailleurs, ou seulement ici.

> À ne pas confondre avec `gate.advertised_authority`, qui parle d'un `host:port` annoncé par un artefact
> servi. Même mot, deux sens sans rapport : ici il s'agit de la *garde* du travail d'un utilisateur.

`projects_root` est **hors instantané**, délibérément, au motif que « git fait autorité ». C'est vrai **chez
nous**, où chaque projet a un miroir distant. Chez un utilisateur du forgemaster distribué, un projet vit dans
un `sot.git` bare **local** et n'a de remote **que s'il en a configuré un** : son disque est parfois la seule
copie. L'exclusion reposait donc sur une hypothèse héritée de notre topologie, jamais vérifiée chez lui.

Ce module la transforme en **constatation**. Il ne déplace pas la frontière de l'instantané (le volume reste
rédhibitoire) — il rend l'hypothèse observable, et la dit avant qu'une MAJ ne parte.

Deux choix portent le reste :

- **Seul le travail non commité bloque** (arbitrage bosse, 2026-08-06). « Non poussé » et « aucun remote »
  sont **rendus**, jamais bloquants : un utilisateur sans remote est un cas *normal* du produit distribué —
  le bloquer lui interdirait toute mise à jour, définitivement. Un garde qui s'allume sur ce qui est normal
  est un garde défaillant.
- **Pure lecture, `GitBackend` injecté.** Aucun état n'est écrit, aucun réseau n'est exigé : un fetch qui
  échoue rend `unreachable`, jamais un faux « à jour » (garantie déjà tenue par `remote_divergence`).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from forgemaster.config import Settings
from forgemaster.git.backend import GitBackend
from forgemaster.projects import registry

# Le seul état qui REFUSE une mise à jour. Les autres sont dits, pas subis.
BLOCKING = "uncommitted"

# Les états rendus, du plus grave au plus serein.
STATES = ("uncommitted", "unreachable", "no_remote", "unpushed", "clean_pushed")

MIRROR = "mirror"
BRANCHES = ("dev", "main")

# `remote_divergence` → notre vocabulaire. Il rend déjà les états dégradés sans faux-vert : on les traduit,
# on ne les recalcule pas.
_FROM_DIVERGENCE = {
    "no_mirror": "no_remote",
    "unreachable": "unreachable",
    "synced": "clean_pushed",
    "local_ahead": "unpushed",
    "remote_ahead": "clean_pushed",   # le travail local EST ailleurs ; c'est l'instance qui est en retard
    "diverged": "unpushed",
}


def survey(conn: sqlite3.Connection, settings: Settings, git: GitBackend) -> list[dict]:
    """Le verdict de chaque projet connu, trié par slug. Un projet dont le SoT a disparu du disque est
    **rendu**, pas tu : `missing` n'est pas bloquant (il n'y a plus de travail à perdre) mais il est dit."""
    return [_verdict(settings, git, project) for project in registry.list_projects(conn)]


def blocking(verdicts: list[dict]) -> list[dict]:
    """Ceux qui refusent la MAJ. Un seul état bloque — cf. l'arbitrage en tête de module."""
    return [v for v in verdicts if v["state"] == BLOCKING]


def describe(verdicts: list[dict]) -> list[str]:
    """Une ligne par projet, dans l'ordre du verdict. Ce qui bloque porte `✗`, ce qui est seulement DIT
    porte `⚠` — la distinction est le sujet même du module, elle doit se voir à l'œil."""
    return [f"  {'✗' if v['state'] == BLOCKING else '⚠' if v['state'] != 'clean_pushed' else '·'} "
            f"{v['slug']} — {v['detail']}" for v in verdicts]


def _verdict(settings: Settings, git: GitBackend, project: dict) -> dict:
    slug = str(project["slug"])
    sot = registry.sot_path_for(settings, slug)
    if not sot.is_dir():
        return _row(slug, "missing", f"aucun SoT sur le disque ({sot}) — rien à perdre ici")

    dirty = _dirty_worktrees(settings, git, slug)
    if dirty:
        detail = ", ".join(f"{name} ({n} fichier{'s' if n > 1 else ''})" for name, n in dirty)
        return _row(slug, "uncommitted", f"travail NON COMMITÉ : {detail}")

    creds = project.get("credential_ref")
    try:
        div = git.remote_divergence(sot, remote=MIRROR, branches=BRANCHES,
                                    creds_ref=str(creds) if creds else None)
    except Exception as exc:                            # noqa: BLE001 (un git qui lève ne doit pas bloquer)
        return _row(slug, "unreachable", f"écart avec le remote non mesurable : {exc}")

    state = _FROM_DIVERGENCE.get(str(div.get("state")), "unreachable")
    return _row(slug, state, _detail(state, div))


def _detail(state: str, div: dict) -> str:
    if state == "no_remote":
        return "aucun remote — cette machine est la SEULE copie de ce projet"
    if state == "unreachable":
        return f"remote `{div.get('remote')}` injoignable — impossible de dire si le travail est ailleurs"
    if state == "unpushed":
        ahead = {b: d["ahead"] for b, d in (div.get("branches") or {}).items() if d.get("ahead")}
        if ahead:
            return "commits NON POUSSÉS : " + ", ".join(f"{b} (+{n})" for b, n in sorted(ahead.items()))
        return "en écart avec le remote"
    return "propre et poussé"


def _dirty_worktrees(settings: Settings, git: GitBackend, slug: str) -> list[tuple[str, int]]:
    """Les worktrees du projet qui portent du travail non commité, avec leur nombre de fichiers. Un worktree
    que git refuse de lire compte comme **sale** : on ne conclut pas au propre sur une erreur."""
    root = settings.projects_root / slug / "worktrees"
    if not root.is_dir():
        return []
    out: list[tuple[str, int]] = []
    for wt in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            status = git.status(wt)
        except Exception:                               # noqa: BLE001
            out.append((wt.name, 0))
            continue
        if not status.get("clean", True):
            out.append((wt.name, len(status.get("files") or [])))
    return out


def _row(slug: str, state: str, detail: str) -> dict:
    return {"slug": slug, "state": state, "detail": detail}


def worktrees_root(settings: Settings, slug: str) -> Path:
    """`<projects_root>/<slug>/worktrees` — même convention que `dispatch.worktree.worktree_path_for`."""
    return settings.projects_root / slug / "worktrees"
