"""Tests d'`update_channel` — apprendre qu'une version existe, et ne le croire que si ça vérifie.

Ce que ces tests gardent, dans l'ordre d'importance :

1. **rien n'est cru sans signature** — et surtout, rien n'est *lu* sans signature : le payload reste des
   octets opaques tant que la vérification n'a pas tranché (invariant *parse-after-verify*, falsifié ici
   par un payload volontairement illégal en JSON — si le refus venait du parseur, le test le verrait) ;
2. **les refus sont DISTINCTS** — `key_id` inconnu n'est pas une signature invalide, et les fondre ferait
   perdre le seul indicateur de compromission (ou de rotation non suivie) qu'un système hors-ligne aura ;
3. **ce qu'on savait survit à ce qu'on n'a pas pu lire** — un réseau injoignable fait VIEILLIR le dernier
   succès, il ne l'efface pas ;
4. **la boucle ne bloque pas le daemon** — l'I/O est bloquante, la boucle d'événements ne doit pas l'être.

La clé de test est **éphémère et générée ici** : aucun secret, aucun BWS, aucun fichier de clé dans le
dépôt. La vraie paire naît à la cérémonie de génération, hors de tout test.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from forgemaster import update_channel as uc
from forgemaster.config import Settings

# --- décors -------------------------------------------------------------------------------------------


@pytest.fixture
def live(tmp_path: Path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def _paire() -> tuple[Ed25519PrivateKey, bytes, str]:
    """Une paire éphémère + le `key_id` DÉRIVÉ de sa publique — jamais attribué."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()
    return priv, pub, uc.key_id(pub)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii")


def _annonce(**over) -> bytes:
    data = {"schema": uc.ANNOUNCE_SCHEMA,
            "published_at": "2026-08-08T10:00:00+00:00",
            "edition": {"version": "0.2.0", "sha": "a" * 40, "committed_at": "2026-08-08T09:00:00+00:00",
                        "wheel": {"name": "forgemaster-0.2.0-py3-none-any.whl", "sha256": "b" * 64,
                                  "size": 1234},
                        "maps": [{"name": "code-map", "sha": "c" * 40}]},
            "lineage": ["d" * 40, "e" * 40]}
    data.update(over)
    return json.dumps(data).encode("utf-8")


def _enveloppe(payload: bytes, *signataires: tuple[Ed25519PrivateKey, str]) -> bytes:
    """L'enveloppe telle qu'un publieur l'émettrait — signature sur `<domaine>\\n<payload>`, jamais sur un
    objet re-sérialisé, et `signatures` en liste même à un élément."""
    sigs = [{"key_id": kid, "sig": _b64(priv.sign(uc._DOMAIN + payload))} for priv, kid in signataires]
    return json.dumps({"schema": uc.ENVELOPE_SCHEMA, "payload": _b64(payload),
                       "signatures": sigs}).encode("utf-8")


