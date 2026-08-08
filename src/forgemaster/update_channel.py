"""update_channel — la moitié RÉSEAU du cycle de mise à jour : apprendre qu'une version existe.

Ce module est le **pendant exact** de `update.py`, et leur frontière est la raison d'être des deux :

- `update.py` **pose** un wheel qu'on lui désigne. Local, hors-ligne, **aucun** réseau — jamais.
- `update_channel.py` **apprend** qu'une édition existe. Réseau, et **rien d'autre** : il ne télécharge
  aucun binaire, ne pose rien, ne propose rien. Il lit une annonce **signée** et écrit ce qu'il a compris.

Cette frontière n'est pas une élégance : c'est ce qui borne le rayon d'explosion d'une clé volée à une
**notification mensongère**, jamais à une exécution. Le canal ANNONCE (« la version X existe, voici le
SHA-256 de son wheel ») ; c'est l'utilisateur qui va chercher le fichier, et `update apply` qui le pose
après son consentement. *La voie hors-ligne est la voie de secours de la voie en ligne.*

Ce qui est verrouillé ailleurs et **appliqué** ici, pas re-débattu — `docs/specs/update-channel-trust-root.md`
(la racine de confiance) et `docs/specs/update-channel-manifest.md` (le format) :

- **On vérifie des OCTETS, jamais un objet re-sérialisé.** Le payload voyage encodé et reste **opaque**
  jusqu'à ce que sa signature ait vérifié. Aucune canonicalisation JSON n'entre dans la vérification :
  on ne la résout pas, on la **supprime**.
- **Une enveloppe d'UN SEUL fichier**, jamais une signature détachée : une publication en deux fichiers
  n'est pas atomique, et un faux « invalide » sur une paire mal assortie est le pire des verdicts — celui
  qui apprend à ignorer l'alarme.
- **`signatures` est une LISTE**, même à un élément. C'est ce qui rend possible le chevauchement de
  rotation, et ça ne se rétrofite pas dans un format que des tiers ont déjà lu.
- **`key_id` est DÉRIVÉ de la clé** et **re-dérivé** à chaque lecture : un identifiant qu'on ne recalcule
  pas n'est qu'un nom, et un nom peut mentir sur la clé qu'il désigne.
- **Jamais « essaie toutes les clés »** : le `key_id` doit être dans le jeu embarqué **ET** la signature
  doit vérifier **sous cette clé**. Fondre les deux transformerait une incohérence en succès silencieux.
- **`key_id` inconnu ≠ signature invalide.** Deux états distincts, parce que le premier est le seul
  indicateur de compromission (ou de rotation) qu'un système hors-ligne aura jamais.
- **Absence n'est pas panne ; un juge PRÉSENT qui plante est un ÉCHEC.** Une édition sans racine de
  confiance ne va **pas** sur le réseau du tout : elle ne pourrait rien vérifier de ce qu'elle ramènerait.

Ce module **ne lève pas** vers ses appelants de haut niveau (`refresh`, `read_state`) : un canal muet ne
doit jamais faire tomber un daemon. Il lève, en revanche, dans ses fonctions de lecture pures — c'est là
que les refus sont **nommés**, et `refresh` les traduit en états.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from forgemaster.config import Settings

# --- format ------------------------------------------------------------------------------------------
#
# DEUX schémas, versionnés SÉPARÉMENT, et ce n'est pas une coquetterie : l'enveloppe (comment on signe) et
# l'annonce (ce qu'on dit) n'ont aucune raison de vieillir ensemble. Fusionner leurs versions obligerait à
# bousculer l'une pour faire évoluer l'autre.
ENVELOPE_SCHEMA = "forgemaster-update-channel/1"
ANNOUNCE_SCHEMA = "forgemaster-edition-announce/1"

# Séparation de domaine : ce qui est signé n'est pas le payload nu mais `<schéma d'enveloppe>\n<payload>`.
# Sans elle, un payload signé sous la v1 serait rejouable tel quel dans une enveloppe v2 dont la sémantique
# aurait changé. Une ligne, et le rejeu inter-protocole n'existe pas.
_DOMAIN = ENVELOPE_SCHEMA.encode() + b"\n"

KEY_ID_LEN = 16          # préfixe hexa du SHA-256 des octets bruts de la publique (64 bits — assez pour
#                          DÉSIGNER une clé parmi trois, jamais pour l'authentifier : c'est la signature qui
#                          authentifie, le `key_id` ne fait que dire laquelle essayer).
LINEAGE_MAX = 50         # bornée à la publication ET refusée au-delà à la lecture : un plafond qu'on ne
#                          vérifie que d'un côté n'est pas un plafond.
MAX_BODY = 256 * 1024    # 256 Kio — quatre ordres de grandeur au-dessus d'une annonce réelle (quelques Kio),
#                          et très loin de ce qui pourrait étouffer un parseur.
FETCH_TIMEOUT_S = 10     # celui d'`apply_update` — deux chemins réseau du même produit n'ont aucune raison
#                          d'attendre différemment.
POLL_INTERVAL_S = 6 * 3600  # un daemon cockpit vit des SEMAINES : un contrôle au boot seul ne notifierait
#                          jamais rien. 6 h borne l'ignorance à 6 h pour 4 requêtes par jour.

# L'URL par défaut : un asset de Release, donc une adresse qui NE CHANGE PAS de version en version, servie
# anonymement, sans jeton, et SANS l'API GitHub (le poll de Releases est explicitement écarté — il exige un
# compte que l'utilisateur n'a pas de raison d'avoir). Publier une annonce n'est ainsi PAS un commit sur
# `main`, que ce dépôt n'avance que promu depuis un `dev` vert.
DEFAULT_URL = "https://github.com/Avadis7860/forgemaster/releases/latest/download/channel.json"
ENV_URL = "FORGEMASTER_UPDATE_CHANNEL_URL"

CHANNEL = "channel"          # `settings.home / channel /` — même convention que `updates` et `wheels`
CHANNEL_STATE = "last.json"
# Le cache est versionné pour la MÊME raison que le manifeste servi, et c'est facile à manquer : il est écrit
# par le binaire d'AVANT une mise à jour et relu par celui d'APRÈS. Contrairement à `_maps/maps.json`, il ne
# voyage PAS avec son lecteur — l'asymétrie qui dispense l'un de se versionner condamne l'autre à le faire.
# Un cache de schéma inconnu se lit comme ABSENT (« jamais interrogé ») : c'est de l'état reconstructible au
# tour suivant, donc le jeter est honnête là où refuser serait une panne inventée.
STATE_SCHEMA = "forgemaster-update-channel-state/1"
KEYS_DIR = "_keys"           # embarqué au wheel comme SOURCE SUIVIE (porte `packages` de hatch), pas comme
KEYS_FILE = "release-keys.json"  # artefact de build : une clé publique n'est pas produite par le build.


class ChannelError(Exception):
    """Racine des refus du canal. On ne l'attrape jamais telle quelle là où l'état doit être distingué."""


class ChannelMalformed(ChannelError):
    """Ce qu'on a lu n'a pas la forme annoncée — ou le plafond est dépassé. Rien n'a été cru."""


