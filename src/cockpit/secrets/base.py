"""secrets.base — le contrat du coffre de credentials du cockpit.

Un `SecretStore` range des secrets (tokens push/mirror…) HORS de la base et hors du working-tree, et rend
une **référence opaque** (`credential_ref`) que la DB stocke à la place de la valeur. À l'usage (writeback
git), la couche merge résout la ref → valeur via le store actif, l'injecte le temps de l'op, ne la persiste
jamais. La ref est **spécifique au backend** (clé interne pour le fichier chiffré, UUID pour BWS) : le store
est choisi **globalement par instance** (`COCKPIT_SECRET_STORE`) — changer de backend = re-onboard.

Le Protocol reste **stdlib-pur** : aucune dépendance crypto/SDK ici (elles sont tirées paresseusement par les
implémentations concrètes). Le socle importe donc `secrets` sans payer `cryptography` ni `bitwarden-sdk`.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class SecretStoreError(RuntimeError):
    """Échec générique du store : backend indisponible, I/O, déchiffrement, auth BWS…"""


class SecretNotFound(SecretStoreError):
    """La référence demandée n'existe pas dans le store actif. Sous-type d'erreur du store (un `except
    SecretStoreError` large l'attrape aussi ; l'appelant peut la distinguer pour un message dédié)."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"secret introuvable pour la référence {ref!r}")
        self.ref = ref


class SecretUnsupported(SecretStoreError):
    """L'opération n'est pas supportée par ce backend (ex. `put` sur BWS bring-your-own : l'utilisateur crée
    le secret dans Bitwarden et fournit son UUID)."""


@runtime_checkable
class SecretStore(Protocol):
    """Coffre pluggable. Les implémentations vivent dans `file_store` / `bws_store` ; on les construit via
    `secrets.build_store(settings)`."""

    #: nom court du backend, pour le diagnostic/onboarding ("file" | "bws").
    backend: str

    def put(self, value: str, *, label: str | None = None) -> str:
        """Range un NOUVEAU secret ; retourne sa référence opaque (générée par le backend). `label` est un
        libellé humain optionnel (affiché par le wizard), jamais le secret lui-même."""
        ...

    def get(self, ref: str) -> str:
        """Résout une référence en sa valeur. Lève `SecretNotFound` si la ref est inconnue."""
        ...

    def delete(self, ref: str) -> None:
        """Supprime le secret désigné. Idempotent (pas d'erreur si déjà absent)."""
        ...

    def has(self, ref: str) -> bool:
        """Vrai si la référence est résolvable. Ne lève pas pour une simple absence."""
        ...

    def list_entries(self) -> list[dict[str, str | None]]:
        """Métadonnées SANS valeurs : `[{"ref": …, "label": …}]` — pour l'écran d'onboarding."""
        ...
