"""update — le verbe `forgemaster update apply` : poser un wheel LOCAL et revenir tout seul si ça ne sert pas.

Ce module est la **moitié locale et hors-ligne** d'un canal de mise à jour : il prend un `.whl` qu'on lui
désigne, rien d'autre. **Aucune** détection de version disponible, **aucun** manifeste servi, **aucune**
signature, **aucun** réseau. Le canal (proposer, consentir) reste hors périmètre — écrire une décision n'est
pas ouvrir un canal.

Le travail réel est fait par `apply_update.py`, script autonome (stdlib pure, zéro import `forgemaster`) que
ce verbe **copie** puis lance dans sa **propre unité transitoire**, sous le `python3` du système : une MAJ
qui bascule le venv et redémarre le service ne doit pas mourir parce que le shell qui l'a lancée a été fermé
— ni, surtout, parce que c'est le daemon lui-même qui l'a lancée et qu'on vient de l'arrêter. Ce second cas
n'est pas théorique : il a été mesuré, et il exige plus qu'un détachement de session (cf.
`_echappement_cgroup`). Le verbe, lui, **suit le journal** et rend le code de sortie : détaché ne veut pas
dire aveugle.

Ce module ne porte donc que ce qu'un script autonome ne peut pas porter : le **refus fail-closed**, avant
que quoi que ce soit ne bouge.

Six refus, tous explicites, jamais un devinage :

- **pas de `systemd-run`** → sans lui l'applicateur ne peut pas sortir du cgroup de son lanceur, donc il se
  ferait tuer par l'arrêt qu'il émet. Livré avec systemd, dont le verbe dépend déjà ;
- **pas d'unité systemd** → la bascule exige un service gérable. On ne va pas inventer une façon de
  redémarrer le forgemaster de quelqu'un ;
- **une unité qui lance un venv EN DUR** → c'est l'état de toute installation antérieure au bleu/vert. Elle
  n'est pas cassée, elle est *non migrée* : le message dit la commande unique qui la migre. Réécrire l'unité
  sous les pieds de l'utilisateur serait pire que refuser ;
- **portée système sans être root** → `systemctl` échouerait au milieu, service arrêté. Refuser avant ;
- **du travail non commité dans `projects_root`** → cette racine n'entre PAS dans l'instantané. Le motif
  (« git fait autorité ») ne vaut que là où il *y a* une autorité : le refus la vérifie au lieu de la
  supposer. Seul le non commité bloque — « aucun remote » est un cas normal du produit distribué, et refuser
  dessus interdirait toute mise à jour à qui n'en veut pas ;
- **un dispatch en cours** → l'arrêt du service tue le worker, et le boot suivant le réape `killed` : la
  task retombe `todo` et les jetons déjà dépensés sont perdus. Le consentement porte sur *appliquer la MAJ*,
  jamais sur *perdre le travail en cours*.

Ce module porte aussi la **lecture d'état d'un run** (`run_state`, `list_runs`) — et elle se fait entièrement
**sur le disque**. C'est ce qui permet à la route HTTP d'exister : le processus qui répond au `GET` d'après
n'est ni celui qui a reçu le `POST`, ni même le même binaire. Rien de cet état ne peut donc vivre en mémoire.

Et il porte l'**aire de dépôt** (`stage_wheel`, `list_wheels`, `prune_wheels`). Elle existe pour une raison
sèche : `apply` ne pose que le fichier qu'on lui désigne, et **HTTP n'a pas de système de fichiers**. La CLI,
elle, en a un — d'où une asymétrie voulue : on **dépose** par la route, on ne dépose pas en CLI, et la CLI
gagne seulement de quoi **voir** ce qui est déposé et ce que la rétention garde.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from forgemaster import apply_update
from forgemaster.config import Settings
from forgemaster.content.upload import UploadRejected, UploadTooLarge, UploadTypeRejected
from forgemaster.projects import authority as auth
from forgemaster.service import stable_link, unit_path

APPLY = "apply.py"
UPDATES = "updates"
RUNNER = "systemd-run"     # le seul échappement au cgroup du lanceur, cf. `_echappement_cgroup`
FOLLOW_TIMEOUT = 900.0     # 15 min : venv neuf + pip install + deux redémarrages, large
REGISTER_TIMEOUT = 30.0    # l'ENREGISTREMENT de l'unité (un aller-retour D-Bus), pas le travail qu'elle fait
ACTIVE_TIMEOUT = 10.0      # `systemctl is-active` sur une unité transitoire : une question, pas un travail

RUN_META = "run.json"        # l'INTENTION du lanceur, écrite avant l'effet (le mode ne se dérive pas)
RUN_RESULT = "result.json"   # le VERDICT, écrit par l'applicateur à la toute fin
RUN_JOURNAL = "journal.log"  # ce que l'applicateur raconte pendant qu'il travaille
RUN_LAUNCH = "launch.log"    # ce que l'UNITÉ capture — donc ce qui reste des pannes les plus précoces
RUN_STAMP = "%Y-%m-%dT%H-%M-%SZ"
# Un identifiant de run qui arrive du réseau n'est pas un nom de dossier : il se valide par sa FORME avant
# de toucher au disque. Seconde garde (confinement du chemin résolu) dans `run_dir_for` — deux, parce
# qu'aucune des deux seule ne suffit (une forme valide peut être un lien symbolique).
RUN_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")
MAX_RUNS = 50                # borne DITE (`truncated`), jamais un cap silencieux

WHEELS = "wheels"            # l'aire de dépôt : le système de fichiers que HTTP n'a pas
DEPOT_META = "deposit.json"  # ce qu'on a mesuré EN recevant (taille, sha256) — ne se re-dérive pas après
# `KEEP_WHEELS` ne dérive PAS de `ROLLBACK_DEPTH`, et c'est délibéré : un wheel déposé n'est pas un barreau
# de l'échelle de retour. Une fois posé, c'est le VENV qui porte le binaire ; le fichier ne sert plus qu'à
# reposer ou comparer. Le coupler à la profondeur de retour serait une fausse parenté.
KEEP_WHEELS = 3
# Mesuré, pas choisi au doigt — et la mesure a demandé deux passes : un wheel bâti d'un arbre propre pèse
# **1,7 Mo** (front vendoré compris), celui du checkout principal **4,7 Mo**, l'écart venant d'un résidu de
# staging embarqué par accident (fiché côté vault, hors de ce module). La borne couvre les deux avec un
# ordre de grandeur de marge — ce canal reçoit un artefact, il ne transfère pas des données.
WHEEL_MAX_BYTES = 64 * 1024 * 1024
WHEEL_CHUNK = 1024 * 1024


class UpdateRefused(Exception):
    """Refus fail-closed. Levée AVANT tout effet — l'instance est intacte quand elle sort."""


def preflight(settings: Settings, *, wheel: str, unit: str | None, scope: str,
              authority: list[dict] | None = None, in_flight: list[dict] | None = None,
              sessions: list[str] | None = None) -> dict:
    """Vérifie tout ce qui doit l'être avant que la moindre chose bouge, et rend le plan (chemins + URL de
    sonde). Lève `UpdateRefused` avec ce qu'il faut faire — jamais un « impossible » nu.

    `authority` (verdict par projet, `projects.authority.survey`), `in_flight` (jobs `running`,
    `dispatch.jobs.running`) et `sessions` (shells PTY vivants) sont **calculés par l'appelant** et passés
    ici : ce module ne va pas chercher une connexion DB ni un registre tout seul (injection explicite), et un
    preflight ne doit rien ouvrir en écriture avant d'avoir le droit de refuser. `sessions` n'est connu que
    du daemon — la CLI est un autre processus et ne le voit pas ; il ne **bloque** donc rien, il se **dit**.
    """
    whl = Path(wheel).expanduser()
    if not whl.is_file():
        raise UpdateRefused(f"wheel introuvable : {whl}")
    if whl.suffix != ".whl":
        raise UpdateRefused(f"{whl.name} n'est pas un wheel (.whl attendu) — aucun réseau, aucune "
                            f"résolution : ce verbe ne pose que le fichier qu'on lui désigne")
    socle = _preflight_service(settings, unit=unit, scope=scope)
    _refuse_uncommitted_work(authority or [], geste="cette MAJ")
    _refuse_busy_dispatch(in_flight or [], geste="cette MAJ")
    return {**socle, "wheel": whl, "authority": authority or [], "sessions": sessions or []}


