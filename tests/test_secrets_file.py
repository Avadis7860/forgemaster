"""EncryptedFileStore — round-trip, persistance, 0 plaintext au repos, clé-600, refus si altéré."""
from __future__ import annotations

import os
import stat

import pytest

from forgemaster.secrets.base import SecretNotFound, SecretStoreError
from forgemaster.secrets.file_store import EncryptedFileStore

SECRET = "ghp_th1s-1s-a-github-pat-value"


def test_put_get_roundtrip(tmp_path):
    store = EncryptedFileStore(tmp_path / "secrets")
    ref = store.put(SECRET, label="github:forgemaster")
    assert ref and ref != SECRET  # référence opaque, pas la valeur
    assert store.get(ref) == SECRET


def test_ref_is_opaque_hex(tmp_path):
    store = EncryptedFileStore(tmp_path / "secrets")
    ref = store.put(SECRET)
    assert len(ref) == 32 and all(c in "0123456789abcdef" for c in ref)  # uuid4().hex


def test_persists_across_instances(tmp_path):
    root = tmp_path / "secrets"
    ref = EncryptedFileStore(root).put(SECRET, label="x")
    # nouvelle instance (process « redémarré ») : relit la clé + le blob sur disque
    assert EncryptedFileStore(root).get(ref) == SECRET


def test_get_missing_raises(tmp_path):
    store = EncryptedFileStore(tmp_path / "secrets")
    with pytest.raises(SecretNotFound):
        store.get("deadbeef")


def test_delete_is_idempotent(tmp_path):
    store = EncryptedFileStore(tmp_path / "secrets")
    ref = store.put(SECRET)
    store.delete(ref)
    assert not store.has(ref)
    store.delete(ref)  # deuxième suppression : pas d'erreur


def test_list_entries_exposes_labels_not_values(tmp_path):
    store = EncryptedFileStore(tmp_path / "secrets")
    store.put(SECRET, label="github:forgemaster")
    entries = store.list_entries()
    assert len(entries) == 1
    assert entries[0]["label"] == "github:forgemaster"
    assert "value" not in entries[0]
    assert SECRET not in str(entries)


def test_no_plaintext_on_disk(tmp_path):
    """Invariant central : la valeur n'apparaît NULLE PART en clair (blob chiffré)."""
    root = tmp_path / "secrets"
    EncryptedFileStore(root).put(SECRET, label="github:forgemaster")
    blob = (root / "store.enc").read_bytes()
    assert SECRET.encode() not in blob
    # ni dans la clé, ni ailleurs dans le dossier
    for path in root.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()


@pytest.mark.skipif(os.name != "posix", reason="permissions POSIX")
def test_key_file_is_0600(tmp_path):
    root = tmp_path / "secrets"
    EncryptedFileStore(root).put(SECRET)
    mode = stat.S_IMODE(os.stat(root / "master.key").st_mode)
    assert mode == 0o600


def test_tampered_blob_refuses(tmp_path):
    """Blob corrompu (ou clé absente) → on lève, jamais rendre du faux clair."""
    root = tmp_path / "secrets"
    store = EncryptedFileStore(root)
    store.put(SECRET)
    (root / "store.enc").write_bytes(b"not-a-valid-fernet-token")
    with pytest.raises(SecretStoreError):
        store.get("whatever")


def test_health_is_ready_zero_config(tmp_path):
    store = EncryptedFileStore(tmp_path / "secrets")
    ready, detail = store.health()
    assert ready is True and "1ʳᵉ écriture" in detail          # clé pas encore créée, mais prêt (zéro-config)
    store.put(SECRET)
    ready2, detail2 = store.health()
    assert ready2 is True and "clé présente" in detail2 and SECRET not in detail2