class ChannelUnknownKey(ChannelError):
    """Le manifeste est signé par une clé que cette édition ne connaît pas. **Distinct** d'une signature
    invalide : c'est le signal d'une rotation qu'on n'a pas suivie — ou de quelqu'un qui sonde."""


class ChannelBadSignature(ChannelError):
    """La signature ne vérifie pas sous la clé qu'elle désigne. Les octets ont bougé, ou le signataire n'a
    pas la privée qu'il prétend avoir."""


class ChannelUnreachable(ChannelError):
    """Le manifeste n'a pas pu être atteint. **Ce n'est pas une panne du produit** : c'est un réseau, un
    proxy, un hôte hors-ligne. Ce qu'on savait avant reste vrai, il vieillit simplement."""


# --- lecture pure ------------------------------------------------------------------------------------


def key_id(public_bytes: bytes) -> str:
    """Le `key_id` d'une clé publique : préfixe du SHA-256 de ses octets BRUTS. **PUR**. Dérivé et non
    attribué — un compteur ou une date pourraient désigner une autre clé que celle qu'ils nomment."""
    return hashlib.sha256(public_bytes).hexdigest()[:KEY_ID_LEN]


def keys_dir() -> Path:
    """Le dossier `forgemaster/_keys` de l'édition INSTALLÉE. Composition de chemin, **PURE** : il peut ne
    pas exister — c'est le cas normal tant que la cérémonie de génération n'a pas eu lieu, et `trust_root`
    le dit alors franchement plutôt que d'inventer une confiance."""
    return Path(__file__).resolve().parent / KEYS_DIR