def _preflight_service(settings: Settings, *, unit: str | None, scope: str) -> dict:
    """Ce que l'aller ET le retour vérifient tous les deux : le lanceur, la portée, l'unité, le lien stable,
    et que l'unité passe **par** ce lien. Extrait de `preflight` le 2026-08-06 en écrivant
    `preflight_rollback` — dupliquer ces refus aurait produit deux jeux de messages qui divergent, alors que
    c'est exactement le même invariant de déploiement qui est en jeu."""
    if shutil.which(RUNNER) is None:
        raise UpdateRefused(
            f"`{RUNNER}` introuvable — sans lui l'applicateur resterait dans le cgroup de son lanceur et le "
            f"`systemctl stop` qu'il émet le tuerait AVANT qu'il ait posé quoi que ce soit. Il est livré "
            f"avec systemd, dont ce verbe dépend déjà : s'il manque, il n'y avait de toute façon aucun "
            f"service à piloter.")
    if scope == "system" and os.geteuid() != 0:
        raise UpdateRefused("portée système demandée sans être root — `systemctl` échouerait en plein "
                            "milieu, service arrêté. Relance en root, ou installe le service en portée "
                            "utilisateur (`forgemaster install-service`).")

    up = Path(unit).expanduser() if unit else unit_path(scope)
    if not up.is_file():
        raise UpdateRefused(f"aucune unité systemd à {up} — la bascule bleu/vert exige un service gérable. "
                            f"Installe-le : `forgemaster install-service`.")
    exec_bin, host, port = parse_exec_start(up.read_text(encoding="utf-8"))

    link = stable_link(settings)
    if not link.is_symlink():
        raise UpdateRefused(f"{link} n'est pas un lien stable vers le venv actif. Relance "
                            f"`forgemaster install-service` (il le pose), puis `systemctl daemon-reload`.")
    if not exec_bin.startswith(str(link) + os.sep):
        raise UpdateRefused(
            f"l'unité {up} lance {exec_bin} — un venv EN DUR. La bascule bleu/vert remplace un lien, pas "
            f"une installation : tant que l'unité ne passe pas par {link}, la MAJ n'aurait aucun effet sur "
            f"le service. Migre-la : `forgemaster install-service` puis `systemctl daemon-reload`.")

    return {"unit": up, "link": link, "venv": link.resolve(),
            "base_url": f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}",
            "scope": scope, "home": settings.home, "projects_root": settings.projects_root,
            # Ce qu'on saura dire de l'INTERFACE, calculé ici pour être DIT avant le geste : le plan le
            # porte, `describe` l'affiche, donc le panneau le montre dans sa prévisualisation. Une
            # dégradation annoncée n'est pas un cap silencieux — une dégradation tue en serait un.
            "ui_check": apply_update.annonce_verification(settings.home)}


def _refuse_uncommitted_work(verdicts: list[dict], *, geste: str) -> None:
    """Le refus d'autorité : du travail que ni la MAJ ni le retour arrière ne protègent. `projects_root` est
    hors instantané — le motif « git fait autorité » n'est vrai que là où il Y A une autorité. On ne bloque
    QUE sur du non-commité : « aucun remote » est un cas normal du produit distribué, et refuser dessus
    interdirait toute MAJ.

    Porté par les deux gestes depuis le 2026-08-06 : revenir en arrière pendant qu'un travail non commité
    vit dans un worktree est exactement le geste à refuser — et c'était l'un des deux acquis que la phase 1a
    laissait sans câblage côté retour."""
    refused = auth.blocking(verdicts)
    if refused:
        details = "\n  ".join(f"{v['slug']} — {v['detail']}" for v in refused)
        raise UpdateRefused(
            f"{len(refused)} projet(s) portent du travail NON COMMITÉ, et `projects_root` n'entre pas dans "
            f"l'instantané : si {geste} tourne mal, ce travail-là ne reviendra pas.\n  {details}\n"
            f"  → commite (ou remise) dans chaque worktree cité, puis relance")


def _refuse_busy_dispatch(jobs: list[dict], *, geste: str) -> None:
    """Le refus de **non-interruption** : un worker tourne, et le geste demandé arrête le service.

    Le motif est mesuré, pas moral. L'arrêt tue le worker in-process ; au boot suivant
    `dispatch.reconcile.reconcile_orphans` le trouve `running` sans verdict, le marque `killed`, et sa task
    retombe `todo` — les jetons déjà dépensés sont perdus, et `record_finish` (gardé `WHERE status='running'`)
    ne pourra plus rien rattraper. Le même module documente ce footgun comme **déjà vécu**.

    Le consentement de l'utilisateur porte sur *appliquer la MAJ*, jamais sur *perdre le travail en cours*
    (Décision B §1, non-interruption fail-closed). Il vit ici, dans le préflight partagé, et non dans la
    route : la CLI arrête exactement le même service, elle doit hériter du même refus."""
    if not jobs:
        return
    details = "\n  ".join(
        f"{j.get('project', '?')}/{j.get('feature', '?')}/{j.get('task', '?')} — job {j.get('job_id', '?')} "
        f"depuis {j.get('started_at', '?')}" for j in jobs)
    raise UpdateRefused(
        f"{len(jobs)} run(s) de dispatch en cours, et {geste} arrête le service qui les porte : ils seraient "
        f"tués en vol, leur task retomberait `todo` et les jetons déjà dépensés seraient perdus.\n"
        f"  {details}\n"
        f"  → attends la fin, ou `forgemaster abort <projet>` pour les arrêter proprement, puis relance")


def preflight_rollback(settings: Settings, *, snapshot: str | None, unit: str | None, scope: str,
                       authority: list[dict] | None = None, in_flight: list[dict] | None = None,
                       sessions: list[str] | None = None) -> dict:
    """Le préflight du retour **volontaire**. Même socle de service que l'aller, même refus d'autorité, plus
    la **résolution de la cible** : quel instantané remettre, et vers quel venv rebasculer.

    La correspondance instantané ↔ binaire se **dérive** (égalité de schéma), elle ne se stocke pas : aucun
    état nouveau, aucune ligne de plus au manifeste, et le garde vaut aussi pour les instantanés déjà pris.
    Sans référence, la cible par défaut est le plus récent instantané que la phase 1 marque `restaurable` —
    le seul état qui ramène binaire **et** données."""
    from forgemaster import snapshot as snap_mod

    socle = _preflight_service(settings, unit=unit, scope=scope)
    _refuse_uncommitted_work(authority or [], geste="ce retour arrière")
    _refuse_busy_dispatch(in_flight or [], geste="ce retour arrière")

    snaps = snap_mod.list_snapshots(settings)
    if not snaps:
        raise UpdateRefused(_aucun_instantane(settings))

    from forgemaster import restore

    actuel = socle["venv"].resolve()
    lu_courant = restore.python_schema(socle["venv"] / "bin" / "python")

    if snapshot:
        cible = next((s for s in snaps if s["name"] == snapshot or s["path"] == snapshot), None)
        if cible is None:
            connus = ", ".join(s["name"] for s in snaps[:5])
            raise UpdateRefused(f"instantané inconnu : {snapshot}. Les plus récents : {connus} "
                                f"(`forgemaster snapshot list` les classe avec leur état)")
        venv, refus = _cible_utilisable(settings, cible, actuel=actuel, lu_courant=lu_courant)
        if venv is None:
            raise UpdateRefused(f"{cible['name']} : {refus}\n{_PISTES}")
    else:
        cible, venv, motif = _resolution_cible(settings, snaps=snaps, actuel=actuel,
                                               lu_courant=lu_courant)
        if venv is None or cible is None:
            raise UpdateRefused(motif)

    return {**socle, "snapshot": Path(cible["path"]), "snapshot_name": cible["name"],
            "target_venv": venv, "authority": authority or [], "sessions": sessions or []}


def _resolution_cible(settings: Settings, *, snaps: list[dict], actuel: Path,
                      lu_courant: int | None) -> tuple[dict | None, Path | None, str]:
    """`(cible, venv, motif)` — la première prise qui ramène **vraiment** en arrière, ou le motif agrégé du
    refus. Le motif est vide quand une cible est trouvée.

    On PARCOURT, on ne prend pas le premier `restaurable` : le plus récent est souvent l'instantané de
    sûreté d'un retour arrière déjà fait, qui ramène vers la version qu'on venait de quitter. Chercher la
    première cible qui ramène VRAIMENT en arrière évite ce va-et-vient.

    Extrait de `preflight_rollback` le 2026-08-07 en écrivant `aptitude` — qui pose exactement la même
    question sans avoir le droit de lever. Le laisser inline aurait produit deux parcours, donc deux
    réponses possibles à *« vers quoi reviendrait-on ? »* : c'est le défaut que la 2a‴ a réparé entre
    `snapshot list` et le verbe, et il n'y a aucune raison de le ré-ouvrir par une troisième marche."""
    if not snaps:
        return None, None, _aucun_instantane(settings)
    motifs: list[str] = []
    for info in snaps:
        venv, refus = _cible_utilisable(settings, info, actuel=actuel, lu_courant=lu_courant)
        if venv is not None:
            return info, venv, ""
        motifs.append(f"{info['name']} — {refus}")
    detail = "\n  ".join(motifs[:5])
    return None, None, f"aucun instantané ne ramènerait en arrière :\n  {detail}\n{_PISTES}"


