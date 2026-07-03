"""secrets.bws_store — `BwsStore` : adaptateur Bitwarden Secrets Manager (store OPTIONNEL, `cockpit[bws]`).

Transport = **SDK officiel `bitwarden-sdk`** (pas le CLI `bws`) : le chemin BWS éprouvé du framework passe par
le SDK (client in-process, une auth réutilisée, région configurable) — plus performant qu'un subprocess
réauthentifié par appel. Le `client_factory` est **injectable** : le transport reste swappable (tests, ou un
adaptateur CLI ultérieur) sans toucher l'appelant.

Modèle **bring-your-own** en v1 : l'utilisateur crée ses secrets dans Bitwarden et fournit leur **UUID** comme
`credential_ref` ; `get` les résout (avec un cache mémoire pour la durée du process). `put`/`delete` ne sont
pas supportés (on ne pilote pas l'org BWS de l'utilisateur) → `SecretUnsupported`.

RACINE de confiance : `BWS_ACCESS_TOKEN` (token du machine account), lu en env ou dans un fichier-600. Lui
seul ne peut pas venir de BWS (circulaire) ; il déverrouille tous les autres secrets. Jamais loggé ni écrit
(auth en mémoire, `state_file=None`).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .base import SecretNotFound, SecretStoreError, SecretUnsupported

# Endpoints globaux par défaut (.com). Instances EU : surcharger via env BWS_API_URL / BWS_IDENTITY_URL.
DEFAULT_API_URL = "https://api.bitwarden.com"
DEFAULT_IDENTITY_URL = "https://identity.bitwarden.com"

ClientFactory = Callable[[str, str, str], object]  # (access_token, api_url, identity_url) -> client


class BwsStore:
    """Store BWS. Sans argument, lit sa config dans l'env (`BWS_ACCESS_TOKEN`, `BWS_API_URL`, …)."""

    backend = "bws"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        token_file: str | os.PathLike[str] | None = None,
        api_url: str | None = None,
        identity_url: str | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._access_token = access_token
        self._token_file = token_file
        self._api_url = api_url or os.environ.get("BWS_API_URL", DEFAULT_API_URL)
        self._identity_url = identity_url or os.environ.get("BWS_IDENTITY_URL", DEFAULT_IDENTITY_URL)
        self._client_factory = client_factory
        self._client: object | None = None
        self._cache: dict[str, str] = {}

    # ── racine de confiance ──────────────────────────────────────────────────────────────────────────
    def _resolve_token(self) -> str:
        tok = self._access_token or os.environ.get("BWS_ACCESS_TOKEN")
        if tok:
            return tok.strip()
        raw = self._token_file or os.environ.get("BWS_ACCESS_TOKEN_FILE")
        if raw:
            path = Path(raw).expanduser()
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        raise SecretStoreError(
            "BWS_ACCESS_TOKEN absent (env, fichier-600 ou paramètre) — impossible d'ouvrir le store BWS."
        )

    # ── client (import SDK paresseux) ────────────────────────────────────────────────────────────────
    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            factory = self._client_factory or self._default_client_factory
            self._client = factory(self._resolve_token(), self._api_url, self._identity_url)
        return self._client

    @staticmethod
    def _default_client_factory(access_token: str, api_url: str, identity_url: str):  # type: ignore[no-untyped-def]
        try:
            from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
        except ImportError as e:
            raise SecretStoreError(
                "SDK Bitwarden absent — installe l'extra `cockpit[bws]` pour activer le store BWS."
            ) from e
        client = BitwardenClient(
            client_settings_from_dict(
                {
                    "apiUrl": api_url,
                    "identityUrl": identity_url,
                    "deviceType": DeviceType.SDK,
                    "userAgent": "cockpit",
                }
            )
        )
        res = client.auth().login_access_token(access_token, None)  # state_file=None → mémoire seule
        if not res.success:
            raise SecretStoreError(f"login BWS échoué : {res.error_message or 'raison inconnue'}")
        return client

    # ── API SecretStore ──────────────────────────────────────────────────────────────────────────────
    def get(self, ref: str) -> str:
        if ref in self._cache:
            return self._cache[ref]
        res = self._get_client().secrets().get(ref)  # type: ignore[attr-defined]
        if not getattr(res, "success", False) or getattr(res, "data", None) is None:
            msg = getattr(res, "error_message", None) or "raison inconnue"
            if "not found" in msg.lower() or "404" in msg:
                raise SecretNotFound(ref)
            raise SecretStoreError(f"récupération BWS échouée pour {ref!r} : {msg}")
        value: str = res.data.value
        self._cache[ref] = value
        return value

    def has(self, ref: str) -> bool:
        try:
            self.get(ref)
            return True
        except SecretNotFound:
            return False

    def put(self, value: str, *, label: str | None = None) -> str:
        raise SecretUnsupported(
            "BWS (v1) : crée le secret dans Bitwarden et donne son UUID (put non supporté)."
        )

    def delete(self, ref: str) -> None:
        raise SecretUnsupported("BWS (v1) : gère la suppression dans Bitwarden (delete non supporté).")

    def list_entries(self) -> list[dict[str, str | None]]:
        return []  # bring-your-own : on n'énumère pas l'org BWS de l'utilisateur en v1