def trust_root(keys_path: Path | None = None) -> list[dict]:
    """Le jeu de clés publiques que cette édition accepte : `[{key_id, public}]`, `public` en octets bruts.

    **Absent ⇒ `[]`** (pas de racine de confiance embarquée — un état honnête, pas une erreur) ; **présent
    mais illisible ou incohérent ⇒ `ChannelMalformed`**. C'est la doctrine de dégradé d'`apply_update`,
    reprise verbatim : *absence n'est pas panne, mais un juge PRÉSENT qui plante est un ÉCHEC.*

    Le `key_id` déclaré est **re-dérivé** de la clé et confronté : un identifiant dérivé qu'on ne recalcule
    pas n'est qu'un nom, et il n'a alors plus aucune des propriétés pour lesquelles on l'a dérivé."""
    p = keys_path if keys_path is not None else keys_dir() / KEYS_FILE
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ChannelMalformed(f"{p} présent mais illisible ({exc}) — racine de confiance non exploitable "
                               f"; on refuse plutôt que de dégrader en « aucune clé »") from None
    try:
        data = json.loads(raw)
        entries = data["keys"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ChannelMalformed(f"{p} illisible ({exc}) — racine de confiance non exploitable") from None
    if not isinstance(entries, list) or not entries:
        raise ChannelMalformed(f"{p} ne déclare aucune clé — une racine de confiance VIDE n'est pas la même "
                               f"chose qu'une racine ABSENTE, et on ne les confond pas")
    out: list[dict] = []
    for i, e in enumerate(entries):
        try:
            declared, pub = e["key_id"], _b64d(e["public"])
        except (KeyError, TypeError, ChannelMalformed) as exc:
            raise ChannelMalformed(f"{p} : clé #{i} inexploitable ({exc})") from None
        derived = key_id(pub)
        if declared != derived:
            raise ChannelMalformed(
                f"{p} : clé #{i} annonce le key_id {declared!r} mais ses octets donnent {derived!r} — "
                f"un key_id DÉRIVÉ qu'on ne re-dérive pas n'est qu'un nom, et celui-ci ment")
        # Deux clés sous le même `key_id` ne peuvent pas coexister : la vérification indexe PAR `key_id`,
        # donc l'une des deux serait silencieusement ignorée — et une clé qu'on croit accepter sans
        # l'accepter est le pire état d'une rotation. On refuse le jeu entier plutôt que d'en garder la
        # moitié.
        if any(k["key_id"] == derived for k in out):
            raise ChannelMalformed(f"{p} : le key_id {derived!r} est déclaré deux fois — l'une des deux "
                                   f"clés serait ignorée en silence")
        out.append({"key_id": derived, "public": pub})
    return out


def parse_envelope(raw: bytes) -> dict:
    """L'enveloppe, et **rien de son contenu** : `{schema, payload: bytes, signatures: [{key_id, sig}]}`.

    Le payload est **décodé** (défaire l'encodage de transport n'est pas le lire) mais **jamais parsé** :
    ces octets restent opaques jusqu'à ce que `verify_envelope` ait tranché. C'est ce qui empêche le
    parseur JSON d'être la première surface d'attaque, avant tout contrôle.

    Refus qui **nomment** le schéma lu et celui qu'on sait lire — patron de `restore.load_manifest`, repris
    et non réinventé : un refus qui ne dit pas ce qu'il attendait n'est pas actionnable."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChannelMalformed(f"manifeste illisible (JSON invalide) : {exc}") from None
    if not isinstance(data, dict):
        raise ChannelMalformed("manifeste illisible — un objet JSON était attendu")
    if data.get("schema") != ENVELOPE_SCHEMA:
        raise ChannelMalformed(
            f"schéma d'enveloppe {data.get('schema')!r} inconnu — cette édition lit {ENVELOPE_SCHEMA!r} "
            f"et refuse de deviner")
    sigs = data.get("signatures")
    # La LISTE est le format, pas une commodité : un scalaire accepté ici rendrait impossible le
    # chevauchement de rotation (deux signatures pendant la fenêtre) sans casser le format chez les
    # instances qui l'ont déjà lu. On refuse donc le scalaire au lieu de l'envelopper par indulgence.
    if not isinstance(sigs, list) or not sigs:
        raise ChannelMalformed(
            f"`signatures` doit être une LISTE non vide (lu : {type(sigs).__name__}) — le pluriel est le "
            f"format dès le premier jour, il ne se rétrofite pas")
    out_sigs = []
    for i, s in enumerate(sigs):
        if not isinstance(s, dict) or "key_id" not in s or "sig" not in s:
            raise ChannelMalformed(f"signature #{i} incomplète — `key_id` et `sig` sont requis")
        out_sigs.append({"key_id": s["key_id"], "sig": _b64d(s["sig"])})
    if "payload" not in data:
        raise ChannelMalformed("manifeste sans `payload` — il n'y a rien à vérifier")
    return {"schema": data["schema"], "payload": _b64d(data["payload"]), "signatures": out_sigs}


def verify_envelope(envelope: dict, keys: list[dict]) -> bytes:
    """Rend les octets du payload **si et seulement si** une signature vérifie. Sinon lève — jamais un
    retour dégradé, jamais un booléen qu'un appelant pourrait oublier de lire.

    Deux conditions, **toutes deux** requises : le `key_id` de la signature est dans le jeu embarqué, ET la
    signature vérifie **sous cette clé-là**. Jamais un « essaie toutes les clés » : ça transformerait une
    incohérence (une signature qui désigne A mais vérifie sous B) en **succès silencieux**.

    Une enveloppe peut porter plusieurs signatures — c'est le chevauchement de rotation. **Une seule**
    valide suffit : pendant la fenêtre, une instance ne connaît qu'une des deux clés, par construction.

    Ordre des verdicts quand rien ne vérifie : une signature qui désignait une clé **connue** et a échoué
    l'emporte sur un `key_id` inconnu. C'est le signal le plus fort — quelqu'un a prétendu détenir une clé
    qu'on accepte — et le noyer sous « rotation non suivie » ferait chercher la mauvaise réparation."""
    if not keys:
        raise ChannelUnknownKey("cette édition n'embarque aucune racine de confiance — elle ne peut "
                                "vérifier aucun manifeste, et ne prétend donc rien")
    connues = {k["key_id"]: k["public"] for k in keys}
    message = _DOMAIN + envelope["payload"]
    inconnus, invalides = [], []
    for s in envelope["signatures"]:
        pub = connues.get(s["key_id"])
        if pub is None:
            inconnus.append(s["key_id"])
            continue
        if _ed25519_verify(pub, s["sig"], message):
            return envelope["payload"]
        invalides.append(s["key_id"])
    if invalides:
        raise ChannelBadSignature(
            f"signature invalide sous la clé {', '.join(invalides)} — les octets signés ne sont pas ceux "
            f"qu'on a lus")
    raise ChannelUnknownKey(
        f"manifeste signé par une clé inconnue de cette édition ({', '.join(inconnus)}) — rotation non "
        f"suivie, ou signataire illégitime. Les deux se réparent, mais pas de la même façon.")


def parse_announce(raw: bytes) -> dict:
    """L'annonce, **et seulement après vérification** : ce module ne l'appelle que derrière
    `verify_envelope`. Fonction **PURE**, testable seule.

    Elle valide ce qu'une phase ultérieure devra LIRE, pas davantage : un validateur qui exige des champs
    que personne n'utilise transforme un enrichissement du format en panne."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChannelMalformed(f"annonce illisible (JSON invalide) : {exc}") from None
    if not isinstance(data, dict):
        raise ChannelMalformed("annonce illisible — un objet JSON était attendu")
    if data.get("schema") != ANNOUNCE_SCHEMA:
        raise ChannelMalformed(
            f"schéma d'annonce {data.get('schema')!r} inconnu — cette édition lit {ANNOUNCE_SCHEMA!r} et "
            f"refuse de deviner")
    ed = data.get("edition")
    if not isinstance(ed, dict):
        raise ChannelMalformed("annonce sans `edition` — il n'y a rien d'annoncé")
    for champ in ("version", "sha"):
        if not isinstance(ed.get(champ), str) or not ed[champ]:
            raise ChannelMalformed(f"`edition.{champ}` manquant ou vide — l'annonce ne désigne rien")
    wheel = ed.get("wheel")
    if not isinstance(wheel, dict) or not isinstance(wheel.get("sha256"), str) or not wheel["sha256"]:
        # Le SHA-256 n'est pas décoratif : le canal ANNONCE, donc c'est la SEULE chose qui permettra de
        # confronter le fichier que l'utilisateur ira chercher à ce qu'on a signé. Une annonce sans lui
        # serait une invitation à faire confiance à un téléchargement.
        raise ChannelMalformed("`edition.wheel.sha256` manquant — sans lui l'annonce ne peut être "
                               "confrontée à aucun fichier, et n'annonce donc rien de vérifiable")
    lineage = data.get("lineage", [])
    if not isinstance(lineage, list) or not all(isinstance(x, str) for x in lineage):
        raise ChannelMalformed("`lineage` doit être une liste de SHA")
    if len(lineage) > LINEAGE_MAX:
        # Refus, jamais troncature : tronquer en silence changerait « je ne peux pas te situer » en « tu
        # n'es pas dans la lignée », c'est-à-dire un aveu en verdict.
        raise ChannelMalformed(f"lignée de {len(lineage)} entrées au-delà du plafond {LINEAGE_MAX} — "
                               f"refusée plutôt que tronquée en silence")
    return data


# --- réseau ------------------------------------------------------------------------------------------


def channel_url() -> str:
    """L'URL du manifeste. Surchargeable par `FORGEMASTER_UPDATE_CHANNEL_URL` — et c'est **inoffensif** :
    l'URL est un paramètre, jamais une garantie. Ce qui porte la confiance est la signature, pas l'adresse
    ni le TLS. C'est aussi ce qui rend les bancs de preuve possibles sans clause spéciale dans le code."""
    return os.environ.get(ENV_URL) or DEFAULT_URL


def fetch(url: str, *, timeout: float = FETCH_TIMEOUT_S, max_bytes: int = MAX_BODY) -> bytes:
    """Le corps du manifeste, plafonné **en flux**. Impur (socket).

    Le plafond est appliqué **avant matérialisation** — `read(max_bytes + 1)` ne ramène jamais plus que le
    plafond plus un octet, l'octet qui sert justement à savoir qu'on l'a dépassé. Lire d'abord et mesurer
    ensuite serait un plafond qui ne protège de rien (défaut déjà payé une fois par le canal de contenu).

    La redirection est **suivie** (un asset de Release renvoie 302 vers un CDN) et c'est sans conséquence :
    on ne fait confiance ni à l'hôte, ni au certificat, ni au chemin — uniquement à la signature. Seul le
    **schéma** est contrôlé, pour qu'une surcharge d'URL ne puisse pas transformer un GET en lecture de
    fichier local."""
    scheme = urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise ChannelMalformed(f"schéma d'URL {scheme!r} refusé — le canal parle http(s), et rien d'autre")
    req = urllib.request.Request(url, headers={"User-Agent": "forgemaster-update-channel"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body: bytes = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise ChannelUnreachable(f"{url} → HTTP {exc.code} ({exc.reason})") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChannelUnreachable(f"{url} injoignable : {exc}") from None
    if len(body) > max_bytes:
        raise ChannelMalformed(f"manifeste au-delà du plafond de {max_bytes} octets — refusé sans être lu")
    return body


# --- état sur disque ---------------------------------------------------------------------------------


def state_path(settings: Settings) -> Path:
    """Où le canal écrit ce qu'il sait. **Sur le disque**, et pas en mémoire, pour la raison déjà payée par
    `update.run_state` : le processus qui répondra à la question n'est ni celui qui a mesuré, ni forcément
    le même binaire."""
    return settings.home / CHANNEL / CHANNEL_STATE


_JAMAIS = {"at": None, "state": "never", "reason": "le canal n'a encore rien lu"}


def read_state(settings: Settings) -> dict:
    """Ce que le canal sait, **sans réseau et sans jamais lever**. Un état absent est un état valide : « je
    n'ai jamais regardé » n'est pas « rien n'existe », et les confondre serait un faux-vert.

    Un cache de **schéma inconnu** est lu comme absent. C'est le cas de figure qu'on oublie : ce fichier est
    écrit par le binaire d'AVANT une mise à jour et relu par celui d'APRÈS — il ne voyage pas avec son
    lecteur. Le jeter est honnête (l'état se reconstruit au tour suivant) là où refuser inventerait une
    panne, et là où le lire au jugé ferait planter une CLI sur une clé absente."""
    data = None
    try:
        data = json.loads(state_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        return {"last_success": None, "last_attempt": dict(_JAMAIS)}
    succes = data.get("last_success")
    if succes is not None and not isinstance(succes, dict):
        succes = None
    tentative = data.get("last_attempt")
    return {"last_success": succes,
            "last_attempt": tentative if isinstance(tentative, dict) else dict(_JAMAIS)}


# Un état d'échec n'a pas le même POIDS qu'un autre, et un niveau de log unique le nierait. Le partage n'est
# pas esthétique : `bad-signature` dit que quelqu'un a servi des octets en se réclamant d'une clé qu'on
# accepte — le seul indicateur de compromission qu'un système hors-ligne aura jamais (§12 de la spec de
# racine de confiance : *un vérificateur PRÉSENT qui échoue est un ÉCHEC, bruyant côté log*). Un réseau
# injoignable, lui, est une non-nouvelle : un train qui ne passe pas n'est pas une alarme.
_NIVEAU = {
    "bad-signature": logging.ERROR,
    "unknown-key": logging.WARNING,
    "malformed": logging.WARNING,      # le manifeste servi n'a pas la forme publiée : côté PUBLICATION, pas
    #                                    côté réseau — quelqu'un doit le réparer, ce n'est pas de l'attente.
    "internal": logging.WARNING,       # défaut de CE module ; discret, il ne serait jamais vu.
}


def refresh(settings: Settings, *, url: str | None = None, fetcher=None,
            keys_path: Path | None = None, now: datetime | None = None) -> dict:
    """Un tour de canal : lire, vérifier, écrire. **Ne lève jamais** — un canal muet ne fait pas tomber un
    daemon, et une exception qui remonte d'ici tuerait la boucle qui l'a appelée.

    **Aucun réseau si la racine de confiance est vide**, et l'ordre compte : on regarde d'abord ce qu'on
    saurait vérifier, ensuite seulement on va chercher. Une édition qui ne peut rien authentifier n'a
    aucune raison d'émettre une requête — elle devrait de toute façon jeter la réponse.

    Le dernier succès **survit** à un échec : un réseau injoignable ne fait pas oublier ce qu'on savait, il
    le fait **vieillir**. Écraser `last_success` sur une panne transformerait une indisponibilité passagère
    en amnésie."""
    horodatage = (now or datetime.now(UTC)).isoformat()
    succes = read_state(settings)["last_success"]
    etat, raison, annonce = "ok", "", None
    keys: list[dict] = []
    try:
        keys = trust_root(keys_path)
    except ChannelMalformed as exc:
        etat, raison = "malformed", str(exc)
    # L'absence de racine de confiance est un état À PART ENTIÈRE, pas une variété d'échec de vérification :
    # on le décide ici, par un test, plutôt qu'en reniflant le message d'une exception — un état déduit
    # d'une chaîne de caractères se casse au premier reformulage.
    if etat == "ok" and not keys:
        etat, raison = "no-trust-root", ("aucune racine de confiance embarquée — rien n'a été demandé au "
                                         "réseau : cette édition jetterait la réponse de toute façon")
    if etat == "ok":
        # Le tirage est résolu À L'APPEL, pas figé en défaut d'argument : un défaut est lié au moment de la
        # définition, donc insurchargeable — et une frontière réseau qu'on ne peut pas remplacer est une
        # frontière qu'on ne peut pas prouver.
        tirer = fetcher or fetch
        try:
            annonce = parse_announce(verify_envelope(parse_envelope(tirer(url or channel_url())), keys))
        except ChannelUnknownKey as exc:
            etat, raison = "unknown-key", str(exc)
        except ChannelBadSignature as exc:
            etat, raison = "bad-signature", str(exc)
        except ChannelUnreachable as exc:
            etat, raison = "unreachable", str(exc)
        except ChannelMalformed as exc:
            etat, raison = "malformed", str(exc)
        except Exception as exc:                        # noqa: BLE001 — filet, pas tapis : voir l'état dédié
            # Rien ne devrait arriver ici : les quatre refus au-dessus couvrent ce que ce module sait
            # produire. Ce filet existe parce que `refresh` PROMET de ne pas lever, et une promesse tenue
            # « sauf imprévu » n'en est pas une. L'état est DISTINCT (`internal`) : avaler un défaut de code
            # sous « réseau injoignable » ferait chercher une panne d'infrastructure pendant des heures.
            etat, raison = "internal", f"défaut interne du canal ({type(exc).__name__}) : {exc}"
    if etat == "ok":
        succes = {"at": horodatage, "announce": annonce}
    else:
        logging.getLogger("forgemaster").log(_NIVEAU.get(etat, logging.INFO),
                                             "canal de MAJ — %s : %s", etat, raison)
    out = {"last_success": succes, "last_attempt": {"at": horodatage, "state": etat, "reason": raison}}
    # Le `schema` est posé à l'ÉCRITURE seulement : ce qu'on rend à l'appelant est la même vue que
    # `read_state`, sinon deux lecteurs du même état verraient deux formes.
    _write_state(settings, {"schema": STATE_SCHEMA, **out})
    return out


async def run_channel_poll(settings: Settings, *, interval_s: float = POLL_INTERVAL_S,
                           refresher=refresh) -> None:
    """Tâche de fond : le canal au démarrage **puis** à intervalle. Idiome sleep-loop de `run_reaper`
    (`terminal/registry.py`), annulée au shutdown par le `_lifespan`.

    **Une seule différence avec le reaper, et elle est structurelle** : le tirage est de l'I/O
    **bloquante** (`urllib`), donc il part dans un thread. Un sleep-loop qui appellerait `urllib` en direct
    gèlerait la boucle d'événements — donc TOUTES les requêtes HTTP et TOUS les WebSocket du daemon —
    jusqu'au timeout. « Sans jamais bloquer le daemon » se tient ici, ou nulle part.

    Le premier tour a lieu **avant** le premier sommeil : un daemon qu'on vient de redémarrer est
    précisément le moment où quelqu'un veut savoir. Le reaper, lui, dort d'abord — il n'a rien à réaper au
    boot ; nous, si."""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(refresher, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                     # noqa: BLE001 — la boucle survit à tout sauf l'annulation
            # `refresh` ne lève pas ; si quelque chose remonte quand même, c'est un défaut de CE module et
            # la boucle doit le DIRE sans mourir — un canal muet est préférable à un daemon qui perd sa
            # tâche de fond en silence.
            logging.getLogger("forgemaster").warning("canal de MAJ — tour échoué (défaut interne) : %s", exc)
        await asyncio.sleep(interval_s)


# --- verdict -----------------------------------------------------------------------------------------
#
# L'état d'un tour dit ce qui S'EST PASSÉ ; le verdict dit ce qu'on en FAIT. Ce sont deux questions, et les
# fondre en une seule condamnerait l'une des deux à mentir : « injoignable » n'est pas une réponse à « dois-je
# mettre à jour ? », et « une édition existe » n'est pas une réponse à « mon dernier contrôle a-t-il
# abouti ? ».
# Les deux voyagent donc ensemble dans le volet, jamais l'une à la place de l'autre.

# Un échec DUR (la vérification) prend la tête ; un échec MOU (le réseau) fait seulement VIEILLIR ce qu'on
# savait. La distinction est la doctrine même du canal, appliquée au verdict : un réseau qui tombe fait
# vieillir, une signature qui ment interrompt. Sans elle, une panne de wifi effacerait une annonce déjà
# vérifiée (amnésie) — ou, symétriquement, une signature falsifiée passerait sous une vieille bonne nouvelle.
_DUR = ("bad-signature", "unknown-key")
_MOU = ("unreachable", "malformed", "internal")


def situate(announce: dict, build_sha: str | None) -> tuple[str, str]:
    """L'instance, située par rapport à l'édition annoncée — **PURE**. Rend `(état, raison)`.

    Trois issues, et la troisième est celle qui empêche les deux autres de mentir :

    - `up-to-date` — le SHA annoncé est le nôtre ;
    - `available` — notre SHA est **dans la lignée**, donc l'édition annoncée **descend** de ce qu'on
      exécute. C'est la seule configuration où proposer une mise à jour est fondé ;
    - `cannot-situate` — notre SHA est **absent** de la lignée. **Trois** causes distinctes produisent
      exactement cette absence : une instance plus ancienne que la fenêtre publiée, un wheel bâti maison
      depuis un commit jamais publié, ou une vraie divergence. On ne les distingue pas, **donc on ne choisit
      pas** : c'est un aveu (« je ne peux pas te situer »), jamais un verdict de divergence. C'est aussi
      pourquoi il ne rougit pas — accuser sur une absence serait le faux verdict que la lignée bornée
      existe précisément pour éviter.

    Le cas sans tampon de build tombe dans le même aveu, et pour la même raison de fond : on ne sait pas
    d'où l'on vient, donc on ne peut pas dire où l'on est."""
    ed = announce.get("edition") or {}
    annonce_sha = ed.get("sha")
    if not build_sha:
        return "cannot-situate", (
            "cette instance n'a pas de tampon de build (checkout éditable, ou wheel bâti sans tampon) — "
            "sans SHA d'origine, rien ne permet de la situer dans la lignée publiée")
    if build_sha == annonce_sha:
        return "up-to-date", ""
    lignee = announce.get("lineage") or []
    if build_sha in lignee:
        return "available", ""
    return "cannot-situate", (
        f"le SHA de cette instance ({build_sha[:12]}) n'apparaît pas dans la lignée publiée "
        f"({len(lignee)} édition(s)) — trois causes donnent cette même absence : instance plus ancienne "
        f"que la fenêtre publiée, wheel bâti maison depuis un commit jamais publié, ou divergence réelle. "
        f"On ne les distingue pas, donc rien n'est proposé et rien n'est reproché")


def verdict(state: dict, *, build_sha: str | None) -> dict:
    """Ce que le produit FAIT de ce que le canal a lu — **PURE**, testable à la table.

    Sept issues, et **aucune ne verdit par défaut** : `never` · `no-trust-root` · `unverified` ·
    `unreachable` · `up-to-date` · `available` · `cannot-situate`.

    Ordre de priorité, et son motif :

    1. **`no-trust-root` d'abord** — une capacité absente n'est pas un échec de vérification, et la
       présenter comme tel enverrait chercher une panne là où il n'y a qu'une édition sans clé.
    2. **Un échec DUR ensuite** (`unverified`) : il l'emporte même sur une annonce déjà vérifiée. Quelqu'un
       sert des octets qui se réclament d'une clé qu'on accepte — c'est plus urgent qu'une bonne nouvelle
       d'hier, et l'annonce d'hier reste rendue à côté, avec son âge.
    3. **Sans rien de vérifié**, on dit lequel des deux silences : `never` (aucun tour) ou `unreachable`.
    4. **Sinon on SITUE** — y compris quand le dernier tour a échoué mollement. C'est la contrepartie exacte
       de la survie de `last_success` : un réseau injoignable fait vieillir ce qu'on savait, il ne le rend
       pas faux. Le tour raté n'est pas caché pour autant — il voyage dans `attempt`.

    Le volet rendu porte **toujours** le dernier tour (`attempt`) à côté du verdict : une surface qui ne
    montrerait que le verdict ne pourrait pas dire de QUAND il date, ni que le contrôle d'aujourd'hui a
    échoué."""
    tentative = state.get("last_attempt") or dict(_JAMAIS)
    succes = state.get("last_success")
    brut = tentative.get("state") or "never"
    vue = {"attempt": {"state": brut, "at": tentative.get("at"),
                       "reason": tentative.get("reason") or ""},
           "verified_at": (succes or {}).get("at"),
           "announced": _announced(succes)}
    # `from_attempt` dit d'OÙ vient le verdict : du dernier tour, ou d'un succès antérieur. C'est un fait
    # du domaine et non un indice d'affichage — et c'est ce qui évite que la règle « ne pas redire le tour
    # quand il EST déjà le sujet » soit recopiée dans chaque surface, où les deux copies finiraient par ne
    # plus lister les mêmes états.
    depuis_le_tour = {**vue, "from_attempt": True}
    if brut == "no-trust-root":
        return {**depuis_le_tour, "state": "no-trust-root", "reason": tentative.get("reason") or ""}
    if brut in _DUR:
        return {**depuis_le_tour, "state": "unverified", "reason": tentative.get("reason") or ""}
    if not succes or not isinstance(succes.get("announce"), dict):
        if brut in _MOU:
            return {**depuis_le_tour, "state": "unreachable", "reason": tentative.get("reason") or ""}
        return {**depuis_le_tour, "state": "never",
                "reason": "aucune annonce n'a encore été vérifiée sur cette instance"}
    etat, raison = situate(succes["announce"], build_sha)
    return {**vue, "from_attempt": False, "state": etat, "reason": raison}


def read_verdict(settings: Settings, *, build_sha: str | None) -> dict:
    """Le volet `channel` de « quelle édition tourne ici ? » : l'état sur DISQUE, transformé en verdict.

    **Aucun réseau** — c'est une lecture de cache, et c'est ce qui la rend posable sur une sonde qui ne doit
    ni pendre ni rendre 500. Le `build_sha` est **passé**, jamais relu ici : il appartient à
    `build_provenance`, et l'importer ferait de ce module un voisin du sien alors que leur seule relation
    est d'être composés par un appelant. La composition vit donc chez l'appelant (route ou CLI), là où elle
    ne crée aucune arête dans le graphe d'imports."""
    return verdict(read_state(settings), build_sha=build_sha)


def _announced(succes: dict | None) -> dict | None:
    """Ce qu'une surface a besoin de savoir de l'annonce vérifiée, et **rien de plus**. On ne renvoie pas le
    document entier : une surface qui reçoit tout finit par afficher ce qu'elle n'a pas su nommer, et le
    contrat d'API se met alors à dépendre de la forme du manifeste d'un tiers."""
    if not succes or not isinstance(succes.get("announce"), dict):
        return None
    ed = succes["announce"].get("edition") or {}
    wheel = ed.get("wheel") or {}
    return {"version": ed.get("version"), "sha": ed.get("sha"),
            "committed_at": ed.get("committed_at"),
            "wheel_name": wheel.get("name"), "wheel_sha256": wheel.get("sha256"),
            "lineage_len": len(succes["announce"].get("lineage") or [])}


# --- CLI ---------------------------------------------------------------------------------------------


_TITRE = {
    "never":          "○ aucune annonce vérifiée sur cette instance",
    "no-trust-root":  "○ capacité absente — cette édition n'embarque aucune racine de confiance",
    "unverified":     "✗ ANNONCE NON VÉRIFIÉE — le manifeste servi n'est pas authentifiable",
    "unreachable":    "○ canal injoignable — rien n'a pu être lu",
    "up-to-date":     "✓ à jour avec l'édition annoncée",
    "available":      "▲ une édition plus récente est annoncée",
    "cannot-situate": "○ édition annoncée NON PROPOSÉE — cette instance n'est pas situable",
}


def cli_check(settings: Settings, *, build_sha: str | None) -> int:
    """`forgemaster update check` — tirer maintenant, et dire **ce qu'il faut en faire**.

    **Toujours rc 0**, comme `update wheels` et `update aptitude` : c'est une QUESTION, pas un geste. Un
    réseau injoignable n'est pas un échec de la commande — c'est une réponse, et elle s'affiche.

    Ce verbe existe parce qu'une instance pilotée **uniquement en CLI** n'a aucun daemon pour aller voir :
    sans lui, la moitié réseau de « savoir » ne la couvrirait pas. Il rend le **même** verdict que la
    surface (`verdict`, un seul foyer) : deux implémentations de « à jour » finiraient par ne plus vouloir
    dire la même chose.

    `build_sha` est **exigé**, sans valeur par défaut : un défaut ferait rendre « non situable » à une
    instance parfaitement tamponnée dont l'appelant aurait oublié l'argument — un verdict faux produit par
    un oubli silencieux, exactement ce qu'un `None` par défaut rend indétectable."""
    vue = verdict(refresh(settings), build_sha=build_sha)
    print(_TITRE[vue["state"]])
    if vue["reason"]:
        print(f"  {vue['reason']}")
    ann = vue["announced"]
    if ann:
        # L'annonce vérifiée est rendue MÊME quand le verdict ne s'appuie pas dessus (`unverified`) : elle a
        # été authentifiée un jour, et la cacher reviendrait à effacer ce qu'on savait au lieu de le vieillir.
        print(f"\n  Annonce vérifiée le {vue['verified_at']} — {ann['version']} @ "
              f"{(ann['sha'] or '?')[:12]}")
        print(f"  wheel {ann['wheel_name'] or '?'} · sha256 {(ann['wheel_sha256'] or '?')[:16]}…")
        print(f"  lignée : {ann['lineage_len']} édition(s) publiée(s) avant elle")
    tent = vue["attempt"]
    # Le dernier tour n'est redit QUE s'il n'est pas déjà le sujet — `from_attempt` le dit, plutôt qu'une
    # liste d'états recopiée ici et une seconde fois dans le rendu web. Sur `unverified`/`unreachable`/
    # `no-trust-root`, le verdict EST ce tour et sa raison a déjà été imprimée : la répéter en dessous
    # ferait passer une seule cause pour deux faits distincts.
    if tent["state"] != "ok" and not vue["from_attempt"]:
        print(f"\n  Dernier contrôle ({tent['at'] or 'jamais'}) : {tent['state']} — {tent['reason']}")
    if vue["state"] == "available":
        print("\nLe canal ANNONCE — il ne télécharge rien et ne pose rien. "
              "`forgemaster update apply --wheel <fichier>` reste le seul geste, et il est à vous.")
    return 0


# --- interne -----------------------------------------------------------------------------------------


_B64URL = bytes.maketrans(b"-_", b"+/")


def _b64d(s: object) -> bytes:
    """base64url **strict** (`validate=True`, padding exigé). L'indulgence par défaut de
    `urlsafe_b64decode` — qui **jette** silencieusement les caractères hors alphabet — donnerait deux textes
    distincts décodant vers les mêmes octets : deux manifestes différents vérifieraient alors sous la même
    signature, et c'est exactement le genre de latitude qu'une signature est censée retirer."""
    if not isinstance(s, str):
        raise ChannelMalformed(f"champ base64 attendu, {type(s).__name__} lu")
    try:
        return base64.b64decode(s.encode("ascii").translate(_B64URL), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise ChannelMalformed(f"base64url invalide : {exc}") from None


def _ed25519_verify(public: bytes, signature: bytes, message: bytes) -> bool:
    """Vérifie, et rend un booléen plutôt que de laisser filer l'exception de la bibliothèque : le seul
    appelant a **deux** issues à distinguer par lui-même, et une `InvalidSignature` qui traverse trois
    couches finit par être attrapée par un `except` trop large.

    Ed25519 est retenu pour ce qu'il **n'a pas** : ni taille de clé, ni padding, ni choix de hash — aucun
    bouton sur lequel se tromper. Import PARESSEUX : `cryptography` est une dép runtime (le Fernet du store
    fichier), mais le socle ne la tire qu'à l'usage."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True


def _write_state(settings: Settings, data: dict) -> None:
    """Écriture ATOMIQUE (temporaire + `os.replace`), même forme que `update._write_json` : un lecteur
    concurrent voit l'ancien fichier ou le nouveau, jamais un demi-fichier. **Ne lève pas** — un disque
    plein ne doit pas tuer la boucle de fond ; on le dit et on continue."""
    p = state_path(settings)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        logging.getLogger("forgemaster").warning("canal de MAJ — état non écrit (%s) : %s", p, exc)