def _aucun_instantane(settings: Settings) -> str:
    """Le seul cas où il n'y a même pas de motif à agréger. Une phrase, deux lecteurs (le verbe qui lève,
    l'aptitude qui rend un état) — la dupliquer les ferait diverger au premier remaniement."""
    from forgemaster import snapshot as snap_mod
    return (f"aucun instantané sous {snap_mod.snapshots_dir(settings)} — il n'y a rien vers quoi revenir. "
            f"Un instantané est pris automatiquement avant chaque `forgemaster update apply`.")


_PISTES = ("  → `forgemaster snapshot list` dit pour chacun ce qu'il ramènerait vraiment\n"
           "  → `forgemaster snapshot restore <instantané>` remet les DONNÉES seules, en le disant\n"
           "  → `forgemaster update apply --wheel <fichier>` est le verbe qui va, lui, en AVANT")


def _cible_utilisable(settings: Settings, info: dict, *, actuel: Path,
                      lu_courant: int | None) -> tuple[Path | None, str]:
    """`(venv cible, motif de refus)` — le motif est vide quand cette cible ramène **vraiment** en arrière.

    Cinq façons de ne pas en être une. La quatrième s'est révélée en revue : après un retour arrière,
    l'instantané de sûreté est le plus récent et il est `restaurable` — le viser ferait repartir **en avant**
    vers la version qu'on vient de quitter. « Revenir » a un sens, et ce n'est pas « bouger ».

    La cinquième dit la MÊME chose, mais là où la quatrième est aveugle : elle compare les schémas, donc
    elle est muette quand rien n'a migré — et c'est exactement le cas que le départage du 2026-08-07 a
    rendu atteignable. Mesuré à la table en écrivant ce correctif : sans elle, un `update rollback` rejoué
    après un retour NON migrant repartait vers le binaire qu'on venait de quitter. Ce que le journal dit et
    que le schéma ne dit pas : cet instantané est né d'un `rollback`."""
    from forgemaster import restore

    if not info["valid"]:
        return None, f"instantané invalide — {info['reason']}"
    if info.get("state") != "restaurable":
        return None, (f"il est `{info.get('state', 'inconnu')}`, pas `restaurable` — "
                      f"{info.get('state_reason', 'état non mesuré')}")
    if info.get("safety_of_rollback"):
        return None, ("c'est la prise de SÛRETÉ d'un retour arrière déjà fait — le viser ramènerait à "
                      "l'état d'avant CE retour, donc à la version qu'on vient de quitter : ce serait "
                      "avancer")
    venv = _venv_pour(settings, Path(info["path"]))
    if venv is None:
        return None, ("il se dit `restaurable` mais son venv n'a pas été retrouvé — la liste et la "
                      "résolution ne voient pas le même disque, on ne devine pas")
    if venv.resolve() == actuel:
        # Depuis le départage (2026-08-07) ce refus ne peut plus être un artefact de l'ordre par récence :
        # quand un run nomme cet instantané, la cible est le binaire qui tournait alors, et « déjà actif »
        # veut vraiment dire « on y est déjà ». Quand PERSONNE ne le nomme, on n'a que l'ordre — et le dire
        # vaut mieux qu'un refus qui a l'air arbitraire.
        sans_journal = ("" if info.get("named_by_run") else
                        ", et aucun journal de MAJ ne dit quel binaire tournait quand il a été pris — "
                        "il a été pris à la main, ou son run a été effacé")
        return None, (f"il correspond au venv DÉJÀ actif ({venv.name}) — il n'y a nulle part où "
                      f"revenir{sans_journal}")
    lu_cible = restore.python_schema(venv / "bin" / "python")
    if lu_courant is not None and lu_cible is not None and lu_cible > lu_courant:
        return None, (f"son binaire lit le schéma {lu_cible} et le tien lit le {lu_courant} : ce serait "
                      f"aller EN AVANT, pas revenir")
    return venv, ""


def _venv_pour(settings: Settings, snapshot_dir: Path) -> Path | None:
    """Le venv dont le forgemaster lit **exactement** le schéma de cet instantané. Égalité, pas « au moins » :
    un binaire qui lit plus loin remettrait les données puis migrerait la base en avant — l'état que la
    phase 1 nomme `données seules`, et qui n'est pas un retour arrière.

    Le **choix** ne se fait pas ici : il vit chez `snapshot.venv_for_schema`, seul endroit qui énumère les
    candidats, les ordonne et les **apparie**. Cette marche parcourait `<home>/venvs` de son côté, comme
    `_restorability` du sien — deux marches qui choisissent séparément finissent par ne pas choisir pareil,
    et le troisième refus de `_cible_utilisable` (« la liste et la résolution ne voient pas le même disque »)
    est précisément ce que ça produisait. Elles lisent désormais la même liste, qui inclut le venv
    d'**origine**, hors `<home>/venvs` : sans lui, le PREMIER saut d'une install fraîche était sans retour.

    L'instantané est passé au résolveur, et pas seulement son schéma : c'est ce qui permet de retenir le
    binaire qui tournait **quand celui-ci a été pris**, plutôt que n'importe lequel qui lit le même
    schéma. Après une MAJ non migrante il y en a deux, et le plus récent est le venv qu'on cherche
    justement à quitter."""
    from forgemaster import restore
    from forgemaster import snapshot as snap_mod

    manifest = json.loads((snapshot_dir / snap_mod.MANIFEST).read_text(encoding="utf-8"))
    voulu = restore.snapshot_schema(snapshot_dir, manifest)
    if voulu is None:
        return None
    return snap_mod.venv_for_schema(settings, voulu, snapshot_dir=snapshot_dir)


# --- l'APTITUDE : ce que l'instance sait faire, dit AVANT qu'on le demande ------------------------------
#
# Deux questions distinctes, et les confondre est le piège de cette surface :
#
#   APTITUDE (ici)      — STRUCTUREL : le lanceur, la portée, l'unité, le lien stable, l'unité qui passe
#                         PAR ce lien, et l'existence d'une cible qui ramènerait vraiment en arrière.
#                         « Cette instance sait-elle revenir ? » Ça ne change que par un ACTE.
#   DISPONIBILITÉ       — TRANSITOIRE : du travail non commité, un dispatch en vol. « Peut-elle le faire
#   (les préflights)      MAINTENANT ? » Ça reste dans les préflights, et ça n'entre pas ici : un worker
#                         qui tourne n'est pas « je ne sais pas revenir », c'est « pas maintenant ».
#                         L'afficher au repos comme une aptitude serait un mensonge d'une autre espèce —
#                         et il vieillirait en secondes sur une page qu'on ne relit pas.

