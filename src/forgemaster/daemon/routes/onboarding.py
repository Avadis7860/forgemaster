"""routes/onboarding — router de domaine « onboarding self-hosted » : état de config-requise au 1er
démarrage + liaison/déliaison d'un credential par projet. Router **fin** : délègue à `forgemaster.onboarding`
(qui compose store de secrets + registre), lit `Deps` par injection explicite.

Sécurité : le token voyage dans le **corps** de la requête (jamais l'URL/les logs), est rangé dans le store
puis oublié ; la réponse ne renvoie QUE la référence opaque (`credential_ref`), jamais la valeur."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from forgemaster import build_provenance, onboarding, update_channel
from forgemaster.daemon.deps import Deps, get_deps


class CredentialLink(BaseModel):
    token: str | None = None   # voie fichier : on stocke la valeur → ref opaque générée
    ref: str | None = None     # voie BWS : bring-your-own UUID (validé avant liaison)
    label: str | None = None   # libellé humain optionnel, jamais le secret


class McpWire(BaseModel):
    secret: str | None = None    # voie valeur : secret HMAC brut (POSSÉDÉ → ref opaque), jamais renvoyé
    ref: str | None = None       # voie BWS : bring-your-own UUID (validé)
    endpoint: str | None = None  # override optionnel de l'endpoint MCP (défaut = MCP_ENDPOINT)


def make_onboarding_router() -> APIRouter:
    router = APIRouter(tags=["onboarding"])

    @router.get("/api/onboarding")
    def onboarding_status(deps: Deps = Depends(get_deps)) -> dict:
        """État d'onboarding : backend du store + racine joignable (`health`), exigences par projet,
        `complete`. Idempotent, aucun secret révélé — atteignable par le runner *goto-only*."""
        conn = deps.open_db()
        try:
            return onboarding.status(conn, deps.secret_store(), settings=deps.settings)
        finally:
            conn.close()

    @router.get("/api/version")
    def version(deps: Deps = Depends(get_deps)) -> dict:
        """**Quelle édition tourne ici ?** Provenance de build + fraîcheur du wheel (`{version, sha,
        committed_at, comparable, stale, behind_by, missing_types, reference, head}`), **mode d'install**
        (`install`, déduit du disque), les 3 cartes hôte **servies** (`maps`), ce que l'édition installée
        **déclare** pour elles (`edition`, = `forgemaster toolchain check`), la topologie du serveur de
        corpus (`mcp`) et le verdict du **canal servi** (`channel`, lu du cache disque — jamais du réseau).
        Signal honnête, jamais faux-vert : un forgemaster en retard sur son miroir SoT local se déclare
        (`stale`, `missing_types`) ; sans provenance/miroir, `comparable=False` ; une conformité non
        vérifiable rend `edition.state = "unknown"`, jamais un vert. Idempotent, sans secret, zéro réseau,
        I/O-free côté liveness (distinct de `/health`).

        Les deux volets sont **composés ici**, et pas dans `build_provenance` : ce module porte dans son
        propre en-tête la contrainte « aucun accès réseau », qui est aujourd'hui une propriété du graphe
        d'imports. Lui faire importer le canal la dégraderait en affaire de confiance — et les 4 autres
        appelants de `provenance()` (snapshot, registre, onboarding) gagneraient un volet dont ils n'ont
        que faire."""
        prov = build_provenance.provenance(deps.settings)
        return {**prov,
                "channel": update_channel.read_verdict(deps.settings, build_sha=prov["sha"])}

    @router.post("/api/projects/{project}/credential")
    def link_credential(project: str, body: CredentialLink, deps: Deps = Depends(get_deps)) -> dict:
        """Lie un credential au projet : `token` (voie fichier) OU `ref` (voie BWS/UUID). Retourne le projet
        avec sa `credential_ref` — jamais le token. Mauvais usage/backend → 400 ; projet absent → 404."""
        conn = deps.open_db()
        try:
            return onboarding.link_credential(conn, deps.secret_store(), project,
                                              token=body.token, ref=body.ref, label=body.label)
        finally:
            conn.close()

    @router.post("/api/onboarding/mcp")
    def wire_mcp(body: McpWire, deps: Deps = Depends(get_deps)) -> dict:
        """Câble l'instance forgemaster-catalogs (wizard, instance-level, hors projet) : pose la ref opaque
        du secret HMAC + l'endpoint → le prochain dispatch injecte un `.mcp.json` valide **sans restart**.
        `secret` (valeur brute) OU `ref` (BWS/UUID). Mauvais usage/backend → 400. Ne renvoie JAMAIS le
        secret, seulement la `credential_ref` opaque posée."""
        return onboarding.wire_mcp(deps.settings, secret=body.secret, ref=body.ref, endpoint=body.endpoint)

    @router.delete("/api/projects/{project}/credential")
    def unlink_credential(project: str, deps: Deps = Depends(get_deps)) -> dict:
        """Délie le credential du projet (`credential_ref` → NULL). Le secret reste dans le store. Projet
        absent → 404."""
        conn = deps.open_db()
        try:
            return onboarding.unlink_credential(conn, project)
        finally:
            conn.close()

    return router
