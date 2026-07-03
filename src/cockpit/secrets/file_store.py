"""secrets.file_store — `EncryptedFileStore` : le store PAR DÉFAUT, portable et zéro-config.

Chiffrement authentifié au repos via **Fernet** (`cryptography` : AES-CBC + HMAC-SHA256, IV géré en interne —
on ne roule PAS sa propre crypto). Deux fichiers sous `home/secrets/` :
- `master.key`  — la clé Fernet, en clair mais **0600** (dossier 0700). C'est la RACINE de confiance :
  l'unique secret non-chiffré sur l'hôte, protégé par les seules permissions FS. Un dump de la DB ou d'un
  backup n'expose donc rien tant que la clé n'est pas emportée avec. Ce n'est PAS de la magie (un attaquant
  qui lit déjà le home a la clé) — réduction de surface : N tokens éparpillés → 1 racine verrouillée.
- `store.enc`   — le blob chiffré, un JSON `{ref: {value, label}}` re-chiffré à chaque écriture.

`cryptography` est importé **paresseusement** (dans les méthodes) : le socle `import cockpit.secrets` reste
stdlib-pur ; la dép n'est tirée que lorsqu'on ouvre effectivement le store fichier.
"""
from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path

from .base import SecretNotFound, SecretStoreError

_KEY_NAME = "master.key"
_BLOB_NAME = "store.enc"


class EncryptedFileStore:
    """Store fichier chiffré. Passer le dossier racine (typiquement `settings.secrets_dir`)."""

    backend = "file"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._key_path = self._root / _KEY_NAME
        self._blob_path = self._root / _BLOB_NAME
        self._cipher_obj: object | None = None  # Fernet, construit paresseusement

    # ── crypto (import paresseux) ────────────────────────────────────────────────────────────────────
    def _cipher(self):  # type: ignore[no-untyped-def]
        if self._cipher_obj is None:
            from cryptography.fernet import Fernet

            self._cipher_obj = Fernet(self._load_or_create_key())
        return self._cipher_obj

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):  # best-effort ; no-op si FS sans perms POSIX
            os.chmod(self._root, 0o700)  # dossier privé

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        self._ensure_root()
        # O_EXCL + 0600 : la clé naît privée, sans fenêtre lisible entre create et chmod.
        fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    # ── I/O du blob ──────────────────────────────────────────────────────────────────────────────────
    def _read_all(self) -> dict[str, dict[str, str]]:
        if not self._blob_path.exists():
            return {}
        from cryptography.fernet import InvalidToken

        try:
            plain = self._cipher().decrypt(self._blob_path.read_bytes())
        except InvalidToken as e:  # clé fausse ou blob altéré → on refuse plutôt que rendre du faux
            raise SecretStoreError(
                f"déchiffrement impossible de {self._blob_path} (clé absente/erronée ou blob altéré)"
            ) from e
        data = json.loads(plain.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _write_all(self, data: dict[str, dict[str, str]]) -> None:
        self._ensure_root()
        token = self._cipher().encrypt(json.dumps(data).encode("utf-8"))
        tmp = self._blob_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(token)
        os.replace(tmp, self._blob_path)  # remplacement atomique

    # ── API SecretStore ──────────────────────────────────────────────────────────────────────────────
    def put(self, value: str, *, label: str | None = None) -> str:
        data = self._read_all()
        ref = uuid.uuid4().hex  # référence opaque générée (jamais un UUID littéral en dur)
        data[ref] = {"value": value, "label": label or ""}
        self._write_all(data)
        return ref

    def get(self, ref: str) -> str:
        entry = self._read_all().get(ref)
        if entry is None:
            raise SecretNotFound(ref)
        return entry["value"]

    def delete(self, ref: str) -> None:
        data = self._read_all()
        if data.pop(ref, None) is not None:
            self._write_all(data)

    def has(self, ref: str) -> bool:
        return ref in self._read_all()

    def list_entries(self) -> list[dict[str, str | None]]:
        return [{"ref": ref, "label": (e.get("label") or None)} for ref, e in self._read_all().items()]

    def health(self) -> tuple[bool, str]:
        """Zéro-config : toujours prêt (la clé est créée à la 1ʳᵉ écriture). Le détail pointe la racine de
        confiance (clé-600) — jamais son contenu."""
        state = "clé présente" if self._key_path.exists() else "clé créée à la 1ʳᵉ écriture"
        return True, f"coffre fichier chiffré ({state}) sous {self._root}"