def aptitude(settings: Settings, *, unit: str | None = None, scope: str = "user") -> dict:
    """Ce que cette instance sait faire — **sans jamais lever**. Un refus est un ÉTAT, pas une erreur : le
    409 reste la réponse de `/plan`, qui prévisualise une ACTION.

    Trois lectures partagent ce cœur (la route en 200 toujours, `forgemaster update aptitude` en rc 0
    toujours, le panneau au repos) et aucune n'a le droit d'en avoir une variante : c'est la même règle que
    `_preflight_service`, extrait pour que l'aller et le retour ne se mettent pas à refuser avec deux jeux
    de messages qui divergent.

    **`reversible.ok` vaut `None` quand le socle refuse, jamais `False`.** `_preflight_service` est ce qui
    donne le venv courant (`socle["venv"]`) ; sans lui on ne peut pas mesurer vers quoi on reviendrait, et
    « je n'ai pas pu mesurer » n'est pas « non ». C'est l'idiome que le module tient déjà ailleurs :
    `impact: null` sur un run sans verdict, l'état `unknown` de `run_state`, `restore.python_schema` qui
    rend `None`. Répondre `False` affirmerait une mesure non faite — et ferait afficher deux fois le même
    refus, celui du socle.

    Pas de champ `remedy` : les cinq refus de `_preflight_service` **nomment déjà** la commande qui répare
    (`forgemaster install-service`, `systemctl daemon-reload`). Un second champ obligerait à les ré-écrire
    ailleurs, donc à les laisser diverger.

    `target` ne porte pas de numéro de schéma : il ne veut rien dire pour qui lit cette réponse dans un
    panneau, et le lire coûterait une sonde de plus. *Vers quoi* on reviendrait, c'est un instantané et un
    binaire.

    **`OSError` est rattrapée, et ce n'est pas de la superstition** : `_preflight_service` lit l'unité
    (`up.read_text`) APRÈS avoir vérifié qu'elle existe, donc une unité présente mais illisible — droits,
    contenu non-UTF-8 — remonte une `PermissionError` / `UnicodeDecodeError` et non un `UpdateRefused`.
    Mesuré, pas supposé : sans ce rattrapage, `GET /api/update/aptitude` rendait **500** là où il promet
    200, et « ne lève jamais » était une promesse plus forte que le code. On ne rattrape **que** ce qu'on
    sait nommer : un `except Exception` avalerait les vraies pannes, ce que ce module refuse partout
    ailleurs (`_restore` rend `False` plutôt que d'avaler)."""
    try:
        socle = _preflight_service(settings, unit=unit, scope=scope)
    except UpdateRefused as exc:
        return _sans_socle(str(exc))
    except (OSError, UnicodeDecodeError) as exc:
        ou = Path(unit).expanduser() if unit else unit_path(scope)
        return _sans_socle(f"l'unité {ou} existe mais ne se lit pas ({exc.__class__.__name__}) — on ne "
                           f"peut donc rien dire de ce que cette instance sait faire. Vérifie ses droits "
                           f"de lecture, puis relance.")

    from forgemaster import restore
    from forgemaster import snapshot as snap_mod

    deployable = {"ok": True,
                  "reason": f"l'unité {socle['unit']} lance le lien stable {socle['link']} : la bascule "
                            f"bleu/vert a prise sur ce service"}
    actuel = socle["venv"].resolve()
    lu_courant = restore.python_schema(socle["venv"] / "bin" / "python")
    cible, venv, motif = _resolution_cible(settings, snaps=snap_mod.list_snapshots(settings),
                                           actuel=actuel, lu_courant=lu_courant)
    if cible is None or venv is None:
        return {"deployable": deployable,
                "reversible": {"ok": False, "target": None, "reason": motif}}
    return {"deployable": deployable,
            "reversible": {"ok": True, "reason": "",
                           "target": {"snapshot": cible["name"], "path": cible["path"],
                                      "venv": str(venv)}}}


def _sans_socle(motif: str) -> dict:
    """La réponse quand le socle de déploiement ne tient pas, quelle qu'en soit la cause.

    `reversible.ok` vaut `None` — **pas** `False` : c'est le socle qui donne le venv courant, donc sans lui
    rien n'a été mesuré. Une seule forme pour tous les cas, sinon la surface aurait à distinguer des refus
    qui disent la même chose."""
    return {"deployable": {"ok": False, "reason": motif},
            "reversible": {"ok": None, "target": None,
                           "reason": "indéterminé tant que le socle de déploiement n'est pas en place — "
                                     "sans lien stable ni unité qui passe par lui, il n'y a pas de "
                                     "« binaire actif » depuis lequel mesurer un retour. Ce n'est pas "
                                     "« non » : c'est une question qui n'a pas encore de sens ici."}}


def parse_exec_start(unit_text: str) -> tuple[str, str, int]:
    """Extrait `(binaire, host, port)` de l'`ExecStart` de l'unité. L'unité est la SEULE vérité sur le bind
    du service — le déduire de `forgemaster.env` ou d'un défaut sonderait une autre instance que celle qu'on
    vient de redémarrer, et conclurait au vert sur la mauvaise."""
    lines = [ln for ln in unit_text.splitlines() if ln.strip().startswith("ExecStart=")]
    if not lines:
        raise UpdateRefused("l'unité n'a pas d'`ExecStart=` — illisible, on ne devine pas")
    argv = shlex.split(lines[-1].split("=", 1)[1].strip().lstrip("-@+!"))
    host, port = "127.0.0.1", None
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--host":
            host = value
        elif flag == "--port":
            port = value
    if port is None or not port.isdigit():
        raise UpdateRefused(f"impossible de lire le port dans `ExecStart={' '.join(argv)}` — sans lui, "
                            f"aucune vérification en vivant n'est possible, donc aucun retour arrière "
                            f"automatique. Réinstalle l'unité : `forgemaster install-service --port <n>`.")
    return argv[0], host, int(port)


def describe(plan: dict) -> list[str]:
    """Ce qui va se passer, dit avant de le faire (et seul contenu de `--dry-run`)."""
    lines = [
        f"wheel à poser   : {plan['wheel']}",
        f"venv actuel     : {plan['venv']}  (via {plan['link']})",
        f"unité systemd   : {plan['unit']}  (portée {plan['scope']})",
        f"sonde en vivant : {plan['base_url']}/health (readiness : 503 = elle dit pourquoi) puis "
        f"/api/version",
        plan.get("ui_check", ""),
        "déroulé         : venv neuf à côté → sonde en isolation → arrêt + instantané à froid → "
        "bascule du lien → vérification en vivant → retour arrière automatique si elle échoue",
    ]
    lines.extend(_a_savoir(plan))
    return [ln for ln in lines if ln]


def _a_savoir(plan: dict) -> list[str]:
    """Ce qui n'a pas bloqué mais doit être su. Deux familles, et elles n'ont pas le même statut :

    - les **projets hors instantané** — un projet sans remote n'est pas une faute, mais l'utilisateur doit
      savoir que sa seule copie est ici et qu'elle n'entre pas dans l'instantané ;
    - les **shells du terminal web** — ils vivent dans le cgroup du daemon, donc l'arrêt les emporte. Ils ne
      **bloquent** pas : un onglet ouvert n'est pas du travail en cours, et refuser dessus rendrait la MAJ
      impossible à qui laisse un shell ouvert, c'est-à-dire à tout le monde. Ce qui bloque, c'est un
      dispatch (`_refuse_busy_dispatch`), parce que lui coûte des jetons. Le registre n'étant connu que du
      daemon, cette ligne n'apparaît **que** par la route — la CLI ne peut pas la dire, elle ne sait pas."""
    lines: list[str] = []
    noted = [v for v in plan.get("authority") or [] if v["state"] != "clean_pushed"]
    if noted:
        lines.append(f"hors instantané : {plan.get('projects_root', 'projects_root')} — "
                     f"{len(noted)} projet(s) à savoir")
        lines.extend(auth.describe(noted))
    sessions = plan.get("sessions") or []
    if sessions:
        lines.append(f"seront perdus  : {len(sessions)} shell(s) du terminal web ({', '.join(sessions)}) — "
                     f"ils vivent dans le service qu'on arrête ; rien d'écrit n'est perdu, la session l'est")
    return lines


def describe_rollback(plan: dict) -> list[str]:
    """Ce que le retour arrière va faire, dit avant de le faire (et seul contenu de `--dry-run`). Il nomme
    les DEUX gestes et leur ordre : c'est l'unité que la phase 3 rend exécutoire, et la dire ici est ce qui
    permet à quelqu'un de refuser en connaissance de cause."""
    lines = [
        f"instantané      : {plan['snapshot_name']}  ({plan['snapshot']})",
        f"venv cible      : {plan['target_venv']}  (le lien {plan['link']} y sera rebasculé)",
        f"venv actuel     : {plan['venv']}",
        f"unité systemd   : {plan['unit']}  (portée {plan['scope']})",
        f"sonde en vivant : {plan['base_url']}/health (readiness : 503 = elle dit pourquoi) puis "
        f"/api/version",
        plan.get("ui_check", ""),
        "déroulé         : sonde du binaire cible en isolation → arrêt + instantané de SÛRETÉ à froid → "
        "bascule du lien PUIS restauration → vérification en vivant → retour du retour si elle échoue",
    ]
    lines.extend(_a_savoir(plan))
    return [ln for ln in lines if ln]