def _racine(path: Path, *cles: tuple[bytes, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": [{"key_id": kid, "public": _b64(pub)} for pub, kid in cles]}),
                    encoding="utf-8")
    return path


class _Compteur:
    """Un `fetch` injecté qui COMPTE ses appels : c'est la seule façon de prouver qu'une requête n'a pas eu
    lieu — un test qui vérifie seulement l'état final ne distingue pas « n'a rien demandé » de « a demandé
    et jeté la réponse »."""

    def __init__(self, corps: bytes | Exception):
        self.corps, self.appels = corps, 0

    def __call__(self, url: str, **_kw) -> bytes:
        self.appels += 1
        if isinstance(self.corps, Exception):
            raise self.corps
        return self.corps


# --- 1. le format : ce qui est refusé, et ce que le refus DIT ------------------------------------------


def test_un_schema_denveloppe_inconnu_est_refuse_en_nommant_les_deux():
    """Patron de `restore.load_manifest`, repris et non réinventé : un refus qui ne dit pas ce qu'il
    attendait n'est pas actionnable — celui qui publie doit pouvoir lire son erreur."""
    raw = json.dumps({"schema": "forgemaster-update-channel/9", "payload": "", "signatures": []}).encode()
    with pytest.raises(uc.ChannelMalformed) as exc:
        uc.parse_envelope(raw)
    assert "forgemaster-update-channel/9" in str(exc.value)
    assert uc.ENVELOPE_SCHEMA in str(exc.value)
    assert "refuse de deviner" in str(exc.value)


def test_signatures_scalaire_est_refuse_car_la_LISTE_est_le_format():
    """Le pluriel est verrouillé dès le premier jour parce qu'il ne se rétrofite pas : accepter un scalaire
    par indulgence rendrait impossible le chevauchement de rotation sans casser le format chez les instances
    qui l'ont déjà lu."""
    for sigs in ({"key_id": "x", "sig": ""}, [], None):
        raw = json.dumps({"schema": uc.ENVELOPE_SCHEMA, "payload": "", "signatures": sigs}).encode()
        with pytest.raises(uc.ChannelMalformed, match="LISTE"):
            uc.parse_envelope(raw)


def test_un_schema_dannonce_inconnu_est_refuse_separement_de_lenveloppe():
    """Deux schémas versionnés SÉPARÉMENT : une enveloppe valide peut porter une annonce qu'on ne sait pas
    lire, et ce cas doit se dire — sinon on ferait bousculer la version de l'enveloppe pour faire évoluer
    ce qu'elle transporte."""
    with pytest.raises(uc.ChannelMalformed) as exc:
        uc.parse_announce(_annonce(schema="forgemaster-edition-announce/9"))
    assert "forgemaster-edition-announce/9" in str(exc.value) and uc.ANNOUNCE_SCHEMA in str(exc.value)


def test_une_annonce_sans_sha256_de_wheel_est_refusee():
    """Le canal ANNONCE : le SHA-256 est la SEULE chose qui permettra de confronter le fichier que
    l'utilisateur ira chercher à ce qui a été signé. Sans lui, l'annonce inviterait à faire confiance à un
    téléchargement — exactement ce que la signature existe pour éviter."""
    sans = json.loads(_annonce())
    del sans["edition"]["wheel"]["sha256"]
    with pytest.raises(uc.ChannelMalformed, match="sha256"):
        uc.parse_announce(json.dumps(sans).encode())


def test_une_lignee_au_dela_du_plafond_est_refusee_jamais_tronquee():
    """Tronquer en silence changerait « je ne peux pas te situer » en « tu n'es pas dans la lignée » : un
    AVEU deviendrait un VERDICT, et l'utilisateur lirait une divergence là où il n'y a qu'une fenêtre."""
    trop = _annonce(lineage=["f" * 40] * (uc.LINEAGE_MAX + 1))
    with pytest.raises(uc.ChannelMalformed) as exc:
        uc.parse_announce(trop)
    assert str(uc.LINEAGE_MAX) in str(exc.value) and "tronquée" in str(exc.value)


def test_un_base64_indulgent_accepterait_deux_textes_pour_les_memes_octets():
    """`urlsafe_b64decode` JETTE les caractères hors alphabet : deux manifestes textuellement différents
    décoderaient alors vers les mêmes octets et vérifieraient sous la MÊME signature. Le décodage strict
    retire cette latitude — c'est précisément ce qu'une signature est censée faire."""
    propre = _b64(b"forgemaster")
    sale = propre[:3] + "\n \t" + propre[3:]
    assert base64.urlsafe_b64decode(sale.encode()) == b"forgemaster", "prémisse du test : l'indulgence"
    with pytest.raises(uc.ChannelMalformed, match="base64url invalide"):
        uc._b64d(sale)


# --- 2. la vérification : deux conditions, et des refus qu'on ne confond pas ---------------------------


def test_une_signature_valide_rend_les_octets_du_payload():
    priv, pub, kid = _paire()
    payload = _annonce()
    env = uc.parse_envelope(_enveloppe(payload, (priv, kid)))
    assert uc.verify_envelope(env, [{"key_id": kid, "public": pub}]) == payload


def test_un_octet_modifie_dans_le_payload_fait_echouer_la_verification():
    """On vérifie des OCTETS, jamais un objet re-sérialisé : aucune canonicalisation JSON n'entre dans la
    vérification, donc aucune modification ne peut passer pour équivalente."""
    priv, pub, kid = _paire()
    payload = _annonce()
    env = uc.parse_envelope(_enveloppe(payload, (priv, kid)))
    env["payload"] = payload.replace(b"0.2.0", b"9.9.9")
    with pytest.raises(uc.ChannelBadSignature):
        uc.verify_envelope(env, [{"key_id": kid, "public": pub}])


def test_un_key_id_inconnu_est_un_etat_DISTINCT_dune_signature_invalide():
    """Les fondre ferait perdre le seul indicateur de compromission — ou de rotation non suivie — qu'un
    système hors-ligne aura jamais. Les deux se réparent, mais pas de la même façon."""
    priv, _pub, kid = _paire()
    _autre_priv, autre_pub, autre_kid = _paire()
    env = uc.parse_envelope(_enveloppe(_annonce(), (priv, kid)))
    with pytest.raises(uc.ChannelUnknownKey):
        uc.verify_envelope(env, [{"key_id": autre_kid, "public": autre_pub}])
    assert not issubclass(uc.ChannelUnknownKey, uc.ChannelBadSignature)
    assert not issubclass(uc.ChannelBadSignature, uc.ChannelUnknownKey)


def test_une_signature_qui_designe_A_mais_verifie_sous_B_est_REFUSEE():
    """Jamais un « essaie toutes les clés » : une signature valide sous une AUTRE clé du jeu que celle
    qu'elle désigne est une INCOHÉRENCE. L'accepter la transformerait en succès silencieux."""
    priv_a, pub_a, kid_a = _paire()
    _priv_b, pub_b, kid_b = _paire()
    # signée par A, mais l'enveloppe prétend que c'est la clé B
    env = uc.parse_envelope(_enveloppe(_annonce(), (priv_a, kid_b)))
    jeu = [{"key_id": kid_a, "public": pub_a}, {"key_id": kid_b, "public": pub_b}]
    with pytest.raises(uc.ChannelBadSignature):
        uc.verify_envelope(env, jeu)


def test_le_chevauchement_de_rotation_verifie_avec_UNE_seule_clef_connue():
    """Pendant la fenêtre de rotation le manifeste porte DEUX signatures, et une instance n'en connaît
    qu'une — par construction, puisque c'est la raison d'être de la fenêtre. Une seule valide suffit."""
    ancienne, pub_ancienne, kid_ancienne = _paire()
    nouvelle, _pub_nouvelle, kid_nouvelle = _paire()
    payload = _annonce()
    env = uc.parse_envelope(_enveloppe(payload, (ancienne, kid_ancienne), (nouvelle, kid_nouvelle)))
    assert uc.verify_envelope(env, [{"key_id": kid_ancienne, "public": pub_ancienne}]) == payload


def test_le_payload_nEST_PAS_parse_avant_davoir_verifie():
    """**Falsification de `parse-after-verify`.** Le payload est du JSON volontairement ILLÉGAL et la
    signature est fausse : si le parseur tournait avant le vérificateur, le refus serait un
    `ChannelMalformed` de JSON. C'est un `ChannelBadSignature` qui doit sortir — et c'est ce qui empêche le
    parseur d'être la première surface d'attaque, avant tout contrôle."""
    priv, pub, kid = _paire()
    poison = b"{ceci n'est pas du JSON"
    env = uc.parse_envelope(_enveloppe(poison, (priv, kid)))
    env["payload"] = poison + b"!"                      # les octets bougent → la signature ne vérifie plus
    with pytest.raises(uc.ChannelBadSignature):
        uc.verify_envelope(env, [{"key_id": kid, "public": pub}])


# --- 3. la racine de confiance : absente, ou présente et cohérente ------------------------------------


def test_une_racine_absente_rend_une_liste_vide_une_racine_qui_MENT_leve(tmp_path: Path):
    """*Absence n'est pas panne ; un juge PRÉSENT qui plante est un ÉCHEC* — doctrine d'`apply_update`,
    reprise verbatim. Et un `key_id` dérivé qu'on ne re-dérive pas n'est qu'un nom : ici il ment, et on le
    voit."""
    assert uc.trust_root(tmp_path / "jamais.json") == []
    _, pub, _kid = _paire()
    menteur = tmp_path / "menteur.json"
    menteur.write_text(json.dumps({"keys": [{"key_id": "0" * 16, "public": _b64(pub)}]}), encoding="utf-8")
    with pytest.raises(uc.ChannelMalformed) as exc:
        uc.trust_root(menteur)
    assert "ment" in str(exc.value)


def test_une_racine_presente_mais_vide_nest_pas_une_racine_absente(tmp_path: Path):
    """Une racine VIDE est une erreur de publication ; une racine ABSENTE est un état normal de cette
    édition. Les confondre ferait dégrader silencieusement un fichier cassé en « aucune clé »."""
    vide = tmp_path / "vide.json"
    vide.write_text(json.dumps({"keys": []}), encoding="utf-8")
    with pytest.raises(uc.ChannelMalformed, match="VIDE"):
        uc.trust_root(vide)


# --- 4. le tirage : plafond, injoignable, et ce qui n'a PAS été demandé --------------------------------


def test_sans_racine_de_confiance_AUCUNE_requete_nest_emise(live: Settings, tmp_path: Path):
    """Une édition qui ne peut rien vérifier jetterait la réponse de toute façon : aller la chercher serait
    une exposition pour rien. Le compteur à zéro est la preuve — l'état final seul ne distinguerait pas
    « n'a rien demandé » de « a demandé puis jeté »."""
    compteur = _Compteur(b"peu importe")
    vue = uc.refresh(live, fetcher=compteur, keys_path=tmp_path / "jamais.json")
    assert compteur.appels == 0
    assert vue["last_attempt"]["state"] == "no-trust-root"
    assert vue["last_success"] is None


def test_un_corps_au_dela_du_plafond_est_refuse_avant_materialisation(monkeypatch):
    """Le plafond est appliqué EN FLUX : `read(max_bytes + 1)` ne ramène jamais plus que le plafond plus
    l'octet qui sert à savoir qu'on l'a dépassé. Lire d'abord et mesurer ensuite serait un plafond qui ne
    protège de rien — défaut déjà payé une fois par le canal de contenu."""
    demande: list[int] = []

    class _Reponse:
        def read(self, n: int) -> bytes:
            demande.append(n)
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(uc.urllib.request, "urlopen", lambda *_a, **_k: _Reponse())
    with pytest.raises(uc.ChannelMalformed, match="plafond"):
        uc.fetch("https://exemple.invalide/channel.json", max_bytes=1024)
    assert demande == [1025], "le plafond n'a pas été appliqué à la lecture elle-même"


def test_une_url_non_http_est_refusee():
    """L'URL est surchargeable par l'environnement, et c'est inoffensif tant que le schéma est contrôlé :
    sans ce refus, une surcharge transformerait un GET en lecture de fichier local."""
    with pytest.raises(uc.ChannelMalformed, match="schéma d'URL"):
        uc.fetch("file:///etc/passwd")


def test_un_reseau_injoignable_fait_VIEILLIR_le_dernier_succes_il_ne_lefface_pas(live: Settings,
                                                                                 tmp_path: Path):
    """Écraser `last_success` sur une panne transformerait une indisponibilité passagère en AMNÉSIE. Et
    l'inverse serait pire : rendre le dernier succès sans dire que la dernière tentative a échoué serait un
    faux-vert."""
    priv, pub, kid = _paire()
    racine = _racine(tmp_path / "keys.json", (pub, kid))

    ok = uc.refresh(live, url="https://exemple.invalide/c.json", keys_path=racine,
                    fetcher=_Compteur(_enveloppe(_annonce(), (priv, kid))))
    assert ok["last_attempt"]["state"] == "ok"
    assert ok["last_success"]["announce"]["edition"]["version"] == "0.2.0"

    ko = uc.refresh(live, url="https://exemple.invalide/c.json", keys_path=racine,
                    fetcher=_Compteur(uc.ChannelUnreachable("réseau coupé")))
    assert ko["last_attempt"]["state"] == "unreachable"
    assert ko["last_success"] == ok["last_success"], "ce qu'on savait a été effacé au lieu de vieillir"
    assert uc.read_state(live)["last_success"] == ok["last_success"], "l'état sur disque a divergé"


def test_chaque_facon_dechouer_rend_son_PROPRE_etat(live: Settings, tmp_path: Path):
    """Quatre pannes, quatre états : elles n'appellent pas les mêmes réparations. Un `error` unique
    obligerait à lire un message libre pour savoir quoi faire."""
    priv, pub, kid = _paire()
    _autre, autre_pub, autre_kid = _paire()
    racine = _racine(tmp_path / "keys.json", (pub, kid))
    url = "https://exemple.invalide/c.json"

    # Signé par une clé légitime, mais l'enveloppe désigne un `key_id` que la racine ne connaît pas.
    inconnue = _enveloppe(_annonce(), (priv, "0" * 16))
    # Signé par `priv`, mais l'enveloppe désigne `autre_kid` — qui EST dans la racine élargie : la
    # signature vérifiera donc sous une clé, mais pas sous celle qu'elle nomme.
    menteuse = _enveloppe(_annonce(), (priv, autre_kid))
    racine_2 = _racine(tmp_path / "keys2.json", (pub, kid), (autre_pub, autre_kid))

    cas = [("unreachable", racine, _Compteur(uc.ChannelUnreachable("coupé"))),
           ("malformed", racine, _Compteur(b'{"schema": "autre-chose"}')),
           ("unknown-key", racine, _Compteur(inconnue)),
           ("bad-signature", racine_2, _Compteur(menteuse)),
           ("internal", racine, lambda *_a, **_k: (_ for _ in ()).throw(ValueError("imprévu")))]
    for attendu, chemin, fetcher in cas:
        vue = uc.refresh(live, url=url, keys_path=chemin, fetcher=fetcher)
        assert vue["last_attempt"]["state"] == attendu, f"{attendu} : {vue['last_attempt']}"
        assert vue["last_attempt"]["reason"], "un état sans raison n'est pas actionnable"


def test_refresh_ne_leve_JAMAIS_meme_sur_un_defaut_quil_ne_prevoit_pas(live: Settings, tmp_path: Path):
    """Une exception qui remonterait d'ici tuerait la boucle de fond qui l'a appelée, et un daemon perdrait
    sa tâche en silence. Le filet rend un état DÉDIÉ (`internal`) plutôt que de se déguiser en panne
    réseau : avaler un défaut de code sous « injoignable » ferait chercher une panne d'infrastructure."""
    _priv, pub, kid = _paire()
    racine = _racine(tmp_path / "keys.json", (pub, kid))

    def _explose(_url, **_kw):
        raise ValueError("panne inattendue du client HTTP")

    with pytest.raises(ValueError):
        _explose("x")                                    # prémisse : ce tirage-là lève bien
    vue = uc.refresh(live, url="https://exemple.invalide/c.json", keys_path=racine, fetcher=_explose)
    assert vue["last_attempt"]["state"] == "internal"
    assert "ValueError" in vue["last_attempt"]["reason"], "le type du défaut est perdu en route"


# --- 5. l'état sur disque, et la boucle ---------------------------------------------------------------


def test_un_etat_jamais_lu_se_DIT_au_lieu_de_ressembler_a_rien(live: Settings):
    """« Je n'ai jamais regardé » n'est pas « rien n'existe ». Les confondre serait un faux-vert : une
    surface afficherait « à jour » sur une instance qui n'a jamais interrogé le canal."""
    vue = uc.read_state(live)
    assert vue["last_success"] is None
    assert vue["last_attempt"]["state"] == "never" and vue["last_attempt"]["reason"]


def test_un_etat_tronque_se_lit_comme_absent_sans_lever(live: Settings):
    """Une écriture concurrente interrompue ne doit pas faire tomber la lecture. L'écriture, elle, est
    atomique (`os.replace`) — ce test garde l'autre moitié : le lecteur."""
    p = uc.state_path(live)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"last_success": {"at"', encoding="utf-8")
    assert uc.read_state(live)["last_attempt"]["state"] == "never"


def test_un_cache_de_SCHEMA_INCONNU_se_lit_comme_absent(live: Settings, capsys):
    """**Le cas qu'on oublie.** Ce fichier est écrit par le binaire d'AVANT une mise à jour et relu par
    celui d'APRÈS : contrairement à `_maps/maps.json`, il ne voyage PAS avec son lecteur. Le lire au jugé
    ferait planter une CLI sur une clé absente ; le REFUSER inventerait une panne. On le jette — l'état se
    reconstruit au tour suivant — et la CLI reste debout."""
    p = uc.state_path(live)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": "forgemaster-update-channel-state/9",
                             "last_success": {"forme": "inconnue"}}), encoding="utf-8")
    vue = uc.read_state(live)
    assert vue["last_success"] is None and vue["last_attempt"]["state"] == "never"
    assert uc.cli_check(live, build_sha="9" * 40) == 0, (
        "une CLI est tombée sur un cache écrit par une autre version")
    # Ce que la CLI dit ici dépend de ce que CE checkout embarque (pas de `_keys/` : capacité absente).
    # Ce qui est gardé est qu'elle DIT quelque chose d'honnête et reste debout — jamais qu'elle verdit.
    assert "○" in capsys.readouterr().out


