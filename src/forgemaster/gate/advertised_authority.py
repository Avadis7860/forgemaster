"""advertised_authority — dimension de gate **type-agnostique** : un artefact servi ne doit JAMAIS advertise
une **autorité absolue** (`scheme://host:port`) **différente de celle par laquelle le visiteur l'a atteint**.

Motivation (bug de CLASSE, très fréquent chez un worker/LLM, risque réputation public) : un produit déployé
émet un lien/redirect qui pointe un host/port **qu'il ne contrôle pas** — un loopback (`127.0.0.1`), le port
INTERNE du conteneur, un host en dur — au lieu de rester **relatif** à l'origine réelle. Le visiteur suit
vers une autorité injoignable → `ERR_CONNECTION_REFUSED`. Instance racine (drain avagency 2026-07-29) : nginx
SSG `absolute_redirect on` (défaut) fuit son `listen 8000` dans un 301 de dossier (`Location:
http://127.0.0.1:8000/x/`) alors que le forgemaster publie sur un port dynamique (5250+).

**Positif par catégorie, PAS un denylist** (objection bosse : un denylist de `127.0.0.1`/`0.0.0.0` est
contournable par un `128.0.0.X` ou un hostname). La règle unique : *toute* autorité absolue dans un `Location`
de redirect qui **diffère de l'autorité atteinte** échoue — que la valeur soit un loopback, une IP inventée ou
un nom d'hôte. Le relatif passe (c'est ce qu'on VEUT) ; le même-autorité passe (self-redirect canonical
légitime). On cible l'ADVERTISE (le `Location`), jamais le bind (`listen 0.0.0.0` sans schéma reste légitime).

Ce module ne connaît AUCUN type de projet : il vit dans `gate/` et se branche là où le forgemaster sonde déjà
l'artefact servi (Tier-1.5 `verify.autoverify_feature`). Il couvre donc site-vitrine, service-api, front-ts…
sans garde dupliquée par-bundle.

**Frontière v1 connue** (cf. task `cockpit-absolute-url-authority-gate`) : un redirect *légitimement* externe
(OAuth, CDN) advertise une autre autorité et serait flaggé. Hors périmètre présent (preview LAN direct, port
brut, navigation interne) — à raffiner si le besoin se présente. La sonde **échoue-ouvert** sur erreur réseau
(la readiness/rendu reste gardée fail-closed par `verify_target` en amont) : l'absence de redirect observable
n'est pas une fuite.
"""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

# Un scheme→port par défaut minimal : sert à normaliser l'autorité pour comparer `:80`/`:443` implicites.
_DEFAULT_PORT = {"http": "80", "https": "443", "ws": "80", "wss": "443"}


def authority(url: str) -> tuple[str, str] | None:
    """Autorité `(host, port)` normalisée d'une URL, ou `None` si l'URL est **relative** (pas de
    `scheme://netloc`). Un `Location` relatif (`/x/`) → `None` = le bon cas (rien advertisé). Le port est
    explicité depuis le schéma quand absent (`http`→`80`) pour que `:80` implicite == `:80` explicite. PUR."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:                                   # port non numérique dans le netloc → douteux
        return (host, "?")
    resolved = str(port) if port is not None else _DEFAULT_PORT.get(parts.scheme.lower(), "?")
    return (host, resolved)


def analyze_location(request_url: str, status: int, location: str | None) -> dict | None:
    """Cœur PUR de la dimension. Rend un **finding** ssi la réponse est un redirect (3xx) dont le `Location`
    porte une **autorité absolue différente** de celle de `request_url`. Sinon `None` (pas de fuite) :
    - pas un 3xx, ou pas de `Location` → `None` ;
    - `Location` **relatif** (aucune autorité) → `None` (le cas voulu) ;
    - `Location` absolu **même autorité** que la requête → `None` (self-redirect canonical légitime).

    Positif par catégorie : n'importe quelle autorité absolue ≠ atteinte échoue (loopback, IP inventée,
    hostname), sans énumérer de valeurs interdites."""
    if not (300 <= status < 400) or not location:
        return None
    advertised = authority(location)
    if advertised is None:
        return None                                      # relatif → conforme (résolu contre l'origine réelle)
    reached = authority(request_url)
    if reached is not None and advertised == reached:
        return None                                      # même autorité → self-redirect légitime
    return {
        "kind": "cross_authority_redirect",
        "request_url": request_url,
        "status": status,
        "location": location,
        "advertised": f"{advertised[0]}:{advertised[1]}",
        "reached": f"{reached[0]}:{reached[1]}" if reached else None,
        "detail": (f"le redirect advertise l'autorité absolue {advertised[0]}:{advertised[1]} — "
                   f"différente de l'origine atteinte "
                   f"{reached[0]+':'+reached[1] if reached else '(inconnue)'} → autorité non contrôlée, "
                   f"injoignable pour le visiteur"),
    }


# Type d'une sonde : URL → (status, header Location|None). Injectable pour tester sans réseau.
Probe = Callable[[str], "tuple[int, str | None]"]


def _urllib_probe(url: str) -> tuple[int, str | None]:
    """Sonde réelle : GET **sans suivre** les redirects. Rend `(status, Location|None)`. Échoue-ouvert sur
    erreur réseau (`(0, None)` → analysé comme non-3xx → aucun finding) : cette dimension observe un redirect
    fuité, elle ne re-garde pas la readiness (déjà fail-closed en amont). Impur (socket)."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
            return None                                  # ne suit pas → l'HTTPError porte le 3xx + Location

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(url, timeout=8)               # noqa: S310 — URL de preview locale, pas d'entrée tierce
        return (resp.status, resp.headers.get("Location"))
    except urllib.error.HTTPError as exc:                # 3xx (non suivi) OU 4xx/5xx : status + headers là
        return (exc.code, exc.headers.get("Location") if exc.headers else None)
    except (urllib.error.URLError, OSError):
        return (0, None)                                 # réseau ko → échoue-ouvert (pas de fuite observable)


def _candidate_paths(paths: list[str]) -> list[str]:
    """Chemins à sonder, dédupliqués en gardant l'ordre. On ajoute la variante **sans** slash final pour les
    sous-chemins (`/x/` → aussi `/x`) : la requête sans slash déclenche le 301 de dossier fui. PUR."""
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        p = p if p.startswith("/") else "/" + p
        variants = [p]
        if p != "/" and p.endswith("/"):
            variants.append(p.rstrip("/"))
        for v in variants:
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def check_served_authority(base_url: str, paths: list[str], *, probe: Probe = _urllib_probe) -> list[dict]:
    """Sonde `base_url` + chaque chemin (sans suivre les redirects) et rend la liste des **findings**
    d'autorité fuitée. `base_url` = l'origine RÉELLE atteinte (le port publié) — elle définit l'autorité
    légitime. `probe` injectable (défaut = urllib no-follow) pour tester sans réseau. Ne lève jamais."""
    base = base_url.rstrip("/")
    findings: list[dict] = []
    for path in _candidate_paths(paths):
        url = base + path
        status, location = probe(url)
        finding = analyze_location(url, status, location)
        if finding is not None:
            findings.append(finding)
    return findings