def spawn(settings: Settings, plan: dict, *, systemctl: str, service: str,
          mode: str = "apply") -> dict:
    """Le **cœur** du lancement : copie `apply.py` dans un dossier de run, écrit l'intention, puis remet le
    tout à sa PROPRE UNITÉ TRANSITOIRE sous le `python3` système. Rend
    `{"run", "unit", "ok", "detail"}` — et **rien d'autre** : aucun `print`, aucune exception qui s'échappe.

    Cette séparation d'avec `launch` n'est pas de l'esthétique : le daemon appelle ce cœur depuis une
    requête HTTP, où un `print` finirait dans le journal du service et où une exception deviendrait un 500
    nu. La spine est le cœur déterministe ; la CLI et le daemon en sont deux **vues**.

    Copié et non lancé depuis le paquet : le script doit survivre au venv qu'il remplace — et lancé hors du
    cgroup de son lanceur (`_echappement_cgroup`) : il doit aussi survivre au SERVICE qu'il arrête.

    **Un seul lanceur pour les deux gestes** : c'est le même applicateur hors-processus, le même dossier de
    run, le même journal et le même `result.json`. Ce que `mode` change tient dans les arguments de cible."""
    now = datetime.now(UTC)
    run_dir = settings.home / UPDATES / now.strftime(RUN_STAMP)
    unite = _unite_transitoire(run_dir)
    # `exist_ok=False` : l'horodatage est à la SECONDE, et depuis la route deux requêtes peuvent tomber dans
    # la même (le handler est synchrone, donc servi par un fil du pool). Avec `exist_ok=True`, la seconde
    # écrasait le `run.json` de la première — l'intention d'un run en vol, perdue — avant d'échouer de toute
    # façon sur un nom d'unité déjà pris. Un second geste dans la même seconde n'est de toute manière pas un
    # geste qu'on veut servir : on le REFUSE, et rien de l'autre n'est touché.
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {"run": run_dir, "unit": unite, "ok": False, "reason": "collision",
                "detail": f"un run porte déjà l'horodatage {run_dir.name} — un autre geste vient de partir "
                          f"dans la même seconde. Rien n'a été touché ; regarde son état avant de relancer."}
    script = run_dir / APPLY
    shutil.copyfile(Path(__file__).with_name("apply_update.py"), script)
    script.chmod(0o755)

    # L'INTENTION, écrite AVANT l'effet — et avant même de savoir si le lancement partira. Le mode ne se
    # dérive de rien : `result.json` ne le porte pas et n'existe qu'à la fin ; le déduire de la prose de
    # `journal.log` serait un analyseur de phrases françaises. Ce qui ne se dérive pas s'écrit, une fois, à
    # l'endroit qui le sait. Un run qui n'a jamais démarré garde donc quand même sa raison d'avoir existé.
    cible = ({"wheel": str(plan["wheel"])} if mode == "apply" else
             {"snapshot": str(plan["snapshot"]), "snapshot_name": plan["snapshot_name"],
              "target_venv": str(plan["target_venv"])})
    _write_json(run_dir / RUN_META,
                {"mode": mode, "unit": unite, "scope": plan["scope"], "service": service,
                 "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), **cible})

    def _non_lance(motif: str) -> dict:
        return {"run": run_dir, "unit": unite, "ok": False, "reason": "runner", "detail": motif}

    try:
        prefixe = _echappement_cgroup(run_dir, scope=plan["scope"], workdir=plan["home"])
    except UpdateRefused as exc:        # `systemd-run` disparu entre le préflight et ici
        return _non_lance(str(exc))
    cmd = [*prefixe, _system_python(), str(script), "--mode", mode,
           "--home", str(plan["home"]), "--link", str(plan["link"]),
           "--run-dir", str(run_dir), "--base-url", plan["base_url"],
           "--service", service, "--systemctl", systemctl, "--scope", plan["scope"]]
    cmd += (["--wheel", str(plan["wheel"])] if mode == "apply" else
            ["--target-venv", str(plan["target_venv"]), "--snapshot", str(plan["snapshot"])])
    # `run` et non `Popen` : `systemd-run` rend la main dès l'unité ENREGISTRÉE, pas à la fin du travail.
    # On lit donc son verdict d'enregistrement — un lancement qui ne part pas cesse d'être silencieux. Avec
    # `Popen`, personne ne le savait : `follow` attendait le quart d'heure entier puis rendait un délai,
    # c'est-à-dire « je ne sais pas » là où le système avait déjà dit « non ».
    #
    # Le `timeout` n'est pas une précaution de style : `Popen` ne bloquait JAMAIS, `run` si. Enregistrer une
    # unité est un aller-retour D-Bus avec le gestionnaire systemd — un gestionnaire coincé ferait attendre
    # ce processus sans fin. Acceptable pour un humain devant un terminal, inacceptable pour la route qui
    # appelle d'ici : une requête qui ne rend jamais la main est pire qu'une requête qui refuse.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,  # noqa: S603
                              check=False, timeout=REGISTER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _non_lance(f"aucune réponse du gestionnaire systemd après {REGISTER_TIMEOUT:.0f} s "
                          f"(enregistrement de l'unité, pas la MAJ elle-même)")
    if proc.returncode != 0:
        return _non_lance((proc.stderr or proc.stdout or "").strip() or f"rc={proc.returncode}")
    # Rien n'est écrit dans `launch.log` ici : c'est l'unité qui y écrit (`StandardOutput=append:`), et
    # l'écraser avec le « Running as unit… » de `systemd-run` effacerait précisément la trace qu'on garde.
    return {"run": run_dir, "unit": unite, "ok": True, "reason": "", "detail": ""}


def launch(settings: Settings, plan: dict, *, systemctl: str, service: str, detach: bool,
           mode: str = "apply") -> int:
    """La **vue CLI** du lancement : `spawn`, puis dire, puis suivre. Rend un code de sortie.

    Un lancement qui n'a pas eu lieu se DIT, et rend un rc — il ne remonte pas en exception. `launch` est
    appelé hors du `try` de `cli_dispatch` (le message « rien n'a été touché » y serait faux : le dossier de
    run existe), donc une `UpdateRefused` qui sortirait d'ici deviendrait une trace nue."""
    quoi = "MAJ" if mode == "apply" else "retour arrière"
    issue = spawn(settings, plan, systemctl=systemctl, service=service, mode=mode)
    if not issue["ok"]:
        # Le motif du chapô suit le `reason` : dire « systemd n'a pas enregistré » sur une COLLISION
        # d'horodatage enverrait chercher la panne au mauvais endroit — et il n'y a pas de panne.
        tete = ("un run porte déjà cet horodatage. Rien n'a bougé"
                if issue["reason"] == "collision"
                else f"`{RUNNER}` n'a pas enregistré l'unité. Rien n'a bougé sur l'instance ; le dossier "
                     f"de run reste pour la trace")
        print(f"✗ {quoi} non lancé(e) — {tete}.\n  {issue['detail']}", file=sys.stderr)
        return 2
    run_dir = issue["run"]
    print(f"{quoi} lancé(e) (unité {issue['unit']}) — journal : {run_dir / RUN_JOURNAL}")
    if detach:
        print("ça continue même si tu fermes ce terminal ; `cat` le journal pour le suivre.")
        return 0
    return follow(run_dir)


def follow(run_dir: Path, *, timeout: float = FOLLOW_TIMEOUT, poll: float = 0.3) -> int:
    """Suit `journal.log` jusqu'à ce que `result.json` apparaisse, et rend le code de sortie du script.
    Un suivi interrompu (délai) ne conclut PAS à l'échec : il dit où regarder. Le script, lui, continue."""
    journal, result = run_dir / RUN_JOURNAL, run_dir / RUN_RESULT
    seen = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if journal.exists():
            text = journal.read_text(encoding="utf-8", errors="replace")
            if len(text) > seen:
                print(text[seen:], end="", flush=True)
                seen = len(text)
        if result.is_file():
            data = json.loads(result.read_text(encoding="utf-8"))
            return int(data.get("rc", 2))
        time.sleep(poll)
    print(f"⚠ suivi interrompu après {timeout:.0f} s — la MAJ continue en arrière-plan. "
          f"Son verdict arrivera dans {result}.")
    return 2


# --- l'état d'un run, lu sur le DISQUE -----------------------------------------------------------------
#
# Tout ce qui suit se relit à froid. C'est la condition d'existence de la surface HTTP : le processus qui
# répond au `GET` d'après n'est ni celui qui a reçu le `POST`, ni même le même binaire — la bascule est
# passée entre les deux. Un registre en mémoire, un `BackgroundTasks`, un dictionnaire sur `app.state` : tous
# rendraient « inconnu » exactement au moment où l'utilisateur attend son verdict.

def runs_dir(settings: Settings) -> Path:
    """Le dossier qui porte tous les runs de MAJ et de retour arrière."""
    return settings.home / UPDATES


def run_dir_for(settings: Settings, run_id: str) -> Path:
    """Le dossier d'un run, par son identifiant, ou `KeyError`. **Deux gardes**, parce qu'aucune ne suffit
    seule : la FORME (un identifiant venu du réseau n'est pas un nom de dossier — `..`, `/`, un chemin
    absolu) puis le CONFINEMENT du chemin résolu sous `<home>/updates` (une forme valide peut être un lien
    symbolique posé là par autre chose)."""
    if not RUN_ID_RE.match(run_id):
        raise KeyError(f"run {run_id!r}")
    base = runs_dir(settings)
    candidat = base / run_id
    try:
        resolu = candidat.resolve(strict=True)
    except OSError as exc:
        raise KeyError(f"run {run_id!r}") from exc
    if not resolu.is_dir() or not resolu.is_relative_to(base.resolve()):
        raise KeyError(f"run {run_id!r}")
    return candidat


def run_state(run_dir: Path, *, is_active: Callable[[str, str], bool] | None = None,
              tail: int = 4000) -> dict:
    """L'état d'un run, tranché **dans cet ordre** :

    | lu sur le disque | état | ce que ça dit |
    |---|---|---|
    | `result.json` présent | `done` (rc 0) / `failed` | le verdict existe, avec son motif |
    | sinon, **sans sonde** | `unknown` | verdict absent, et personne n'a demandé au gestionnaire |
    | sinon, unité **active** | `running` | ça tourne encore, maintenant |
    | sinon, `launch.log`/`journal.log` présents | `interrupted` | parti, jamais conclu |
    | sinon | `never_started` | enregistré, jamais démarré |

    `interrupted` est l'état que le fire-and-forget d'avant ne savait pas dire : il ne restait qu'un silence
    qu'on ne pouvait pas distinguer d'une attente. `unknown` en est le garde-fou : **sans** sonde, on ne peut
    PAS conclure à `interrupted` — ce serait annoncer une mort qu'on n'a pas vérifiée. Un appelant qui
    économise la sonde (`list_runs`) doit donc récolter un aveu, pas une conclusion.

    `is_active` est **injecté** (invariant : toute I/O l'est) et reçoit `(unité, portée)`. `--collect` efface
    l'unité à sa sortie, donc `systemctl` ne distingue pas « terminée » d'« introuvable » — sans conséquence
    ici : l'unité n'est interrogée qu'**après** que `result.json` a été jugé absent, elle ne sert donc qu'à
    séparer `running` d'`interrupted`."""
    meta = _read_json(run_dir / RUN_META) or {}
    result = _read_json(run_dir / RUN_RESULT)
    journal = run_dir / RUN_JOURNAL
    unite = str(meta.get("unit") or _unite_transitoire(run_dir))
    portee = str(meta.get("scope") or "user")
    etat = {"run": run_dir.name, "mode": meta.get("mode"), "scope": portee, "unit": unite,
            "started_at": meta.get("started_at"),
            "target": meta.get("wheel") or meta.get("snapshot_name") or meta.get("snapshot"),
            "journal": _tail(journal, tail),
            # `impact` = le PÉRIMÈTRE de ce qui a bougé, distinct du verdict qui dit ce qui s'est passé.
            # `apply_update` l'écrit exprès pour les cas où le verdict seul laisse la question ouverte
            # (« MAJ refusée — <motif> » ne dit PAS si le service a été touché ; « aucun : le service n'a
            # pas été touché » le dit). Il n'existe qu'avec un verdict, donc `None` partout ailleurs — et
            # `None` se lit « je n'en sais rien », jamais « rien n'a bougé ».
            "impact": None}
    if result is not None:
        rc = int(result.get("rc", 2))
        return {**etat, "state": "done" if rc == 0 else "failed", "rc": rc,
                "verdict": result.get("verdict", ""), "impact": result.get("impact")}
    if is_active is None:
        return {**etat, "state": "unknown", "rc": None,
                "verdict": "verdict pas encore écrit, et l'unité n'a pas été sondée — demande l'adresse de "
                           "ce run pour savoir s'il tourne encore"}
    if is_active(unite, portee):
        return {**etat, "state": "running", "rc": None, "verdict": ""}
    if journal.is_file() or (run_dir / RUN_LAUNCH).is_file():
        return {**etat, "state": "interrupted", "rc": None,
                "verdict": "parti, jamais conclu — l'unité n'est plus là et aucun verdict n'a été écrit"}
    return {**etat, "state": "never_started", "rc": None,
            "verdict": "l'unité n'a jamais démarré — rien n'a bougé sur l'instance"}


def list_runs(settings: Settings, *, is_active: Callable[[str, str], bool] | None = None,
              limit: int = MAX_RUNS) -> dict:
    """Les runs, du plus récent au plus ancien, chacun avec son état. La borne est **dite** (`truncated` +
    `total`) — invariant « jamais de cap silencieux » : une liste tronquée en silence se lit « c'est tout ».

    **Une seule sonde systemd pour toute la liste**, dépensée sur le run sans verdict le plus récent. Sonder
    chaque run coûterait jusqu'à `limit` allers-retours au gestionnaire sur une simple vue de liste, et le
    plafond (`limit × ACTIVE_TIMEOUT`) tomberait sur une requête HTTP le jour où ce gestionnaire est coincé
    — c'est-à-dire le jour où l'on regarde justement cette page.

    Les runs sans verdict plus anciens rendent donc `unknown`, jamais `interrupted` : la liste **avoue** au
    lieu de conclure, et leur adresse propre (`GET /runs/{id}`) sonde, elle.

    Rend aussi `follow_timeout` — la borne au-delà de laquelle le produit LUI-MÊME cesse d'attendre un run
    (`FOLLOW_TIMEOUT`, ce que suit la CLI). Une surface qui dit « elle aurait dû revenir » a besoin de ce
    chiffre, et le recopier chez elle le ferait diverger le jour où il bouge. Même raison que `keep` et
    `max_bytes` sur l'aire de dépôt : **une borne annoncée vient de la borne qui s'applique**."""
    base = runs_dir(settings)
    noms = sorted((p.name for p in base.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)),
                  reverse=True) if base.is_dir() else []
    gardes = noms[:limit]
    vue, sonde = [], is_active
    for nom in gardes:
        run_dir = base / nom
        conclu = (run_dir / RUN_RESULT).is_file()
        vue.append(run_state(run_dir, is_active=None if conclu else sonde, tail=0))
        if not conclu:
            sonde = None                     # budget épuisé : les suivants avouent au lieu de conclure
    return {"runs": vue, "total": len(noms), "truncated": len(noms) > len(gardes),
            "follow_timeout": FOLLOW_TIMEOUT}


def systemd_is_active(systemctl: str = "systemctl") -> Callable[[str, str], bool]:
    """La sonde de production pour `run_state` : `systemctl [--user] is-active <unité>`. Bornée dans le
    temps — une question posée au gestionnaire ne doit jamais faire attendre une requête HTTP."""
    def _probe(unit: str, scope: str) -> bool:
        cmd = [systemctl, *(["--user"] if scope == "user" else []), "is-active", unit]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,  # noqa: S603
                                  check=False, timeout=ACTIVE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return False            # gestionnaire injoignable : on ne SAIT pas qu'elle tourne
        return proc.stdout.strip() == "active"
    return _probe


# --- l'aire de dépôt d'un artefact --------------------------------------------------------------------
#
# `preflight` ne pose que le fichier qu'on lui DÉSIGNE — la CLI a un système de fichiers, elle passe un
# chemin. HTTP n'en a pas : un utilisateur distribué a son wheel dans son navigateur, pas sur le disque de
# son instance. Cette aire est ce chaînon-là, et rien de plus : elle reçoit, elle borne, elle range. Le
# chemin qu'elle rend se repasse tel quel à `apply` — qui, lui, continue d'accepter n'importe quel wheel
# local (le canal servi de la phase 5 en déposera ailleurs ; confiner ici obligerait à ré-ouvrir là-bas).

def wheels_dir(settings: Settings) -> Path:
    """Le dossier qui porte les artefacts déposés par la route."""
    return settings.home / WHEELS


def stage_wheel(settings: Settings, *, filename: str, chunks: Iterable[bytes]) -> dict:
    """Range un artefact reçu **en flux** sous `<home>/wheels/<stamp>/<nom>.whl`, et rend ce qu'il faut pour
    le poser : `{stamp, name, path, size, sha256, pruned}`.

    `chunks` est n'importe quel itérable de `bytes` — ce cœur ne connaît ni HTTP ni `UploadFile` (injection
    explicite), donc il se teste avec une liste. **Quatre gardes, dans cet ordre**, et l'ordre porte du
    sens : un nom traversant se juge AVANT la taille, sinon un fichier hostile de 100 Mo serait rejeté pour
    son poids et on ne saurait jamais qu'il visait `../`.

    | # | garde | rejet |
    |---|---|---|
    | 1 | nom **nu** (ni vide, ni séparateur, ni `..`, ni chemin absolu) | `UploadRejected` → 400 |
    | 2 | extension `.whl`, rien d'autre | `UploadTypeRejected` → 415 |
    | 3 | confinement du chemin résolu sous `<home>/wheels` | `UploadRejected` → 400 |
    | 4 | taille, **pendant** le flux | `UploadTooLarge` → 413 |

    Les trois exceptions sont celles de `content.upload` : les handlers globaux du daemon les mappent déjà
    400/413/415 par la MRO. C'est le point de couture du patron d'upload — on reprend la couture, pas la
    pièce (`write_project_upload` écrit sous `docs/design/`, ce n'est pas le même sujet).

    Écriture **atomique** (`.part` + `os.replace`) : un dépôt à moitié monté n'est jamais listé ni posable.
    Tout échec efface le dossier entier — rien de tronqué ne survit, symétrie avec « jamais de troncature ».
    Deux dépôts dans la même seconde → `UpdateRefused` (409) : l'horodatage est à la seconde et le handler
    est servi par un fil du pool, exactement comme `spawn`."""
    nom = _valide_nom_de_wheel(filename)
    base = wheels_dir(settings)
    stamp = datetime.now(UTC).strftime(RUN_STAMP)
    depot = base / stamp
    cible = depot / nom
    if not cible.resolve().is_relative_to(base.resolve()):        # défense en profondeur (garde 3)
        raise UploadRejected(f"chemin hors de l'aire de dépôt : {filename!r}")
    try:
        depot.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise UpdateRefused(
            f"un dépôt porte déjà l'horodatage {stamp} — un autre artefact vient d'arriver dans la même "
            f"seconde. Rien n'a été touché ; réessaie.") from exc
    part = depot / (nom + ".part")
    total, digest = 0, hashlib.sha256()
    try:
        with part.open("wb") as fh:
            for chunk in chunks:
                total += len(chunk)
                if total > WHEEL_MAX_BYTES:
                    raise UploadTooLarge(
                        f"artefact trop volumineux : plus de {WHEEL_MAX_BYTES} o (borne WHEEL_MAX_BYTES). "
                        f"Aucune troncature — rien n'a été gardé.")
                digest.update(chunk)
                fh.write(chunk)
        if total == 0:
            raise UploadRejected("dépôt vide : il n'y a pas de wheel de zéro octet")
        os.replace(part, cible)
    except BaseException:
        shutil.rmtree(depot, ignore_errors=True)
        raise
    meta = {"name": nom, "size": total, "sha256": digest.hexdigest(),
            "staged_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _write_json(depot / DEPOT_META, meta)
    return {"stamp": stamp, "path": str(cible), "pruned": prune_wheels(settings), **meta}


def _valide_nom_de_wheel(filename: str) -> str:
    """Le nom reçu, validé comme **nom nu** puis comme wheel. Un identifiant qui vient du réseau n'est pas
    un nom de fichier : il se juge par sa forme avant de toucher au disque."""
    nom = filename.strip()
    if not nom:
        raise UploadRejected("nom de fichier vide")
    if nom in (".", "..") or "/" in nom or "\\" in nom or nom != Path(nom).name:
        raise UploadRejected(f"nom de fichier invalide (path-traversal) : {filename!r}")
    # Un octet de contrôle (dont le `\0`) passerait les gardes de forme et ferait lever `open()` — un
    # `ValueError` de la stdlib, rendu tel quel en 400. Le refuser ici, c'est dire ce qui ne va pas plutôt
    # que laisser fuiter « embedded null byte » depuis deux couches plus bas.
    if any(c < " " or c == "\x7f" for c in nom):
        raise UploadRejected(f"nom de fichier invalide (caractère de contrôle) : {filename!r}")
    if Path(nom).suffix.lower() != ".whl":
        raise UploadTypeRejected(
            f"type non autorisé : {Path(nom).suffix or '(sans extension)'}. Cette aire ne reçoit que des "
            f"wheels (.whl) — c'est le seul artefact que `update apply` sait poser.")
    return nom


def list_wheels(settings: Settings, *, keep: int = KEEP_WHEELS) -> dict:
    """Les artefacts déposés, du plus récent au plus ancien, et la politique qui les garde. Tri par **nom**
    (le nom EST l'horodatage) — jamais par mtime, qu'une copie ou une restauration réécrit.

    `sha256` et `size` sont ceux **mesurés à la réception** (`deposit.json`), pas recalculés à la lecture :
    c'est une provenance de dépôt, et la relire à chaque affichage coûterait une relecture complète de
    l'aire. Un dossier posé à la main n'en a pas — il est listé quand même, avec `sha256: null`, parce que
    « je n'ai pas mesuré » n'est pas « il n'y a rien ».

    Rend les DEUX bornes qui régissent l'aire (`keep`, `max_bytes`), pas seulement la rétention : une surface
    qui annoncerait « 64 Mo max » sans les lire ré-écrirait à la main un chiffre qu'elle ne connaît pas —
    leçon déjà apprise par le banc de la 3a·3a, qui épinglait `3` au lieu de lire `keep`."""
    base = wheels_dir(settings)
    stamps = sorted((p.name for p in base.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)),
                    reverse=True) if base.is_dir() else []
    en_vol = wheels_in_use(settings)
    vue = []
    for stamp in stamps:
        whls = sorted((base / stamp).glob("*.whl"))
        if not whls:
            continue                     # dépôt sans artefact (échec effacé, ou main humaine) : rien à dire
        meta = _read_json(base / stamp / DEPOT_META) or {}
        vue.append({"stamp": stamp, "name": whls[0].name, "path": str(whls[0]),
                    "size": meta.get("size", whls[0].stat().st_size), "sha256": meta.get("sha256"),
                    "in_use": str(whls[0]) in en_vol})
    return {"wheels": vue, "total": len(vue), "keep": keep, "max_bytes": WHEEL_MAX_BYTES}


def wheels_in_use(settings: Settings) -> set[str]:
    """Les artefacts qu'un run **sans verdict** nomme encore dans son `run.json`. Une rétention qui ignore
    ses références casse ce qu'elle croit ranger : purger le wheel d'un run en vol le ferait échouer en
    plein pip install. Borné à `MAX_RUNS`, comme la liste des runs."""
    base = runs_dir(settings)
    if not base.is_dir():
        return set()
    runs = sorted((p for p in base.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)), reverse=True)
    return {str(whl) for p in runs[:MAX_RUNS] if not (p / RUN_RESULT).is_file()
            if (whl := (_read_json(p / RUN_META) or {}).get("wheel"))}