def test_le_meme_key_id_declare_deux_fois_est_refuse(tmp_path: Path):
    """La vérification indexe PAR `key_id` : deux clés sous le même identifiant, et l'une des deux serait
    ignorée en silence. Une clé qu'on croit accepter sans l'accepter est le pire état d'une rotation — on
    refuse le jeu entier plutôt que d'en garder la moitié."""
    _priv, pub, kid = _paire()
    doublon = tmp_path / "doublon.json"
    doublon.write_text(json.dumps({"keys": [{"key_id": kid, "public": _b64(pub)},
                                            {"key_id": kid, "public": _b64(pub)}]}), encoding="utf-8")
    with pytest.raises(uc.ChannelMalformed, match="deux fois"):
        uc.trust_root(doublon)


def test_la_boucle_tire_AU_DEMARRAGE_puis_a_intervalle(live: Settings):
    """Le reaper dort d'abord — il n'a rien à réaper au boot. Le canal, si : un daemon qu'on vient de
    redémarrer est précisément le moment où quelqu'un veut savoir."""
    tours: list[int] = []

    async def _scenario():
        tache = asyncio.create_task(
            uc.run_channel_poll(live, interval_s=3600, refresher=lambda _s: tours.append(1)))
        await asyncio.sleep(0.05)                        # laisse le premier tour se jouer dans son thread
        tache.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tache

    asyncio.run(_scenario())
    # L'intervalle est d'une heure : si le tour n'avait lieu qu'APRÈS le premier sommeil, la liste serait
    # vide — c'est ce qui distingue « au démarrage puis à intervalle » de « à intervalle ».
    assert tours, "le premier tour n'a pas eu lieu avant le premier sommeil"


