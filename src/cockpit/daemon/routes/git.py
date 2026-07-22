"""routes/git — router de domaine « git » (visibilité read-only sur le SoT bare d'un projet : branches,
avance/retard `main` vs `dev`, log court, écart SoT↔miroir, **et exploration de l'arbre + contenu d'un
fichier**). **Read-only sauf UNE op gatée** : `POST .../git/sync/reconcile` réconcilie le miroir **ff-only**
(jamais de merge non-ff, spec forge-sot-local) — tout le reste ne fait que lire ; le cycle git mutant de
développement (worktree → gate → merge) vit dans `gate/merge`. Sert « où en est main vs dev ? », « suis-je à
jour vs GitHub ? » ET « voyager dans les fichiers du dépôt »."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from cockpit.daemon.deps import Deps, get_deps
from cockpit.git.internal import BlobTooLargeError, GitOpError, InternalGit
from cockpit.projects.registry import get_project
from cockpit.secrets import cred_resolver

_LOG_N = 20  # profondeur du log par réf (récents d'abord)

# Anti-XSS same-origin (le daemon sert l'app ET les octets) : seuls quelques types réellement inertes gardent
# leur Content-Type en affichage « raw » inline ; tout le reste (text/*, html, svg, inconnu) est coercé en
# text/plain pour qu'aucun HTML/JS/SVG du dépôt ne s'exécute dans notre origine.
_INLINE_SAFE = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"})


def _raw_media_type(path: str) -> str:
    """Content-Type sûr pour un affichage inline : type deviné s'il est inerte, sinon text/plain."""
    guessed, _ = mimetypes.guess_type(path)
    return guessed if guessed in _INLINE_SAFE else "text/plain; charset=utf-8"


def _download_filename(path: str) -> str:
    """Basename assaini pour un en-tête `Content-Disposition` (retire CR/LF/guillemets → pas d'injection)."""
    name = path.strip("/").rsplit("/", 1)[-1]
    return name.translate({0x0D: None, 0x0A: None, 0x22: None}) or "download"