def prune_wheels(settings: Settings, *, keep: int = KEEP_WHEELS) -> list[str]:
    """Applique la rétention **à l'écriture** (pas par un timer : une politique qui dépend d'un minuteur ne
    tient pas sur une instance qu'on éteint). Garde les `keep` dépôts les plus récents, et **épargne** ceux
    qu'un run sans verdict nomme — un dépôt épargné ne décale pas la fenêtre, il s'ajoute à ce qui reste.

    Rend les horodatages purgés : l'appelant les **dit**. Une purge muette, c'est un cap silencieux avec un
    autre nom."""
    base = wheels_dir(settings)
    if not base.is_dir():
        return []
    stamps = sorted((p.name for p in base.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)),
                    reverse=True)
    en_vol = wheels_in_use(settings)
    purges = []
    for stamp in stamps[keep:]:
        depot = base / stamp
        if any(str(whl) in en_vol for whl in depot.glob("*.whl")):
            continue
        shutil.rmtree(depot, ignore_errors=True)
        purges.append(stamp)
    return purges


def _read_json(path: Path) -> dict | None:
    """Le contenu d'un JSON de run, ou `None` — présent-mais-illisible se lit comme absent : un fichier
    tronqué par une écriture concurrente ne doit pas faire tomber la lecture d'état de tous les autres."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    """Écriture ATOMIQUE (temporaire + `os.replace`) : un lecteur concurrent voit l'ancien fichier ou le
    nouveau, jamais un demi-fichier. Même forme que `apply_update._write_json`, dont ce module est le
    pendant côté lanceur."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _tail(path: Path, n: int) -> str:
    """Les `n` derniers octets d'un journal, décodés en tolérant les coupures. `n <= 0` → rien (la liste ne
    porte pas les journaux : elle dirait des kilo-octets pour une vue qui n'en affiche aucun)."""
    if n <= 0 or not path.is_file():
        return ""
    try:
        texte = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return texte[-n:]


