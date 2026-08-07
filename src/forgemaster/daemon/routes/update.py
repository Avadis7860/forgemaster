"""routes/update — la surface HTTP du cycle de mise à jour : déclencher, et **retrouver** le verdict.

La route la plus puissante du produit — elle remplace le binaire de l'instance qui la sert. Deux propriétés
la rendent tenable, et aucune n'est un détail :

**1. Rien ne vit en mémoire.** Le processus qui répond au `GET` d'après n'est ni celui qui a reçu le `POST`,
ni même le même binaire : la bascule est passée entre les deux. Tout l'état d'un run se relit du disque
(`update.run_state`), à chaque requête. Un registre de tâches, un `BackgroundTasks`, un dictionnaire sur
`app.state` : tous rendraient « inconnu » exactement au moment où l'utilisateur attend son verdict.

**2. Le POST exécute, il ne prévisualise pas.** La prévisualisation d'un geste mutant est un `GET`
idempotent — `GET /api/update/plan` porte le préflight et ce qui va se passer, comme le fait déjà
`GET .../git/sync` avant `POST .../git/sync/reconcile` (`docs/schema-contract.md` §3). Un `dry_run` dans le
corps d'un `POST` obligerait à faire confiance à un drapeau pour ne rien casser.

**3. L'artefact arrive par ici, mais `apply` n'est pas confiné à ce qui en vient.** `POST /wheels` donne à
HTTP le système de fichiers qu'il n'a pas — la CLI, elle, en a un. Confiner `apply` aux seuls dépôts serait
tentant et faux : le canal servi (phase 5) fera arriver un wheel ailleurs, et il faudrait ré-ouvrir. Le
dépôt **ajoute une source**, il ne devient pas la seule.

**Surface volontairement plus étroite que la CLI.** Ni `--unit`, ni `--systemctl`, ni `--service` : ce sont
des points d'injection pour un test ou pour un opérateur devant un terminal, pas des choses qu'on accepte
d'un corps de requête. La route sert le chemin nominal du produit ; l'unité est celle de la portée.

**Posture d'authentification** — ce daemon n'authentifie **aucun** appelant (CORS seul, bind `127.0.0.1`).
La route n'ajoute pas ce pouvoir (la CLI pose déjà un wheel arbitraire), elle l'expose par un second chemin
sur la même boucle locale. Ce qu'elle ajoute et qui est mesuré, pas supposé : un corps JSON force un
préflight CORS, que l'allow-list refuse pour toute origine tierce (`tests/test_update_route.py`). Le jour où
l'instance écoutera autre chose que la boucle locale, cette route est la première à devoir une auth.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from forgemaster import update
from forgemaster.daemon.deps import Deps, get_deps

Scope = Literal["user", "system"]

# La portée par défaut est `user`, et ce n'est pas un confort : mesuré sur vrai systemd, un daemon non
# privilégié ne peut PAS piloter une unité système (polkit refuse) — c'est donc le seul chemin par lequel
# une MAJ depuis le produit existe. Le refus « portée système sans être root » du préflight reste le filet.
DEFAULT_SCOPE: Scope = "user"


# `extra="forbid"` — déviation ASSUMÉE de la permissivité par défaut, et le motif est propre à ces deux
# corps : un champ ignoré en silence laisserait croire qu'on a épinglé l'unité (`unit`) ou le binaire
# systemctl. Sur la route la plus puissante du produit, « ignoré » se lit « honoré » par qui l'a écrit. Un
# 422 dit la vérité — c'est le même invariant que « jamais de cap silencieux », appliqué à une entrée.
_STRICT = ConfigDict(extra="forbid")


class ApplyRequest(BaseModel):
    model_config = _STRICT
    wheel: str                       # le wheel LOCAL à poser (aucun réseau, aucune résolution)
    scope: Scope = DEFAULT_SCOPE


class RollbackRequest(BaseModel):
    model_config = _STRICT
    snapshot: str | None = None      # défaut : le plus récent instantané qui ramène VRAIMENT en arrière
    scope: Scope = DEFAULT_SCOPE


def make_update_router() -> APIRouter:
    router = APIRouter(prefix="/api/update", tags=["update"])

    def _plan(deps: Deps, request: Request, *, mode: str, wheel: str | None,
              snapshot: str | None, scope: str) -> dict:
        """Le préflight des deux gestes, avec la connaissance que **seul le daemon** possède. Un refus sort
        en 409 : la requête est bien formée, c'est l'instance qui refuse dans son état."""
        settings = deps.settings
        authority = update.survey_authority(settings)
        in_flight = update.survey_in_flight(settings)
        # Le refus de CONCURRENCE, depuis le 2026-08-07 : deux gestes mutants acceptés à 2 s d'écart
        # lançaient deux applicateurs simultanés. La sonde systemd vit dans le relevé, pas ici — la route
        # ne fait que le calculer et le passer, comme les deux autres.
        updates_in_flight = update.survey_updates_in_flight(settings)
        sessions = _sessions(request)
        try:
            if mode == "apply":
                if not wheel:
                    raise ValueError("`wheel` est requis pour une MAJ — ce verbe ne pose que le fichier "
                                     "qu'on lui désigne")
                return update.preflight(settings, wheel=wheel, unit=None, scope=scope,
                                        authority=authority, in_flight=in_flight,
                                        updates_in_flight=updates_in_flight, sessions=sessions)
            return update.preflight_rollback(settings, snapshot=snapshot, unit=None, scope=scope,
                                             authority=authority, in_flight=in_flight,
                                             updates_in_flight=updates_in_flight,
                                             sessions=sessions)
        except update.UpdateRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/plan")
    def plan_route(request: Request, mode: Literal["apply", "rollback"] = "apply",
                   wheel: str | None = None, snapshot: str | None = None,
                   scope: Scope = DEFAULT_SCOPE, deps: Deps = Depends(get_deps)) -> dict:
        """**Ce qui se passerait**, et rien d'autre : préflight complet + la description que la CLI imprime
        avant d'agir. Strictement idempotent — aucun dossier de run n'est créé. C'est le `--dry-run` de la
        CLI rendu par le verbe qui convient. **409** quand l'instance refuse, avec le texte intégral du
        refus (jamais un « impossible » nu) ; **400** si `wheel` manque."""
        plan = _plan(deps, request, mode=mode, wheel=wheel, snapshot=snapshot, scope=scope)
        lines = update.describe(plan) if mode == "apply" else update.describe_rollback(plan)
        return {"mode": mode, "scope": scope, "describe": lines, "plan": _public(plan)}

    @router.get("/aptitude")
    def aptitude_route(scope: Scope = DEFAULT_SCOPE, deps: Deps = Depends(get_deps)) -> dict:
        """Ce que cette instance sait faire — se déployer, revenir — **avant** qu'on le lui demande.

        **200 toujours, y compris quand tout refuse.** Un refus d'aptitude est un ÉTAT, pas une erreur :
        la requête est bien formée, l'instance répond, et sa réponse est « non ». Le 409 reste la réponse
        de `/plan`, qui prévisualise une ACTION — là, refuser veut dire « ce que tu me demandes de faire
        n'aura pas lieu ». Rendre 409 ici obligerait chaque lecteur à traiter un état normal comme une
        panne, et la surface qui l'affiche au repos ne pourrait plus distinguer « je ne sais pas revenir »
        de « je n'ai pas pu te répondre ».

        Elle ne porte **que** le structurel. Le travail non commité et les dispatches en vol restent dans
        les préflights : ils répondent à « peut-elle le faire maintenant ? », qui vieillit en secondes et
        n'a rien à faire sur une page qu'on ne relit pas."""
        return update.aptitude(deps.settings, unit=None, scope=scope)

    @router.post("/apply", status_code=202)
    def apply_route(body: ApplyRequest, request: Request, deps: Deps = Depends(get_deps)) -> dict:
        """Pose un wheel local, en bleu/vert, avec retour arrière automatique s'il ne sert pas. **202** :
        c'est accepté et parti, pas fini — le daemon va mourir puis revenir. La réponse porte l'identifiant
        du run, seule chose dont l'appelant a besoin pour retrouver le verdict après la bascule."""
        return _lance(deps, request, mode="apply", wheel=body.wheel, snapshot=None, scope=body.scope)

    @router.post("/rollback", status_code=202)
    def rollback_route(body: RollbackRequest, request: Request,
                       deps: Deps = Depends(get_deps)) -> dict:
        """Revient en arrière d'un geste — binaire ET données ensemble, ou pas du tout. Même contrat de
        réponse que l'aller : la surface expose les deux sens, sinon elle re-crée le lot de consolation que
        le retour arrière volontaire a fermé."""
        return _lance(deps, request, mode="rollback", wheel=None, snapshot=body.snapshot,
                      scope=body.scope)

    def _lance(deps: Deps, request: Request, *, mode: str, wheel: str | None,
               snapshot: str | None, scope: str) -> dict:
        plan = _plan(deps, request, mode=mode, wheel=wheel, snapshot=snapshot, scope=scope)
        issue = update.spawn(deps.settings, plan, systemctl="systemctl", service="forgemaster", mode=mode)
        if not issue["ok"]:
            if issue["reason"] == "collision":
                # Un autre geste est parti dans la même seconde : conflit TRANSITOIRE, pas une panne — et
                # surtout rien n'a été touché, ni sur l'instance ni sur le run de l'autre.
                raise HTTPException(status_code=409, detail=issue["detail"])
            # Ce n'est PAS un refus (l'instance était d'accord) : c'est une machinerie indisponible. Le 503
            # le dit, et l'identifiant du run voyage quand même — le dossier existe, la trace est trouvable.
            raise HTTPException(
                status_code=503,
                detail=f"{issue['unit']} n'a pas été enregistrée par systemd — rien n'a bougé sur "
                       f"l'instance. Le dossier de run {issue['run'].name} reste pour la trace.\n"
                       f"{issue['detail']}")
        return {"run": issue["run"].name, "unit": issue["unit"], "mode": mode, "state": "running"}

    @router.post("/wheels", status_code=201)
    def stage_wheel_route(file: UploadFile = File(...), deps: Deps = Depends(get_deps)) -> dict:
        """Dépose l'artefact à poser. **201** `{stamp, name, path, size, sha256, staged_at, pruned}` — le
        `path` rendu se repasse tel quel à `GET /plan` puis `POST /apply` ; c'est tout le rôle de cette
        route, donner à HTTP le système de fichiers qu'il n'a pas.

        Le corps est lu **en flux** (`WHEEL_CHUNK`), jamais d'un seul `read()` : le patron d'origine
        (`projects.upload_to_project`) matérialise toute la part avant de la borner, ce qui est tenable sous
        un cap de 10 Mo et ne l'est pas sur le daemon qui s'apprête à se remplacer lui-même. D'où un handler
        **synchrone** — FastAPI le joue dans le pool de fils, et le fichier spoolé de la part (`file.file`,
        rembobiné par le parseur) se lit morceau par morceau, sans générateur asynchrone à recoller.

        Ce que le flux n'achète PAS, et il faut le dire : le parseur multipart a **déjà** déversé la part sur
        le disque avant que ce handler soit appelé. C'est un autre défaut, fiché, pas maquillé.

        Nom traversant/vide → **400** · extension hors `.whl` → **415** · au-delà de la borne → **413** (les
        trois par les handlers globaux) · deux dépôts dans la même seconde → **409**."""
        def _flux() -> Iterator[bytes]:
            while chunk := file.file.read(update.WHEEL_CHUNK):
                yield chunk
        try:
            return update.stage_wheel(deps.settings, filename=file.filename or "", chunks=_flux())
        except update.UpdateRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/wheels")
    def wheels_route(deps: Deps = Depends(get_deps)) -> dict:
        """Les artefacts déposés, récents d'abord, et la politique qui les garde (`keep`). `in_use` dit
        celui qu'un run sans verdict nomme encore — une rétention qui ne montre pas ses exceptions se lit
        comme un cap silencieux."""
        return update.list_wheels(deps.settings)

    @router.get("/runs")
    def runs_route(deps: Deps = Depends(get_deps)) -> dict:
        """Les runs, récents d'abord, chacun avec son état. Borne **dite** (`truncated` + `total`) : une
        liste tronquée en silence se lit « c'est tout ». Sans journal — la vue de liste n'en affiche aucun."""
        return update.list_runs(deps.settings, is_active=update.systemd_is_active())

    @router.get("/runs/{run_id}")
    def run_route(run_id: str, deps: Deps = Depends(get_deps)) -> dict:
        """L'état d'un run + son verdict + la queue de son journal, **lus sur le disque**. C'est la route
        qui prouve la propriété : le binaire qui y répond après une MAJ n'est pas celui qui a reçu le
        `POST`. Run inconnu ou identifiant hors forme → **404** (handler global sur `KeyError`)."""
        return update.run_state(update.run_dir_for(deps.settings, run_id),
                                is_active=update.systemd_is_active())

    return router


def _sessions(request: Request) -> list[str]:
    """Les shells du terminal web encore vivants — le registre vit sur `app.state`, posé par le lifespan.
    `getattr` avec défaut, sans quoi toute requête sur une app non entrée en contexte (le cas d'un
    `TestClient` nu) tomberait en 500 sur un attribut manquant. Ils ne **bloquent** rien : ils sont dits."""
    registry = getattr(request.app.state, "terminals", None)
    return registry.live() if registry is not None else []


def _public(plan: dict) -> dict:
    """Le plan rendu au réseau : des chaînes, et **seulement** ce qui aide à décider. Les `Path` ne sont pas
    sérialisables tels quels, et le verdict d'autorité par projet est déjà distillé dans `describe`."""
    garde = ("unit", "link", "venv", "base_url", "scope", "wheel", "snapshot", "snapshot_name",
             "target_venv")
    return {k: str(plan[k]) for k in garde if plan.get(k) is not None}
