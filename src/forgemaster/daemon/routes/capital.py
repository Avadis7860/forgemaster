"""routes/capital — router de domaine « capital-token servi » : parcourir en LECTURE le corpus typé servi
par le MCP `forgemaster-catalogs` (navigation `types → collections → sections → corps`), depuis l'accueil
top-level du forgemaster. Pendant que `routes/bundles` introspecte ce que le forgemaster **sème** (bundles
vendorés), ce router introspecte le **capital-token** qu'il **loue/possède**
(`tech`/`blueprint`/`templates`) — l'autre moitié de « juger l'efficacité avant distribution ».

Read-only, idempotent (GET) : le corps servi vient du MCP en direct via `mcp.capital_browser` (mint JWT +
`fastmcp`, dégradation honnête totale). Goto-safe : le runner goto-only de la boucle visuelle atteint ces GET.

**Dégradation honnête, 3 états** (le cœur) : le browser rend `None` quand le MCP n'est pas câblé (pas de
`FORGEMASTER_MCP_JWT_SECRET_REF`) ou injoignable → la route en fait un **503** générique ; une **erreur
d'outil
serveur** (MCP câblé qui répond mais échoue sur la ressource) remonte typée (`CapitalServerError`) → **502** +
le détail serveur réel (plus de mislabel « non câblé »). Le front ne tente PAS le parcours quand `/status` dit
`wired:false` ; `wire_state()` est la porte sans réseau (`{wired, endpoint}`).

Seams injectés (`browser_factory`, `status_fn`) : test sans réseau, patron `test_mcp_client`.
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from forgemaster.config import Settings
from forgemaster.daemon.deps import Deps, get_deps
from forgemaster.mcp import CapitalBrowser, CapitalServerError, capital_browser
from forgemaster.provision.mcp import wire_state

_UNAVAILABLE = "capital-token indisponible : MCP non câblé ou injoignable"


def _served(fetch: Callable[[], dict | list | None]) -> dict | list:
    """Résout un parcours MCP en corps HTTP en distinguant **trois états** (le mislabel corrigé) : (c) le MCP
    **répond mais échoue** sur la ressource (`CapitalServerError`) → **502** + le **détail serveur réel**
    (jamais « non câblé ») ; (a/b) non câblé / injoignable → `None` → **503** générique ; sinon le corps
    bien formé, servi tel quel (le serveur MCP est SoT de la forme, pas de ré-emballage). `fetch` est un
    **thunk** : l'appel browser vit DANS le `try` pour capter (c) au bon endroit."""
    try:
        data = fetch()
    except CapitalServerError as exc:
        raise HTTPException(status_code=502, detail=f"MCP a répondu en erreur : {exc}") from exc
    if data is None:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    return data


def make_capital_router(
    browser_factory: Callable[[Settings], CapitalBrowser] = capital_browser,
    status_fn: Callable[[], dict] = wire_state,
) -> APIRouter:
    router = APIRouter(prefix="/api/capital", tags=["capital"])

    @router.get("/status")
    def capital_status() -> dict:
        """État de câblage MCP vu par le daemon (`{wired, endpoint}`), **sans réseau** — la PORTE du front :
        `wired:false` → l'explorer rend « non câblé » honnête et ne tente aucune requête de parcours."""
        return status_fn()

    @router.get("/types")
    def capital_types(deps: Deps = Depends(get_deps)) -> dict | list:
        """Les **types** servis (`tech`/`blueprint`/`templates`) + stratégie. MCP indispo → 503 honnête."""
        return _served(lambda: browser_factory(deps.settings).list_types())

    @router.get("/read")
    def capital_read(type: str, ref: str, deps: Deps = Depends(get_deps)) -> dict | list:
        """Le **corps** d'une ref (`<collection>/<path>` silo, ou `<id>` plat). MCP indispo → 503 honnête."""
        return _served(lambda: browser_factory(deps.settings).read(type, ref))

    @router.get("/{type}/collections")
    def capital_collections(type: str, deps: Deps = Depends(get_deps)) -> dict | list:
        """Les **collections** (silos) d'un type. Type plat (blueprint) → `collections:[]`. Indispo → 503."""
        return _served(lambda: browser_factory(deps.settings).list_collections(type))

    @router.get("/{type}/sections")
    def capital_sections(type: str, deps: Deps = Depends(get_deps),
                         scope: str | None = None) -> dict | list:
        """La **TOC** (sections) d'un type : `?scope=<silo>` requis pour un silo (`tech`), omis pour un corpus
        plat (`blueprint`). MCP indispo (ou silo sans scope côté serveur) → 503 honnête."""
        return _served(lambda: browser_factory(deps.settings).list_sections(type, scope))

    return router
