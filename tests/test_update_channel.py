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
    assert uc.cli_check(live) == 0
    sortie = capsys.readouterr().out
    assert "no-trust-root" in sortie and "Aucune annonce" in sortie


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

    assert uc.cli_check(live) == 0
    sortie = capsys.readouterr().out
    assert "0.2.0" in sortie
    assert "ANNONCE" in sortie and "update apply" in sortie