def test_la_boucle_NE_BLOQUE_PAS_la_boucle_devenements(live: Settings):
    """« Sans jamais bloquer le daemon » se tient ici, ou nulle part. Le tirage est de l'I/O BLOQUANTE : un
    sleep-loop qui appellerait `urllib` en direct gèlerait la boucle d'événements — donc toutes les requêtes
    HTTP et tous les WebSocket du daemon — jusqu'au timeout. La preuve est un battement qui doit continuer
    à battre PENDANT que le tirage dort."""
    import time
    battements = 0

    def _tirage_lent(_settings):
        time.sleep(0.25)                                 # bloquant, comme `urllib`

    async def _scenario():
        nonlocal battements
        tache = asyncio.create_task(uc.run_channel_poll(live, interval_s=3600, refresher=_tirage_lent))
        debut = time.monotonic()
        while time.monotonic() - debut < 0.25:
            battements += 1
            await asyncio.sleep(0.01)
        tache.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tache

    asyncio.run(_scenario())
    # Sans `to_thread`, la boucle serait figée pendant les 250 ms du tirage et on compterait ~1 battement.
    assert battements > 10, f"la boucle d'événements a été bloquée par le tirage ({battements} battements)"


def test_la_boucle_survit_a_un_tirage_qui_leve(live: Settings):
    """`refresh` ne lève pas ; si quelque chose remonte quand même, c'est un défaut de CE module — et la
    boucle doit le DIRE sans mourir. Un canal muet vaut mieux qu'un daemon qui perd sa tâche en silence."""
    appels = []

    def _casse(_settings):
        appels.append(1)
        raise RuntimeError("défaut interne")

    async def _scenario():
        tache = asyncio.create_task(uc.run_channel_poll(live, interval_s=0.01, refresher=_casse))
        await asyncio.sleep(0.05)
        vivante = not tache.done()
        tache.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tache
        return vivante

    assert asyncio.run(_scenario()), "la boucle est morte sur un tour raté"
    assert len(appels) > 1, "la boucle n'a pas retenté après l'échec"


