"""Le producteur d'annonces : ce qu'il lit, ce qu'il signe, et ce qu'il refuse.

La garde qui vaut le plus est l'**aller-retour** : produire avec `channel_publish`, relire avec
`update_channel`. Elle échoue le jour où l'un des deux côtés dérive — ce qu'aucun test de forme ne verrait,
puisque les deux resteraient individuellement corrects.
"""
from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from forgemaster import channel_publish, update_channel

VERSION = "9.9.9"
SHA = "a" * 40
COMMITTED = "2026-08-08T10:00:00+02:00"


def _paire() -> tuple[bytes, bytes]:
    p = Ed25519PrivateKey.generate()
    return p.private_bytes_raw(), p.public_key().public_bytes_raw()


def _wheel(tmp: Path, *, public: bytes | None, sha: str = SHA, version: str = VERSION,
           stamp: dict | None = None) -> Path:
    """Un wheel minuscule mais VRAI de forme : METADATA, tampon de build, manifeste de cartes, et la
    racine de confiance si on lui en donne une. Fixture minuscule, nom fictif."""
    whl = tmp / f"forgemaster-{version}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(f"forgemaster-{version}.dist-info/METADATA",
                   f"Metadata-Version: 2.1\nName: forgemaster\nVersion: {version}\n")
        z.writestr(channel_publish.WHEEL_STAMP,
                   json.dumps(stamp if stamp is not None else {"sha": sha, "committed_at": COMMITTED}))
        z.writestr(channel_publish.WHEEL_MAPS,
                   json.dumps({"maps": [{"name": "code-map", "wheel": "cm.whl", "sha": "b" * 40,
                                         "committed_at": COMMITTED}]}))
        if public is not None:
            z.writestr(channel_publish.WHEEL_KEYS, channel_publish.trust_root_document(public))
    return whl


def _keys_file(tmp: Path, public: bytes) -> Path:
    p = tmp / update_channel.KEYS_FILE
    p.write_text(channel_publish.trust_root_document(public), encoding="utf-8")
    return p


# --- 1. la frontière, structurelle et pas déclarative -------------------------------------------------

def test_le_client_n_importe_JAMAIS_le_producteur():
    """`update_channel` est ce qu'une édition distribuée embarque. Qu'il puisse un jour importer le
    producteur ferait entrer chez l'utilisateur du code dont il n'a aucun usage — et la phrase « le client
    ne sait que vérifier » cesserait d'être une propriété du graphe d'imports pour devenir une intention.

    Vérifié par AST plutôt que par relecture : un import ajouté six mois plus tard ne se voit pas."""
    arbre = ast.parse(Path(update_channel.__file__).read_text(encoding="utf-8"))
    importes: set[str] = set()
    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            importes.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            importes.add(node.module or "")
            importes.update(f"{node.module}.{a.name}" for a in node.names)
    fautifs = sorted(m for m in importes if "channel_publish" in m)
    assert not fautifs, (
        f"update_channel importe le producteur ({fautifs}) — la frontière n'est plus structurelle")


def test_le_producteur_importe_le_client_et_ne_recopie_aucune_constante():
    """Le sens qui reste permis, et qui doit l'être : schémas, plafond, dérivation et message signé ont un
    SEUL foyer. Un producteur qui les redéfinirait se découvrirait le jour où plus rien ne vérifie."""
    src = Path(channel_publish.__file__).read_text(encoding="utf-8")
    assert "from forgemaster import update_channel" in src
    for interdit in ('"forgemaster-update-channel/1"', '"forgemaster-edition-announce/1"'):
        assert interdit not in src, f"{interdit} redéfini côté producteur au lieu d'être importé"


# --- 2. l'aller-retour -------------------------------------------------------------------------------

def test_produire_puis_relire_AVEC_LE_CODE_DU_CLIENT(tmp_path: Path):
    prive, public = _paire()
    whl = _wheel(tmp_path, public=public)
    annonce = channel_publish.build_announce(whl, lineage=["c" * 40], published_at=COMMITTED)
    payload = json.dumps(annonce, sort_keys=True, separators=(",", ":")).encode()
    fil = json.dumps(channel_publish.sign_envelope(payload, prive)).encode()

    keys = update_channel.trust_root(_keys_file(tmp_path, public))
    relu = update_channel.parse_announce(
        update_channel.verify_envelope(update_channel.parse_envelope(fil), keys))
    assert relu == annonce, "l'annonce relue n'est pas celle produite"
    assert relu["edition"]["sha"] == SHA and relu["edition"]["version"] == VERSION


def test_signer_SANS_la_separation_de_domaine_ne_verifie_pas(tmp_path: Path):
    """La séparation de domaine n'est pas décorative : sans elle, un payload signé sous la v1 serait
    rejouable tel quel dans une enveloppe v2 dont la sémantique aurait changé. Ce test mesure qu'elle est
    bien *dans* ce qui est signé — signer le payload nu produit une enveloppe qui ne vérifie PAS."""
    prive, public = _paire()
    payload = b'{"schema":"forgemaster-edition-announce/1"}'
    nue = {"schema": update_channel.ENVELOPE_SCHEMA,
           "payload": channel_publish.b64u(payload),
           "signatures": [{"key_id": update_channel.key_id(public),
                           "sig": channel_publish.b64u(
                               Ed25519PrivateKey.from_private_bytes(prive).sign(payload))}]}
    keys = update_channel.trust_root(_keys_file(tmp_path, public))
    with pytest.raises(update_channel.ChannelBadSignature):
        update_channel.verify_envelope(update_channel.parse_envelope(json.dumps(nue).encode()), keys)


