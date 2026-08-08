"""channel_publish — le **producteur** de l'annonce signée : ce que `update_channel` passe sa vie à lire.

Symétrique de `update_channel`, et jamais fondu avec lui. La frontière est **structurelle**, pas déclarative
(patron de la session F, repris verbatim) : ce module importe `update_channel` — les schémas, le plafond de
lignée, la dérivation de `key_id`, le message signé — et **l'inverse est impossible**, gardé par AST
(`test_channel_publish.py`). Deux propriétés en sortent, qu'aucun commentaire ne pourrait tenir :

- **rien du chemin de mise à jour ne peut atteindre le signeur** : `update_channel` est ce qu'une édition
  charge pour se tenir au courant, et il ne tire jamais ce module ;
- le **producteur** ne redéfinit aucune constante : un schéma, un domaine de signature ou un plafond qui
  aurait deux valeurs se découvrirait le jour où plus personne ne vérifie.

**Ce qui n'est pas distribué est la CLÉ, pas ce fichier.** Il vit sous `src/forgemaster/`, donc il voyage
dans le wheel comme tout le reste ; seul le point d'entrée mainteneur (`scripts/publish_channel.py`) en est
exclu. Prétendre le contraire serait de la rhétorique — le packaging ne le fait pas. Ce module est **inerte**
sans une privée, et une privée n'existe que dans le coffre.

**L'annonce est fonction de l'ARTEFACT, pas du poste qui l'a bâti.** `version`, `sha`, `committed_at` et
`maps` sont lus **dans le wheel** ; le SHA-256 et la taille sont ceux du fichier qu'on publiera. Un
producteur qui lirait le répertoire de travail pourrait annoncer un commit et publier un autre binaire — et
rien, côté client, ne saurait le dire : la signature couvrirait le mensonge.
"""
from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path

from forgemaster import update_channel

# Ce que le wheel porte et que l'annonce décrit. Les deux chemins sont ceux que `deploy/build-wheel.sh`
# garantit par assertion (étapes 2b et 4/4) — leur absence n'est donc pas un cas dégradé à tolérer ici,
# c'est un wheel qu'on refuse d'annoncer.
WHEEL_STAMP = "forgemaster/_build.json"
WHEEL_MAPS = "forgemaster/_maps/maps.json"
WHEEL_KEYS = f"forgemaster/{update_channel.KEYS_DIR}/{update_channel.KEYS_FILE}"


class PublishError(Exception):
    """Refus de produire une annonce. Toujours nommé : à ce stade, un défaut passé sous silence devient une
    signature valide sur un mensonge."""


def b64u(raw: bytes) -> str:
    """base64url **avec padding** — exactement ce que `update_channel._b64d` accepte (`validate=True`).
    L'encodeur vit ici et pas chez le client : une édition distribuée n'a rien à encoder."""
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _member(z: zipfile.ZipFile, nom: str) -> bytes:
    try:
        return z.read(nom)
    except KeyError:
        raise PublishError(
            f"{nom} absent du wheel — il n'est pas annonçable. `deploy/build-wheel.sh` le garantit par "
            f"assertion ; un wheel qui en manque n'a pas été bâti par lui.") from None


def _version(z: zipfile.ZipFile) -> str:
    """La version telle que `pip` la lira : le `Version:` du METADATA, jamais le nom du fichier.

    Le nom d'un wheel se renomme d'un `mv` ; le METADATA est ce que l'installation croira."""
    metas = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
    if len(metas) != 1:
        raise PublishError(f"{len(metas)} METADATA dans le wheel au lieu d'un seul — artefact non concluant")
    for ligne in z.read(metas[0]).decode("utf-8", "replace").splitlines():
        if ligne.startswith("Version:"):
            return ligne.split(":", 1)[1].strip()
    raise PublishError(f"{metas[0]} ne déclare pas de `Version:` — l'annonce ne désignerait rien")