# --- 6. la CLI ----------------------------------------------------------------------------------------


def test_update_check_rend_TOUJOURS_rc_0(live: Settings, tmp_path: Path, monkeypatch, capsys):
    """Une QUESTION, pas un geste — parité stricte avec `update wheels` et `update aptitude`. Un réseau
    injoignable n'est pas un échec de la commande : c'est une réponse, et elle s'affiche."""
    monkeypatch.setattr(uc, "trust_root", lambda *_a, **_k: [])
    assert uc.cli_check(live, build_sha="f" * 40) == 0
    sortie = capsys.readouterr().out
    assert "capacité absente" in sortie and "aucune racine de confiance" in sortie


def test_update_check_dit_que_le_canal_ANNONCE_et_ne_pose_rien(live: Settings, tmp_path: Path,
                                                               monkeypatch, capsys):
    """Le rayon d'explosion d'une clé volée est borné à une notification mensongère PARCE QUE le produit ne
    télécharge rien. Ce que la commande dit à l'utilisateur doit refléter cette frontière, sinon il
    attendra une mise à jour automatique qui ne viendra pas."""
    priv, pub, kid = _paire()
    racine = _racine(tmp_path / "keys.json", (pub, kid))
    # Capturée AVANT le patch : sinon la lambda se rappellerait elle-même, à l'infini.
    vraie_racine = uc.trust_root
    monkeypatch.setattr(uc, "trust_root", lambda *_a, **_k: vraie_racine(racine))
    monkeypatch.setattr(uc, "fetch", _Compteur(_enveloppe(_annonce(), (priv, kid))))
    monkeypatch.setenv(uc.ENV_URL, "https://exemple.invalide/c.json")

    # Le SHA de build est PRIS DANS LA LIGNÉE de l'annonce : c'est la seule configuration où proposer est
    # fondé, donc la seule où cette phrase a le droit de s'afficher.
    assert uc.cli_check(live, build_sha="d" * 40) == 0
    sortie = capsys.readouterr().out
    assert "0.2.0" in sortie
    assert "ANNONCE" in sortie and "update apply" in sortie