# --- 3. l'annonce décrit l'ARTEFACT ------------------------------------------------------------------

def test_l_annonce_est_lue_DANS_le_wheel_et_nulle_part_ailleurs(tmp_path: Path):
    """Un producteur qui lirait le dépôt pourrait annoncer un commit et publier un autre binaire — et rien,
    côté client, ne saurait le dire : la signature couvrirait le mensonge."""
    whl = _wheel(tmp_path, public=None, sha="d" * 40, version="1.2.3")
    a = channel_publish.build_announce(whl, lineage=[], published_at=COMMITTED)
    assert a["edition"]["sha"] == "d" * 40 and a["edition"]["version"] == "1.2.3"
    assert a["edition"]["wheel"]["name"] == whl.name
    assert a["edition"]["wheel"]["size"] == whl.stat().st_size
    assert a["edition"]["maps"] == [{"name": "code-map", "sha": "b" * 40}], \
        "l'annonce recopie le manifeste local au lieu de n'en garder que de quoi SITUER"


def test_le_sha256_annonce_est_celui_du_FICHIER_publie(tmp_path: Path):
    import hashlib
    whl = _wheel(tmp_path, public=None)
    a = channel_publish.build_announce(whl, lineage=[], published_at=COMMITTED)
    assert a["edition"]["wheel"]["sha256"] == hashlib.sha256(whl.read_bytes()).hexdigest()


def test_un_wheel_sans_tampon_de_build_n_est_PAS_annoncable(tmp_path: Path):
    whl = _wheel(tmp_path, public=None, stamp={"sha": None, "committed_at": None})
    with pytest.raises(channel_publish.PublishError, match="ne sait pas de quel commit"):
        channel_publish.build_announce(whl, lineage=[], published_at=COMMITTED)


# --- 4. les refus ------------------------------------------------------------------------------------

def test_la_lignee_au_dela_du_plafond_est_REFUSEE_jamais_tronquee(tmp_path: Path):
    """Le client refuse déjà au-delà du plafond ; un plafond vérifié d'un seul côté n'en est pas un. Et
    tronquer en silence changerait « je ne peux pas te situer » en « tu n'es pas dans la lignée »."""
    whl = _wheel(tmp_path, public=None)
    trop = [f"{i:040x}" for i in range(update_channel.LINEAGE_MAX + 1)]
    with pytest.raises(channel_publish.PublishError, match="plafond"):
        channel_publish.build_announce(whl, lineage=trop, published_at=COMMITTED)
    ok = channel_publish.build_announce(whl, lineage=trop[:update_channel.LINEAGE_MAX],
                                        published_at=COMMITTED)
    update_channel.parse_announce(json.dumps(ok).encode())          # le client l'accepte, lui


def test_le_sha_annonce_ne_peut_pas_figurer_dans_sa_PROPRE_lignee(tmp_path: Path):
    whl = _wheel(tmp_path, public=None)
    with pytest.raises(channel_publish.PublishError, match="propre lignée"):
        channel_publish.build_announce(whl, lineage=["e" * 40, SHA], published_at=COMMITTED)


@pytest.mark.parametrize("longueur", [0, 31, 33, 64])
def test_une_privee_qui_ne_fait_pas_32_octets_est_NOMMEE(longueur: int):
    """64 octets est le cas piégeux : c'est la forme « étendue » que servent d'autres bibliothèques. Un
    refus muet enverrait chercher un défaut de crypto là où il y a un défaut de pipe."""
    with pytest.raises(channel_publish.PublishError, match="32"):
        channel_publish.sign_envelope(b"{}", b"\0" * longueur)


# --- 5. le key_id est dérivé, jamais déclaré ---------------------------------------------------------

def test_le_key_id_est_DERIVE_de_la_cle_qui_signe(tmp_path: Path):
    prive, public = _paire()
    env = channel_publish.sign_envelope(b"{}", prive)
    assert env["signatures"][0]["key_id"] == update_channel.key_id(public)


def test_une_enveloppe_signee_par_une_AUTRE_cle_est_un_key_id_INCONNU(tmp_path: Path):
    """Et pas une signature invalide : les deux états sont distincts, parce que le second est le seul
    indicateur de compromission qu'un système hors-ligne aura jamais."""
    autre_prive, _ = _paire()
    _, public = _paire()
    env = channel_publish.sign_envelope(b"{}", autre_prive)
    keys = update_channel.trust_root(_keys_file(tmp_path, public))
    with pytest.raises(update_channel.ChannelUnknownKey):
        update_channel.verify_envelope(update_channel.parse_envelope(json.dumps(env).encode()), keys)


def test_le_document_de_racine_de_confiance_est_relu_par_le_CLIENT(tmp_path: Path):
    """`trust_root_document` est écrit ici et lu par `trust_root` — dont la première garde est de
    **re-dériver** le `key_id`. Si les deux dérivations divergeaient, l'édition serait inutilisable, pas
    indulgente : c'est ce que ce test fige."""
    _, public = _paire()
    (k,) = update_channel.trust_root(_keys_file(tmp_path, public))
    assert k["public"] == public and k["key_id"] == update_channel.key_id(public)