def _maps(z: zipfile.ZipFile) -> list[dict]:
    """Les cartes de l'édition, réduites à ce que l'annonce déclare : `{name, sha}`.

    On n'y recopie ni le nom du wheel ni sa date — le manifeste local les porte déjà pour qui a l'édition,
    et une annonce n'est pas un miroir de l'artefact : c'est ce qu'il faut pour SITUER une instance."""
    try:
        data = json.loads(_member(z, WHEEL_MAPS))
        return [{"name": m["name"], "sha": m["sha"]} for m in data["maps"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise PublishError(f"{WHEEL_MAPS} illisible ({exc}) — l'édition ne déclare pas ses cartes") from None


def build_announce(wheel: Path, *, lineage: list[str], published_at: str) -> dict:
    """L'annonce, lue **dans** le wheel. Aucun accès au dépôt, aucune variable d'environnement.

    La lignée est fournie par l'appelant (elle se mesure avec git, ce qui n'est pas le métier d'ici) mais
    elle est **contrôlée** ici : au-delà du plafond on **refuse** au lieu de tronquer. Le plafond est déjà
    refusé à la lecture (`parse_announce`) ; un plafond vérifié d'un seul côté n'en est pas un — et
    tronquer en silence changerait « je ne peux pas te situer » en « tu n'es pas dans la lignée »,
    c'est-à-dire un aveu en verdict.
    """
    octets = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as z:
        tampon = json.loads(_member(z, WHEEL_STAMP))
        version, cartes = _version(z), _maps(z)
    sha = tampon.get("sha")
    if not sha:
        raise PublishError(
            f"{WHEEL_STAMP} sans `sha` — ce wheel ne sait pas de quel commit il est né, donc aucune "
            f"instance ne pourra se situer par rapport à lui")

    if len(lineage) > update_channel.LINEAGE_MAX:
        raise PublishError(
            f"lignée de {len(lineage)} entrées au-delà du plafond {update_channel.LINEAGE_MAX} — refusée "
            f"plutôt que tronquée : le client refuse déjà à la lecture, et un plafond qu'on ne vérifie "
            f"que d'un côté n'est pas un plafond")
    if sha in lineage:
        raise PublishError(
            f"le SHA annoncé ({sha[:12]}) figure dans sa propre lignée — la lignée est ce qui PRÉCÈDE "
            f"l'édition annoncée, pas elle-même")

    return {
        "schema": update_channel.ANNOUNCE_SCHEMA,
        "published_at": published_at,
        "edition": {
            "version": version,
            "sha": sha,
            "committed_at": tampon.get("committed_at"),
            "wheel": {"name": wheel.name, "sha256": hashlib.sha256(octets).hexdigest(),
                      "size": len(octets)},
            "maps": cartes,
        },
        "lineage": list(lineage),
    }


def sign_envelope(payload: bytes, private_raw: bytes) -> dict:
    """L'enveloppe **du fil** : `{schema, payload: <b64u>, signatures: [{key_id, sig: <b64u>}]}`.

    Le `key_id` n'est **pas** un argument : il est dérivé de la publique déduite de la privée qui signe. Le
    passer serait rouvrir exactement le mensonge que la dérivation existe pour rendre impossible — une
    signature qui désigne une clé et vérifie sous une autre.

    Ce qui est signé est `update_channel.signing_message(payload)`, un seul foyer partagé avec le
    vérificateur : la séparation de domaine ne vaut que si les deux côtés la calculent au même endroit.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if len(private_raw) != 32:
        # Mesuré : `from_private_bytes` lève un `ValueError` correct, mais qui ne dit pas d'OÙ vient la
        # clé. Le refus nommé ici évite d'aller chercher un défaut de crypto là où il y a un défaut de pipe.
        raise PublishError(
            f"clé privée de {len(private_raw)} octets — une privée Ed25519 brute en fait 32. Une valeur "
            f"tronquée par un pipe ou décodée avec le mauvais alphabet ressemble exactement à ça.")
    priv = Ed25519PrivateKey.from_private_bytes(private_raw)
    public = priv.public_key().public_bytes_raw()
    return {
        "schema": update_channel.ENVELOPE_SCHEMA,
        "payload": b64u(payload),
        "signatures": [{"key_id": update_channel.key_id(public),
                        "sig": b64u(priv.sign(update_channel.signing_message(payload)))}],
    }


def trust_root_document(public_raw: bytes) -> str:
    """Le corps de `_keys/release-keys.json`, **dérivé** de la publique et rien d'autre.

    Il vit ici, et pas dans la cérémonie qui a fait naître la paire : celle-ci tourne dans le vault, où
    `key_id` n'existe pas. Un identifiant dérivé qui aurait deux implémentations n'aurait plus aucune des
    propriétés pour lesquelles on l'a dérivé — et `trust_root()` re-dérive de toute façon à chaque lecture,
    donc une seconde implémentation qui divergerait rendrait l'édition inutilisable, pas indulgente."""
    doc = {"keys": [{"key_id": update_channel.key_id(public_raw), "public": b64u(public_raw)}]}
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"