# --- 7. le verdict : de l'état à ce qu'on en FAIT -----------------------------------------------------
#
# L'état d'un tour dit ce qui s'est passé ; le verdict dit ce qu'on en fait. Ces tests gardent la
# distinction, et surtout les deux règles qui empêchent le verdict de mentir : un échec DUR (la
# vérification) prend la tête, un échec MOU (le réseau) fait seulement VIEILLIR — et une absence de la
# lignée est un AVEU, jamais un verdict de divergence.


def _etat(annonce_sha: str = "a" * 40, lignee: list[str] | None = None, *,
          tour: str = "ok", raison: str = "") -> dict:
    """Un état de cache tel que `read_state` le rendrait — sans passer par le réseau ni le disque : le
    verdict est PUR, et le tester à la table est ce qui rend ses sept issues énumérables."""
    succes = {"at": "2026-08-08T10:00:00+00:00",
              "announce": json.loads(_annonce(edition={
                  "version": "0.2.0", "sha": annonce_sha, "committed_at": "2026-08-08T09:00:00+00:00",
                  "wheel": {"name": "f-0.2.0.whl", "sha256": "b" * 64, "size": 12}},
                  lineage=lignee if lignee is not None else ["d" * 40, "e" * 40]).decode())}
    return {"last_success": succes,
            "last_attempt": {"at": "2026-08-08T11:00:00+00:00", "state": tour, "reason": raison}}


def test_le_SHA_annonce_egal_au_notre_rend_a_jour():
    v = uc.verdict(_etat(annonce_sha="9" * 40), build_sha="9" * 40)
    assert v["state"] == "up-to-date"
    assert v["announced"]["version"] == "0.2.0"


def test_notre_SHA_DANS_la_lignee_rend_disponible_car_lannonce_DESCEND_de_nous():
    """La seule configuration où proposer une mise à jour est fondé : l'édition annoncée a été publiée
    APRÈS la nôtre, donc elle en descend. C'est ce que la lignée bornée sert à décider — sans miroir git,
    chez qui n'en a pas."""
    v = uc.verdict(_etat(lignee=["d" * 40, "9" * 40]), build_sha="9" * 40)
    assert v["state"] == "available"


