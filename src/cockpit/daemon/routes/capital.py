"""routes/capital — router de domaine « capital-token servi » : parcourir en LECTURE le corpus typé servi par
le MCP `mcp-catalogs` (navigation `types → collections → sections → corps`), depuis l'accueil top-level du
cockpit. Pendant que `routes/bundles` introspecte ce que le cockpit **sème** (bundles vendorés), ce router
introspecte le **capital-token** qu'il **loue/possède** (`tech`/`blueprint`/`templates`) — l'autre moitié de
« juger l'efficacité avant distribution ».

Read-only, idempotent (GET) : le corps servi vient du MCP en direct via `mcp.capital_browser` (mint JWT +
`fastmcp`, dégradation honnête totale). Goto-safe : le runner goto-only de la boucle visuelle atteint ces GET.

**Dégradation honnête** (le cœur) : le browser rend `None` dès que le MCP n'est pas câblé (pas de
`COCKPIT_MCP_JWT_SECRET_REF`) OU injoignable — jamais inventé. La route en fait un **503** honnête. Le front
ne tente PAS le parcours quand `/status` dit `wired:false` (il rend « non câblé ») ; le 503 couvre la panne
réseau d'un MCP câblé-mais-down. `wire_state()` est la porte sans réseau (`{wired, endpoint}`).

Seams injectés (`browser_factory`, `status_fn`) : test sans réseau, patron `test_mcp_client`.
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from cockpit.config import Settings
from cockpit.daemon.deps import Deps, get_deps
from cockpit.mcp import CapitalBrowser, capital_browser
from cockpit.provision.mcp import wire_state

_UNAVAILABLE = "capital-token indisponible : MCP non câblé ou injoignable"


def _served(data: dict | list | None) -> dict | list:
    """**Pass-through** honnête : les outils MCP rendent déjà des corps bien formés (`{types:[…]}`,
    `{type,collections,…}`, `{type,scope,sections,…}`, `{type,ref,body/content,…}`) — on les sert tels quels
    (le daemon = proxy authentifié fin, le serveur MCP est la SoT de la forme). `None` (MCP non câblé ou
    injoignable) → **503** honnête, jamais un corps inventé ni un ré-emballage."""
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
        return _served(browser_factory(deps.settings).list_types())

    @router.get("/read")
    def capital_read(type: str, ref: str, deps: Deps = Depends(get_deps)) -> dict | list:
        """Le **corps** d'une ref (`<collection>/<path>` silo, ou `<id>` plat). MCP indispo → 503 honnête."""
        return _served(browser_factory(deps.settings).read(type, ref))

    @router.get("/{type}/collections")
    def capital_collections(type: str, deps: Deps = Depends(get_deps)) -> dict | list:
        """Les **collections** (silos) d'un type. Type plat (blueprint) → `collections:[]`. Indispo → 503."""
        return _served(browser_factory(deps.settings).list_collections(type))

    @router.get("/{type}/sections")
    def capital_sections(type: str, deps: Deps = Depends(get_deps),
                         scope: str | None = None) -> dict | list:
        """La **TOC** (sections) d'un type : `?scope=<silo>` requis pour un silo (`tech`), omis pour un corpus
        plat (`blueprint`). MCP indispo (ou silo sans scope côté serveur) → 503 honnête."""
        return _served(browser_factory(deps.settings).list_sections(type, scope))

    return router