def _system_python() -> str:
    """Le `python3` du SYSTÈME, jamais celui du venv qu'on est en train de remplacer."""
    return shutil.which("python3") or "/usr/bin/python3"


def _unite_transitoire(run_dir: Path) -> str:
    """Le nom de l'unité qui portera l'applicateur — **dérivé** du dossier de run, jamais posé à côté de
    lui. Un run et son unité ne peuvent donc pas diverger, et depuis un dossier de run on sait quoi
    interroger (`systemctl [--user] status …`) sans mémoire externe. Même doctrine que la correspondance
    instantané ↔ binaire (`preflight_rollback`) : ce qui se dérive ne se stocke pas."""
    return f"forgemaster-update-{run_dir.name}"


def _echappement_cgroup(run_dir: Path, *, scope: str, workdir: Path) -> list[str]:
    """Le préfixe qui sort l'applicateur du cgroup de son lanceur, en le faisant devenir **sa propre unité
    transitoire**.

    Pourquoi il faut sortir, et pourquoi `setsid` ne suffisait pas : `Popen(start_new_session=True)` change
    la **session**, pas le **cgroup**. Lancé par le daemon, l'applicateur restait dans le cgroup de
    `forgemaster.service` — que le `systemctl stop` qu'il émet lui-même vide entièrement (`KillMode` par
    défaut = `control-group`). Mesuré sur vrai systemd le 2026-08-06 : service laissé à terre, aucun
    `result.json` écrit, donc **aucun verdict à retrouver**. Jamais rencontré avant parce que toutes nos
    preuves partaient d'un ssh, c'est-à-dire de dehors.

    **Pourquoi pas `KillMode=process` sur l'unité** (l'autre échappement possible) : le daemon n'a pas qu'un
    enfant. Les shells PTY (`terminal.pty`) et les workers de dispatch (`dispatch.worker`) vivent eux aussi
    dans son cgroup, et `KillMode=process` les orphelinerait **tous**, à chaque arrêt, pour corriger ce seul
    cas. L'unité transitoire ne change le sort que de l'applicateur, à l'endroit qui le concerne.

    `--collect` : l'unité s'efface après sa sortie, y compris en échec — sans quoi un run échoué laisserait
    un nom occupé. `append:` conserve `launch.log`, qui porte ce qui arrive **avant** que l'applicateur
    ouvre son propre `journal.log` : les pannes les plus précoces, celles dont il ne reste rien d'autre."""
    runner = shutil.which(RUNNER)
    if runner is None:                  # normalement déjà refusé au préflight ; ceinture, et honnête
        raise UpdateRefused(f"`{RUNNER}` introuvable au moment de lancer.")
    log = run_dir / "launch.log"
    return [runner, *(["--user"] if scope == "user" else []),
            "--collect", f"--unit={_unite_transitoire(run_dir)}",
            "-p", f"WorkingDirectory={workdir}",
            "-p", f"StandardOutput=append:{log}", "-p", f"StandardError=append:{log}"]