def test_notre_SHA_ABSENT_de_la_lignee_est_un_AVEU_et_ne_propose_RIEN():
    """Trois causes produisent exactement cette absence — instance plus ancienne que la fenêtre, wheel bâti
    maison, divergence réelle. On ne les distingue pas, DONC on ne choisit pas : le verdict dit qu'il ne
    peut pas situer, il n'accuse pas de divergence et il ne propose pas."""
    v = uc.verdict(_etat(lignee=["d" * 40, "e" * 40]), build_sha="9" * 40)
    assert v["state"] == "cannot-situate"
    assert "trois causes" in v["reason"]
    # Le mot qui n'a PAS le droit d'être prononcé sur une absence : ce serait un verdict, pas un aveu.
    assert "as divergé" not in v["reason"]


def test_sans_tampon_de_build_on_ne_se_situe_pas_non_plus():
    """Même aveu, autre cause : on ne sait pas d'où l'on vient, donc on ne peut pas dire où l'on est. Le
    replier sur « à jour » ou sur « en retard » serait un verdict tiré au sort."""
    v = uc.verdict(_etat(), build_sha=None)
    assert v["state"] == "cannot-situate"
    assert "tampon de build" in v["reason"]


def test_une_signature_INVALIDE_prend_la_tete_meme_sur_une_annonce_deja_verifiee():
    """L'échec DUR l'emporte : quelqu'un sert des octets en se réclamant d'une clé qu'on accepte, et c'est
    plus urgent qu'une bonne nouvelle d'hier. L'annonce d'hier n'est pas effacée pour autant — elle est
    rendue à côté, avec sa date."""
    v = uc.verdict(_etat(tour="bad-signature", raison="les octets ne sont pas ceux qu'on a lus"),
                   build_sha="9" * 40)
    assert v["state"] == "unverified"
    assert v["announced"] is not None and v["verified_at"] == "2026-08-08T10:00:00+00:00"


def test_un_reseau_injoignable_NE_DEGRADE_PAS_le_verdict_il_le_fait_VIEILLIR():
    """La contrepartie exacte de la survie de `last_success` : si un tour raté écrasait le verdict, une
    panne de wifi produirait une amnésie — le produit oublierait une édition déjà vérifiée. Le tour raté
    n'est pas caché pour autant : il voyage dans `attempt`."""
    v = uc.verdict(_etat(lignee=["9" * 40], tour="unreachable", raison="HTTP 503"), build_sha="9" * 40)
    assert v["state"] == "available"                    # le verdict SITUE quand même
    assert v["attempt"]["state"] == "unreachable"       # et l'échec du jour est dit, pas dissimulé
    assert v["attempt"]["reason"] == "HTTP 503"


def test_sans_rien_de_verifie_on_dit_LEQUEL_des_deux_silences():
    """« Je n'ai jamais regardé » et « je n'ai pas pu lire » n'appellent pas la même réparation. Les fondre
    en un seul silence obligerait l'utilisateur à deviner s'il doit attendre ou vérifier son réseau."""
    jamais = {"last_success": None, "last_attempt": dict(uc._JAMAIS)}
    assert uc.verdict(jamais, build_sha="9" * 40)["state"] == "never"
    muet = {"last_success": None,
            "last_attempt": {"at": "2026-08-08T11:00:00+00:00", "state": "unreachable", "reason": "DNS"}}
    assert uc.verdict(muet, build_sha="9" * 40)["state"] == "unreachable"


def test_une_edition_SANS_racine_de_confiance_dit_une_CAPACITE_ABSENTE_pas_un_echec():
    """Absence n'est pas panne. Et c'est le verdict le plus prioritaire de tous : présenter « aucune clé
    embarquée » comme un échec de vérification enverrait chercher une panne là où il n'y a rien de cassé."""
    etat = {"last_success": None,
            "last_attempt": {"at": "x", "state": "no-trust-root", "reason": "aucune racine embarquée"}}
    assert uc.verdict(etat, build_sha="9" * 40)["state"] == "no-trust-root"


def test_le_volet_ne_rend_PAS_le_manifeste_entier():
    """Un volet d'API qui recopie le document d'un tiers fait dépendre son propre contrat de la forme de ce
    document. On rend ce qu'une surface a besoin de nommer, et rien de plus."""
    v = uc.verdict(_etat(), build_sha="9" * 40)
    assert set(v["announced"]) == {"version", "sha", "committed_at", "wheel_name", "wheel_sha256",
                                   "lineage_len"}
    assert "schema" not in v["announced"] and "maps" not in v["announced"]


