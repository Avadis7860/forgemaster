"""secrets — coffre de credentials pluggable du forgemaster.

Un `credential_ref` opaque vit en DB à la place du token ; le store le résout à l'usage (writeback git),
injecte la valeur le temps de l'op, ne la persiste jamais. Backend choisi globalement par instance via
`FORGEMASTER_SECRET_STORE` (défaut `file`). Ce paquet reste stdlib-pur au chargement : `cryptography` (file)
et
`bitwarden-sdk` (bws) sont importés paresseusement par les implémentations.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from forgemaster.config import Settings

from .base import SecretNotFound, SecretStore, SecretStoreError, SecretUnsupported

__all__ = [
    "SecretStore",
    "SecretStoreError",
    "SecretNotFound",
    "SecretUnsupported",
    "build_store",
    "cred_resolver",
    "scoped_cred_resolver",
]


def build_store(settings: Settings) -> SecretStore:
    """Construit le store actif d'après `settings.secret_store`. Import concret paresseux (le défaut `file`
    ne tire jamais le SDK BWS, et inversement)."""
    backend = settings.secret_store
    if backend == "file":
        from .file_store import EncryptedFileStore

        return EncryptedFileStore(settings.secrets_dir)
    if backend == "bws":
        from .bws_store import BwsStore

        return BwsStore()
    raise SecretStoreError(f"backend de secret store inconnu : {backend!r} (attendu : file | bws).")


def cred_resolver(settings: Settings) -> Callable[[str], str]:
    """Résolveur `credential_ref → token` adossé au store actif, **partagé** par les couches qui injectent un
    token dans une op git (writeback merge, adoption/clone, bootstrap). **Lazy** : le store n'est construit
    que si une référence est réellement présentée. **Total** : un secret absent/illisible dégrade en `''`
    (jamais d'exception ici — l'auth git reste best-effort côté appelant). La *policy* de résolution vit ICI
    (couche secrets), jamais dans `git/internal` (qui n'importe pas ce paquet)."""
    def resolve(ref: str) -> str:
        try:
            return build_store(settings).get(ref)
        except SecretStoreError:
            return ""
    return resolve


def scoped_cred_resolver(settings: Settings, conn: sqlite3.Connection, *, slug: str) -> Callable[[str], str]:
    """Résolveur `credential_ref → token` **scopé à un projet** (ACL, P4). Durcit `cred_resolver` : il ne
    résout QUE le ref **lié à `slug`** (`projects.credential_ref`) — un ref appartenant à un autre projet (ou
    à aucun) dégrade en `''`, jamais le token d'autrui. Ferme la pollution *control-plane* : le résolveur
    d'un projet ne peut pas tirer le secret d'un voisin. **Lazy/Total** comme `cred_resolver` (store construit
    à la demande ; absence / illisible / hors-scope → `''`, aucune exception ici). La *policy* d'appartenance
    vit ICI (couche secrets), jamais dans `git/internal`. Import de `get_project` **paresseux** (le paquet
    `projects.registry` importe `secrets` — cycle évité)."""
    from forgemaster.projects.registry import get_project

    def resolve(ref: str) -> str:
        try:
            owned = get_project(conn, slug).get("credential_ref")
        except KeyError:
            return ""                                  # projet inconnu → rien à résoudre
        if not ref or ref != owned:
            return ""                                  # ref hors du projet demandeur → refusé (ACL)
        try:
            return build_store(settings).get(ref)
        except SecretStoreError:
            return ""
    return resolve