# --- ce que l'appelant doit SAVOIR avant de demander (injecté aux préflights) --------------------------
#
# Ces deux sondes étaient sous l'en-tête « CLI » tant que la CLI était le seul appelant. Elles sont
# remontées ici le 2026-08-06 : le daemon les appelle aussi, et une fonction que deux vues partagent n'est
# pas un détail d'implémentation de l'une d'elles.

def survey_authority(settings: Settings) -> list[dict]:
    """Le verdict d'autorité, ou une liste vide en dégradation honnête. On n'ouvre la base **que si elle
    existe déjà** : un preflight qui refuse ne doit pas avoir créé la base de son refus. Une base illisible
    ne bloque pas non plus — elle rend « je ne sais pas », et ce module ne bloque que sur ce qu'il SAIT."""
    if not settings.db_path.is_file():
        return []
    from forgemaster.db import store
    from forgemaster.git.internal import InternalGit
    try:
        conn = store.connect(settings.db_path)
    except sqlite3.Error:
        return []
    try:
        return auth.survey(conn, settings, InternalGit())
    except sqlite3.Error:
        return []                       # base d'un schéma qu'on ne lit pas : pas un motif de refus
    finally:
        conn.close()


def survey_in_flight(settings: Settings) -> list[dict]:
    """Les runs de dispatch en vol, ou une liste vide en dégradation honnête — **même contrat** que
    `survey_authority` : base absente ou d'un schéma qu'on ne lit pas ⇒ « je ne sais pas », et ce module ne
    bloque que sur ce qu'il SAIT. Un refus qui se déclencherait sur une base illisible refuserait toutes les
    MAJ des instances qu'il faut justement mettre à jour."""
    if not settings.db_path.is_file():
        return []
    from forgemaster.db import store
    from forgemaster.dispatch import jobs
    try:
        conn = store.connect(settings.db_path)
    except sqlite3.Error:
        return []
    try:
        return jobs.running(conn)
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# --- CLI ------------------------------------------------------------------------------------------------

def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster update <action>` — `apply` et `rollback` suivent la MÊME séquence : préflight qui
    refuse avant tout effet, description, puis lancement de l'applicateur détaché. `wheels` est une **vue en
    lecture** sur l'aire de dépôt : on ne dépose pas en CLI (elle a déjà un système de fichiers), mais une
    politique de rétention qui n'existe que dans le code n'est pas une politique déclarée."""
    if args.action == "wheels":
        return _cli_wheels(settings)
    scope = "system" if getattr(args, "system", False) else "user"
    if args.action == "aptitude":
        return _cli_aptitude(settings, unit=args.unit, scope=scope)
    aller = args.action == "apply"
    quoi = "MAJ" if aller else "retour arrière"
    try:
        plan = (preflight(settings, wheel=args.wheel, unit=args.unit, scope=scope,
                          authority=survey_authority(settings),
                          in_flight=survey_in_flight(settings)) if aller else
                preflight_rollback(settings, snapshot=args.snapshot, unit=args.unit, scope=scope,
                                   authority=survey_authority(settings),
                                   in_flight=survey_in_flight(settings)))
    except UpdateRefused as exc:
        print(f"✗ {quoi} refusé(e) — rien n'a été touché.\n  {exc}", file=sys.stderr)
        return 1
    for line in (describe(plan) if aller else describe_rollback(plan)):
        print(line)
    if args.dry_run:
        print("\n(--dry-run : rien n'a été lancé)")
        return 0
    print()
    return launch(settings, plan, systemctl=args.systemctl, service=args.service, detach=args.detach,
                  mode="apply" if aller else "rollback")


def _cli_aptitude(settings: Settings, *, unit: str | None, scope: str) -> int:
    """`forgemaster update aptitude` — ce que cette instance sait faire, **avant** qu'on lui demande quoi
    que ce soit. Parité stricte avec `GET /api/update/aptitude`, même cœur, aucune règle en double.

    **Toujours rc 0**, et c'est la parité exacte du 200 de la route : lire un état n'est pas une panne.
    Même doctrine que `update wheels` (« lire une aire vide n'est pas une panne »). Ce qui échoue avec un
    rc, c'est un GESTE — `apply`, `rollback` — pas une question."""
    vue = aptitude(settings, unit=unit, scope=scope)
    dep, rev = vue["deployable"], vue["reversible"]

    print(f"{'✓' if dep['ok'] else '✗'} déployable — {dep['reason']}")
    # `?` et non `✗` : le socle refusé rend la réversibilité NON MESURÉE, pas fausse. Afficher une croix
    # ferait lire un second refus là où il n'y en a qu'un.
    marque = "?" if rev["ok"] is None else ("✓" if rev["ok"] else "✗")
    if rev["ok"]:
        cible = rev["target"]
        print(f"{marque} réversible — vers {cible['snapshot']}, par le binaire {cible['venv']}")
    else:
        print(f"{marque} réversible — {rev['reason']}")

    if dep["ok"] and rev["ok"]:
        print("\n`forgemaster update rollback` peut encore refuser dans l'instant (travail non commité, "
              "dispatch en vol) :\n  c'est la DISPONIBILITÉ, pas l'aptitude. Elle se mesure au moment du "
              "geste, pas ici.")
    return 0


def _cli_wheels(settings: Settings) -> int:
    """`forgemaster update wheels` — ce que la route a déposé, et ce que la rétention en garde. Toujours
    rc 0 : lire une aire vide n'est pas une panne, c'est l'état d'une instance qui n'a rien reçu."""
    vue = list_wheels(settings)
    print(f"aire de dépôt : {wheels_dir(settings)}")
    if not vue["wheels"]:
        print("  (vide — rien n'a été déposé par la route ; `--wheel <chemin>` reste le chemin de la CLI)")
    for w in vue["wheels"]:
        sha = (w["sha256"] or "")[:12] or "sha non mesuré"
        tenu = "  ← retenu : un run sans verdict le nomme" if w["in_use"] else ""
        print(f"  {w['stamp']}  {w['name']}  {w['size'] / 1024 / 1024:.1f} Mo  {sha}{tenu}")
    print(f"\nrétention : les {vue['keep']} plus récents sont gardés, plus tout dépôt qu'un run sans verdict "
          f"nomme encore.\n  la purge se fait au dépôt suivant, et elle dit ce qu'elle a retiré.")
    return 0
