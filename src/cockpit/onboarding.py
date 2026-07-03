"""onboarding — check de config-requise au 1er démarrage + liaison des credentials par entité.

Une instance self-hosted onboarde SES tokens avant d'être pleinement fonctionnelle. Ce module ne stocke
RIEN par lui-même : il compose le **store de secrets actif** (`cockpit.secrets`) et le **registre de
projets** (`credential_ref`) pour deux usages —
  1. `status()` : ce qui manque (racine du store joignable ? quels projets à miroir n'ont pas de token ?),
     sans jamais lire une valeur de secret ;
  2. `link_credential()` : lie un token à une entité. La DB ne reçoit que la **référence** ; le store la
     valeur. Aucun secret ne transite par un log, un argv, ni un retour d'API.

Deux voies de liaison selon le backend, unifiées :
- **voie fichier/keyring** (on POSSÈDE la valeur) : `token` → `store.put(token)` → ref opaque générée ;
- **voie BWS** (bring-your-own) : `ref` (UUID) fourni → on le VALIDE via `store.get(ref)` puis on le lie
  (le backend BWS n'accepte pas `put`).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from cockpit.config import Settings
from cockpit.db import store as db_store
from cockpit.projects import registry
from cockpit.secrets import SecretNotFound, SecretStore, SecretUnsupported, build_store


def status(conn: sqlite3.Connection, secret_store: SecretStore) -> dict:
    """État d'onboarding de l'instance, SANS révéler aucun secret :
    - `secret_store` : backend actif + racine de confiance joignable (`health()`), pour le 1er démarrage ;
    - `requirements` : un item par projet ; un projet avec `mirror_remote` a **besoin** d'un token pour
      pousser le miroir → `satisfied` ssi il porte un `credential_ref` (ou n'a pas de miroir) ;
    - `complete` : racine du store prête ET toutes les exigences satisfaites (pas de faux-vert)."""
    ready, detail = secret_store.health()
    requirements = []
    for p in registry.list_projects(conn):
        needs = bool(p.get("mirror_remote"))
        linked = bool(p.get("credential_ref"))
        requirements.append({
            "project": p["slug"],
            "mirror_remote": p.get("mirror_remote"),
            "needs_credential": needs,
            "linked": linked,
            "satisfied": (not needs) or linked,
        })
    complete = ready and all(r["satisfied"] for r in requirements)
    return {
        "secret_store": {"backend": secret_store.backend, "ready": ready, "detail": detail},
        "requirements": requirements,
        "complete": complete,
    }


def link_credential(conn: sqlite3.Connection, secret_store: SecretStore, project: str, *,
                    token: str | None = None, ref: str | None = None,
                    label: str | None = None) -> dict:
    """Lie un credential à `project` et retourne le projet relu (avec `credential_ref`, JAMAIS le token).
    Fournir **exactement l'un** de `token` (voie fichier : on stocke la valeur) ou `ref` (voie BWS :
    bring-your-own UUID, validé avant liaison). Lève `ValueError` (→ 400) sur mauvais usage / backend
    incompatible / ref introuvable ; `KeyError` (→ 404) si le projet n'existe pas."""
    if (token is None) == (ref is None):
        raise ValueError("fournir exactement l'un de : token (voie fichier) | ref (voie BWS/UUID).")
    registry.get_project(conn, project)  # KeyError → 404 AVANT tout effet sur le store
    if token is not None:
        try:
            resolved = secret_store.put(token, label=label or project)
        except SecretUnsupported as exc:
            raise ValueError(
                f"le backend {secret_store.backend!r} n'accepte pas de token direct "
                f"(bring-your-own) — fournir plutôt une référence (UUID) via `ref`."
            ) from exc
    else:
        assert ref is not None
        try:
            secret_store.get(ref)  # valide que la référence résout (fail-loud si UUID inexistant)
        except SecretNotFound as exc:
            raise ValueError(f"référence introuvable dans le store {secret_store.backend!r} : {ref!r}") \
                from exc
        resolved = ref
    return registry.set_credential_ref(conn, project, resolved)


def unlink_credential(conn: sqlite3.Connection, project: str) -> dict:
    """Délie le credential d'un projet (`credential_ref` → NULL). Le secret reste dans le store (on ne
    pilote pas sa suppression ici — cf. `store.delete`). `KeyError` (→ 404) si le projet n'existe pas."""
    return registry.set_credential_ref(conn, project, None)


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit onboard <action>` : `status` (défaut) affiche ce qui manque ; `link` lie un token
    (`--token-file <f>` = voie fichier, jamais le token en argv ; ou `--ref <uuid>` = voie BWS) ; `unlink`
    délie."""
    conn = db_store.open_db(settings)
    try:
        action = getattr(args, "action", None) or "status"
        if action == "status":
            st = status(conn, build_store(settings))
            sstore = st["secret_store"]
            mark = "✅" if sstore["ready"] else "🔴"
            print(f"{mark} store `{sstore['backend']}` — {sstore['detail']}")
            for r in st["requirements"]:
                icon = "✅" if r["satisfied"] else "🔴"
                need = "token lié" if r["linked"] else ("token REQUIS (miroir)" if r["needs_credential"]
                                                        else "aucun miroir")
                print(f"  {icon} {r['project']} — {need}")
            print("onboarding complet ✅" if st["complete"] else "onboarding INCOMPLET 🔴")
            return 0 if st["complete"] else 1
        if action == "link":
            token = None
            if getattr(args, "token_file", None):
                token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
            p = link_credential(conn, build_store(settings), args.project, token=token,
                                ref=getattr(args, "ref", None), label=getattr(args, "label", None))
            print(f"credential lié à {p['slug']} (réf {p['credential_ref']}) — 0 token en DB")
            return 0
        if action == "unlink":
            p = unlink_credential(conn, args.project)
            print(f"credential délié de {p['slug']}")
            return 0
        raise ValueError(f"action onboard inconnue : {action!r}")
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