def make_git_router() -> APIRouter:
    router = APIRouter(tags=["git"])

    def _sot(deps: Deps, project: str) -> Path:
        """Résout le chemin du SoT bare d'un projet (KeyError projet absent → 404 handler global)."""
        conn = deps.open_db()
        try:
            return Path(get_project(conn, project)["sot_path"])
        finally:
            conn.close()

    @router.get("/api/projects/{project}/git")
    def git_view(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Vue git read-only du SoT bare : branches (nom·sha court·sujet), avance/retard `main` vs `dev`
        (le signal « main rattrape dev »), et log court par réf protégée présente. Idempotent (aucune
        mutation) — le runner de boucle visuelle *goto-only* l'atteint sans risque. Projet absent → 404 ;
        SoT illisible → 422 (jamais un demi-état inventé)."""
        sot = _sot(deps, project)
        git = InternalGit()
        try:
            branches = git.branches(sot)
            names = {b["name"] for b in branches}
            logs = {ref: git.log(sot, ref, n=_LOG_N) for ref in ("dev", "main") if ref in names}
            # ahead/behind seulement si les deux réfs protégées existent (un SoT neuf les a toutes deux).
            ahead_behind = (
                git.ahead_behind(sot, base="main", head="dev") if {"dev", "main"} <= names else None
            )
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"lecture git impossible : {exc}") from exc
        return {"project": project, "branches": branches, "ahead_behind": ahead_behind, "logs": logs}

    @router.get("/api/projects/{project}/git/sync")
    def git_sync(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Écart SoT↔**miroir GitHub** par branche + rollup projet. **RÉSEAU, non-idempotent** : fait un
        `git fetch` du miroir — donc **SÉPARÉ** du `GET .../git` idempotent no-poll (le runner de boucle
        visuelle *goto-only* ne doit PAS déclencher de fetch réseau ; l'UI le rattache au `RefreshButton`
        manuel, jamais au polling). Rend `{project, remote, fetched, branches:{<b>:{ahead, behind, state}},
        state}` (rollup `synced|local_ahead|remote_ahead|diverged`). **Dégradation honnête, jamais 0/0
        faux-vert** : miroir non câblé → `no_mirror` ; injoignable/auth → `unreachable` (`fetched=false`,
        `branches={}`). Auth transitoire via le `credential_ref` du projet (à l'usage, jamais le token).
        Projet absent → 404 ; SoT illisible/corrompu → 422."""
        conn = deps.open_db()
        try:
            row = get_project(conn, project)   # KeyError projet absent → 404 (handler global)
        finally:
            conn.close()
        git = InternalGit(cred_resolver=cred_resolver(deps.settings))
        try:
            div = git.remote_divergence(
                Path(row["sot_path"]), remote="mirror", branches=("dev", "main"),
                creds_ref=row["credential_ref"],
            )
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"sync git impossible : {exc}") from exc
        return {"project": project, **div}

    @router.post("/api/projects/{project}/git/sync/reconcile")
    def git_sync_reconcile(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Réconcilie le SoT avec son miroir GitHub **ff-only** — la SEULE mutation de ce routeur, et elle est
        **gatée par construction** : jamais de merge non-ff ni de `--force` (spec forge-sot-local). L'UI
        montre d'ABORD la décision via le GET `/git/sync` idempotent (source unique = l'`state` par branche),
        puis appelle ce POST pour exécuter — pas de dry-run POST. Recalcule la divergence (l'état frais fait
        autorité), puis par branche : `remote_ahead` → ff local ; `local_ahead` → push ff ; `diverged` →
        **bloqué** sans mutation ; garde-fou : jamais ff une branche sortie dans un worktree actif. Rend
        `{project, remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state}`.
        Projet absent → 404 ; op git dure (ex. ref corrompue) → 422."""
        conn = deps.open_db()
        try:
            row = get_project(conn, project)   # KeyError projet absent → 404 (handler global)
        finally:
            conn.close()
        git = InternalGit(cred_resolver=cred_resolver(deps.settings))
        try:
            report = git.reconcile(
                Path(row["sot_path"]), remote="mirror", branches=("dev", "main"),
                creds_ref=row["credential_ref"],
            )
        except GitOpError as exc:
            raise HTTPException(status_code=422, detail=f"réconciliation impossible : {exc}") from exc
        return {"project": project, **report}

    @router.get("/api/projects/{project}/git/tree")
    def git_tree(project: str, ref: str = "dev", path: str = "", deps: Deps = Depends(get_deps)) -> dict:
        """Entrées d'un dossier du dépôt à une réf (dossiers d'abord). `ref` défaut `dev`, `path` vide =
        racine. Read-only, idempotent (goto-only safe). Projet absent → 404 ; réf/chemin introuvable ou
        `path` non-dossier → 404 (la ressource demandée n'existe pas à cette réf)."""
        sot = _sot(deps, project)
        git = InternalGit()
        try:
            entries = git.ls_tree(sot, ref, path)
        except GitOpError as exc:
            raise HTTPException(status_code=404, detail=f"arbre introuvable ({ref}:{path}) : {exc}") from exc
        # Enrichissement façon GitHub : dernier commit par entrée + « latest commit » du dossier. La réf est
        # déjà validée par ls_tree ; ls_tree reste pur (le chemin codemap/archive n'est pas ralenti).
        last = git.entry_last_commits(sot, ref, path, [e["name"] for e in entries])
        for e in entries:
            e["last_commit"] = last.get(e["name"])
        return {"project": project, "ref": ref, "path": path, "entries": entries,
                "latest_commit": git.latest_commit(sot, ref, path)}

    @router.get("/api/projects/{project}/git/blob")
    def git_blob(project: str, ref: str, path: str, deps: Deps = Depends(get_deps)) -> dict:
        """Contenu d'un fichier du dépôt à une réf. `ref` et `path` requis. Renvoie type/taille + contenu
        texte (ou drapeaux `binary`/`too_large`/`truncated` — jamais d'octets bruts, cf. gardes L4).
        Read-only, idempotent. Projet absent → 404 ; réf/chemin introuvable ou non-blob → 404."""
        sot = _sot(deps, project)
        try:
            blob = InternalGit().read_blob(sot, ref, path)
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"fichier introuvable ({ref}:{path}) : {exc}") from exc
        return {"project": project, **blob}

    def _raw_bytes(sot: Path, ref: str, path: str) -> bytes:
        """Octets bruts pour raw/download, avec la sémantique HTTP : cap dépassé → 413 (signalé),
        introuvable/non-blob → 404. `BlobTooLargeError` (sous-classe) attrapé AVANT `GitOpError`."""
        try:
            return InternalGit().read_blob_raw(sot, ref, path)
        except BlobTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"fichier introuvable ({ref}:{path}) : {exc}") from exc

    @router.get("/api/projects/{project}/git/raw")
    def git_raw(project: str, ref: str, path: str, deps: Deps = Depends(get_deps)) -> Response:
        """Octets **bruts** d'un fichier à une réf, pour affichage **inline** (bouton « Raw »). Content-Type
        deviné mais coercé en `text/plain` pour tout type actif (anti-XSS same-origin) + `X-Content-Type-
        Options: nosniff`. Read-only, idempotent. Projet absent / réf-chemin introuvable / non-blob → 404 ;
        au-delà de 10 Mo → **413** (jamais tronqué en silence)."""
        sot = _sot(deps, project)
        data = _raw_bytes(sot, ref, path)
        return Response(
            content=data, media_type=_raw_media_type(path),
            headers={"X-Content-Type-Options": "nosniff",
                     "Content-Disposition": f'inline; filename="{_download_filename(path)}"'})

    @router.get("/api/projects/{project}/git/download")
    def git_download(project: str, ref: str, path: str, deps: Deps = Depends(get_deps)) -> Response:
        """Octets **bruts** d'un fichier en **pièce jointe** (bouton « Download ») : `application/octet-
        stream` + `Content-Disposition: attachment` (jamais rendu inline) + `nosniff`. Read-only,
        idempotent. Mêmes 404 (introuvable/non-blob) et **413** (> 10 Mo) que `raw`."""
        sot = _sot(deps, project)
        data = _raw_bytes(sot, ref, path)
        return Response(
            content=data, media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff",
                     "Content-Disposition": f'attachment; filename="{_download_filename(path)}"'})

    @router.get("/api/projects/{project}/git/commit/{sha}")
    def git_commit(project: str, sha: str, deps: Deps = Depends(get_deps)) -> dict:
        """Détail d'un commit (métadonnées + fichiers touchés avec `+/-` par fichier). Read-only,
        idempotent (goto-only safe). Projet absent → 404 ; sha/réf introuvable → 404."""
        sot = _sot(deps, project)
        try:
            detail = InternalGit().commit_detail(sot, sha)
        except GitOpError as exc:
            raise HTTPException(status_code=404, detail=f"commit introuvable ({sha}) : {exc}") from exc
        return {"project": project, **detail}

    @router.get("/api/projects/{project}/git/diff")
    def git_diff(
        project: str, base: str, head: str, deps: Deps = Depends(get_deps),
    ) -> dict:
        """Diff unifié d'une feature (`base...head`, three-dot = depuis la merge-base). Read-only,
        idempotent. Projet absent → 404 ; une réf introuvable → 404."""
        sot = _sot(deps, project)
        git = InternalGit()
        try:
            text = git.diff_text(sot, base=base, head=head)
            names = git.diff_names(sot, base=base, head=head)
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"diff impossible ({base}...{head}) : {exc}") from exc
        return {"project": project, "base": base, "head": head, "files": names, "diff": text}

    @router.get("/api/projects/{project}/git/history")
    def git_history(
        project: str, ref: str, path: str, deps: Deps = Depends(get_deps),
    ) -> dict:
        """Historique des commits touchant un fichier à une réf (récents d'abord). Read-only, idempotent.
        Projet absent → 404 ; réf introuvable → 404 ; fichier sans historique → liste vide (200)."""
        sot = _sot(deps, project)
        try:
            commits = InternalGit().file_history(sot, ref, path)
        except GitOpError as exc:
            raise HTTPException(
                status_code=404, detail=f"historique introuvable ({ref}:{path}) : {exc}") from exc
        return {"project": project, "ref": ref, "path": path, "commits": commits}

    return router