def test_une_signature_invalide_est_BRUYANTE_la_ou_un_reseau_absent_est_DISCRET(live: Settings,
                                                                                tmp_path: Path,
                                                                                monkeypatch, caplog):
    """§12 de la spec de racine de confiance : *un vérificateur PRÉSENT qui échoue est un ÉCHEC, bruyant
    côté log*. Le test mesure le NIVEAU, pas le message — un test qui n'assertait que le texte resterait
    vert si tout retombait en `info`, c'est-à-dire si la règle cessait d'être tenue."""
    import logging as _log

    priv, pub, kid = _paire()
    autre, _, _ = _paire()
    racine = _racine(tmp_path / "keys.json", (pub, kid))
    vraie = uc.trust_root
    monkeypatch.setattr(uc, "trust_root", lambda *_a, **_k: vraie(racine))
    monkeypatch.setenv(uc.ENV_URL, "https://exemple.invalide/c.json")

    # ① signature invalide : les octets se réclament d'une clé qu'on accepte → ERROR.
    monkeypatch.setattr(uc, "fetch", _Compteur(_enveloppe(_annonce(), (autre, kid))))
    with caplog.at_level(_log.DEBUG, logger="forgemaster"):
        uc.refresh(live)
    dur = [r for r in caplog.records if "bad-signature" in r.getMessage()]
    assert dur and dur[0].levelno == _log.ERROR

    # ② réseau injoignable : un train qui ne passe pas n'est pas une alarme → INFO.
    caplog.clear()
    monkeypatch.setattr(uc, "fetch", _Compteur(uc.ChannelUnreachable("HTTP 503")))
    with caplog.at_level(_log.DEBUG, logger="forgemaster"):
        uc.refresh(live)
    mou = [r for r in caplog.records if "unreachable" in r.getMessage()]
    assert mou and mou[0].levelno == _log.INFO
    assert priv is not None                       # la paire légitime existe : c'est ce qui rend ① probant


def test_la_lecture_du_verdict_NE_VA_PAS_sur_le_reseau(live: Settings, monkeypatch):
    """Il est posé sur `/api/version`, une sonde qui ne doit ni pendre ni rendre 500. Prouvé en rendant le
    tirage IMPOSSIBLE : s'il était appelé, le volet lèverait au lieu de rendre un état honnête."""
    def _interdit(*_a, **_k):
        raise AssertionError("le volet a émis une requête — il ne lit QUE le cache disque")

    monkeypatch.setattr(uc, "fetch", _interdit)
    monkeypatch.setattr(uc, "refresh", _interdit)
    vue = uc.read_verdict(live, build_sha="9" * 40)
    assert vue["state"] == "never"


def test_update_check_reste_rc_0_sur_LES_TROIS_verdicts_situes(live: Settings, tmp_path: Path,
                                                               monkeypatch, capsys):
    """Parité stricte avec `wheels`/`aptitude` : c'est une question. Un rc non nul sur « je ne peux pas te
    situer » ferait échouer un script d'exploitation sur un état parfaitement normal.

    Le nom dit **trois** et non « chaque » : ce banc joue les issues qui dépendent du SHA de build, celles
    qu'un même manifeste peut produire. Les quatre autres se jouent ailleurs (`no-trust-root`, `never`,
    cache inconnu) ou n'ont pas de titre à part — et l'exhaustivité, elle, est gardée par le test suivant,
    qui la mesure au lieu de la promettre."""
    priv, pub, kid = _paire()
    racine = _racine(tmp_path / "keys.json", (pub, kid))
    vraie = uc.trust_root
    monkeypatch.setattr(uc, "trust_root", lambda *_a, **_k: vraie(racine))
    monkeypatch.setattr(uc, "fetch", _Compteur(_enveloppe(_annonce(), (priv, kid))))
    monkeypatch.setenv(uc.ENV_URL, "https://exemple.invalide/c.json")
    for sha, attendu in (("a" * 40, "à jour"), ("d" * 40, "plus récente"), ("9" * 40, "NON PROPOSÉE")):
        assert uc.cli_check(live, build_sha=sha) == 0
        assert attendu in capsys.readouterr().out


def test_chaque_verdict_possible_a_son_TITRE_dans_la_CLI():
    """Garde d'EXHAUSTIVITÉ, et pas de politesse : `cli_check` indexe `_TITRE` par le verdict. Un huitième
    état ajouté à `verdict()` sans son titre ferait lever une `KeyError` dans une commande qui PROMET rc 0
    — le défaut ne serait vu qu'en production, et sur l'état neuf, c'est-à-dire le moins joué.

    On mesure ici l'ensemble des issues que `verdict` peut réellement produire, plutôt que de recopier une
    liste : une liste recopiée resterait verte le jour où la fonction en rend une de plus."""
    etats = set()
    for tour in ("never", "ok", "unreachable", "malformed", "internal",
                 "unknown-key", "bad-signature", "no-trust-root"):
        for succes in (None, {"at": "x", "announce": json.loads(_annonce().decode())}):
            for sha in (None, "a" * 40, "d" * 40, "9" * 40):
                etat = {"last_success": succes,
                        "last_attempt": {"at": "x", "state": tour, "reason": "r"}}
                etats.add(uc.verdict(etat, build_sha=sha)["state"])
    assert etats == set(uc._TITRE), f"verdicts sans titre : {etats - set(uc._TITRE)}"
