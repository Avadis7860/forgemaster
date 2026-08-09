"""update_proposals — le pont entre un tour du canal et la proposition qu'il fait naître en base.

Deux modules refusent de se connaître, et ce module existe pour ça :

- `update_channel` est le module **réseau** ; sa frontière avec l'hors-ligne est écrite dans
  `docs/specs/update-channel-manifest.md`. Il ne gagne aucun import de `db` : un tirage HTTP qui ouvrirait
  la base ferait dépendre le verdict — pur et testable à la table — de l'état d'un fichier SQLite.
- `db.proposals` est une couche **données** ; lui faire importer `update_channel` pour savoir ce qu'est un
  verdict inverserait la dépendance et mettrait du réseau sous une table.

La composition vit donc ici, à l'étage où elle ne crée aucune arête douteuse — même motif que le `build_sha`
de `read_verdict`, composé par l'appelant plutôt que relu par le module.

**Ce que ce pont fait, et le peu qu'il fait.** Il note qu'une édition **vérifiée et plus récente** a été
annoncée. Il ne propose rien à l'écran, ne recueille aucun choix, n'applique jamais : le daemon recueille un
consentement, il ne se l'accorde pas (décision `propose-consent-preserve` §1).

**Un seul verdict fait naître une proposition : `available`.** Les six autres n'en font naître aucune, et
chacun pour une raison qui lui est propre — `up-to-date` n'a rien à proposer ; `unverified` est ignoré côté
produit **par doctrine** (on n'agit pas sur des octets non authentifiés) ; `cannot-situate` est un aveu, pas
une divergence, et proposer sur un aveu accuserait ; `never` / `unreachable` / `no-trust-root` ne savent
rien. Écrire la règle comme une liste d'exclusions serait la même règle un jour, et une autre le jour où un
huitième état apparaîtrait : on inclut, on n'exclut pas.
"""
from __future__ import annotations

import logging
import sqlite3

from forgemaster import build_provenance, update_channel
from forgemaster.config import Settings
from forgemaster.db import proposals, store

_LOG = logging.getLogger("forgemaster")

#: Le seul verdict qui a quelque chose à proposer : une édition vérifiée dont on descend réellement.
PROPOSABLE = "available"


def note_verdict(settings: Settings, verdict: dict, *, now: str | None = None) -> dict | None:
    """Note en base la proposition portée par `verdict`, s'il y en a une. Rend la ligne, sinon `None`.

    **PURE de réseau** : le verdict est passé, jamais retiré ici. C'est ce qui rend cette fonction testable
    à la table, et ce qui permet de la rejouer sur un verdict lu du cache sans rien contacter.

    **Best-effort de bout en bout** : ouvrir la base peut échouer (schéma trop neuf sur une instance qu'on
    vient de rétrograder — le daemon démarre quand même, exprès, pour le DIRE). Une proposition qu'on ne
    sait pas noter ne doit pas faire tomber la boucle de fond : l'annonce repassera au tour suivant."""
    if verdict.get("state") != PROPOSABLE:
        return None
    annonce = verdict.get("announced") or {}
    version, sha = annonce.get("version"), annonce.get("sha")
    if not version or not sha:
        # `available` sans version ni SHA n'est pas censé exister (`situate` les lit pour conclure). Si ça
        # arrive, on le DIT plutôt que d'écrire une ligne dont la clé serait vide.
        _LOG.warning("annonce proposable sans version ni SHA — proposition non notée : %r", annonce)
        return None
    try:
        conn = store.open_db(settings)
    except (store.SchemaTooNew, sqlite3.Error, OSError) as exc:
        _LOG.warning("proposition de MAJ non notée (base inaccessible) : %s", exc)
        return None
    try:
        return proposals.enregistre(conn, version=version, sha=sha, now=now)
    finally:
        conn.close()


def tour(settings: Settings, *, build_sha: str | None = None) -> dict:
    """Un tour complet du canal : tirage, puis note de la proposition. Rend l'état écrit par le tirage.

    C'est la forme qu'attend `run_channel_poll(refresher=...)` — la boucle de fond appelle ceci au lieu de
    `update_channel.refresh`, et gagne la mémoire de ce qu'elle a annoncé sans rien savoir de la base.

    Le `build_sha` par défaut est celui de l'édition qui tourne (`build_provenance.read_stamp`, qui **ne
    lève jamais** et rend `None` sur un checkout éditable) : sans lui, `situate` ne peut pas conclure et
    **aucun** verdict ne serait jamais `available`. Il est lu ici, à l'étage de composition, et pas dans
    `update_channel` — pour la raison écrite dans `read_verdict`."""
    etat = update_channel.refresh(settings)
    sha = build_sha if build_sha is not None else build_provenance.read_stamp()["sha"]
    note_verdict(settings, update_channel.verdict(etat, build_sha=sha))
    return etat
