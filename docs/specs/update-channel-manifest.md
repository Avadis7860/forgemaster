# spec — le manifeste servi du canal de mise à jour (format, tirage, cache)

> **Livré.** `src/forgemaster/update_channel.py` implémente ce contrat ; `tests/test_update_channel.py` le
> garde. Ce qui n'est **pas** livré et reste explicite ci-dessous : la vraie paire de clés (donc
> `_keys/release-keys.json`), la publication d'une Release, et le **verdict** rendu à l'utilisateur.
> Frère de `update-channel-trust-root.md`, qui fixe **comment on signe** ; celui-ci fixe **ce qu'on signe,
> où on le trouve et quand on va le chercher**.

## Problème tranché

`build_provenance.staleness` compare le SHA de build au HEAD d'un **miroir bare local**, zéro réseau par
invariant. Chez quelqu'un qui n'a pas ce miroir — c'est-à-dire chez l'utilisateur distribué — il rend
`comparable=false`, et il a **raison** de ne rien affirmer. Ce qui manquait n'était pas un calcul : c'était
une **référence joignable**, et de quoi la croire.

## La frontière, avant tout le reste

Deux modules, deux moitiés, et leur séparation est la propriété la plus importante du cycle :

| module | rôle | réseau |
|---|---|---|
| `update.py` | **pose** le wheel qu'on lui désigne | **aucun, jamais** |
| `update_channel.py` | **apprend** qu'une édition existe | oui, et rien d'autre |

Le canal **annonce** — « la version X existe, voici le SHA-256 de son wheel » — et ne télécharge **aucun**
binaire. C'est ce qui borne le rayon d'explosion d'une clé volée à une **notification mensongère**, jamais à
une exécution. Et c'est ce qui fait de `update apply <wheel>` la **voie de secours** quand le canal est
sourd (rotation non suivie, réseau absent) : *la voie hors-ligne est la voie de secours de la voie en ligne.*

Les deux modules n'ont **aucune référence l'un vers l'autre** — le routage de `update check` se fait dans
`cli.py`, pas dans `update.cli_dispatch`. La frontière est ainsi une propriété du graphe d'imports, pas une
consigne dans un commentaire.

## Règles verrouillées

1. **Deux schémas, versionnés SÉPARÉMENT** : `forgemaster-update-channel/1` (l'enveloppe — *comment on
   signe*) et `forgemaster-edition-announce/1` (l'annonce — *ce qu'on dit*). Les fusionner obligerait à
   bousculer l'une pour faire évoluer l'autre.
2. **Un schéma inconnu est REFUSÉ, jamais deviné**, et le refus **nomme** le schéma lu **et** celui qu'on
   sait lire — patron de `restore.load_manifest`. Un refus qui ne dit pas ce qu'il attendait n'est pas
   actionnable pour qui publie.
3. **`signatures` est une LISTE non vide.** Un scalaire est refusé, pas enveloppé par indulgence :
   l'accepter rendrait impossible le chevauchement de rotation sans casser le format chez les instances qui
   l'ont déjà lu.
4. **Le payload reste OPAQUE jusqu'à vérification** (*parse-after-verify*). L'enveloppe est parsée — il faut
   bien lire `signatures` — mais le payload n'est que **décodé** (défaire un encodage de transport n'est pas
   le lire) et n'est parsé qu'après le verdict de signature.
5. **base64url STRICT** (`validate=True`, padding exigé). Le décodage indulgent de la stdlib **jette** les
   caractères hors alphabet : deux textes distincts décoderaient vers les mêmes octets et vérifieraient sous
   la même signature — exactement la latitude qu'une signature existe pour retirer.
6. **Séparation de domaine** : ce qui est signé est `"<schéma d'enveloppe>\n" + payload`, jamais le payload
   nu. Sans elle, un payload signé sous la v1 serait rejouable dans une enveloppe v2 dont la sémantique
   aurait changé.
7. **L'annonce porte le SHA-256 du wheel, et une annonce sans lui est refusée.** C'est la seule chose qui
   permettra de confronter le fichier que l'utilisateur ira chercher à ce qui a été signé.
8. **La lignée est bornée et le dépassement est un REFUS, jamais une troncature.** Tronquer en silence
   changerait « je ne peux pas te situer » en « tu n'es pas dans la lignée » : un **aveu** deviendrait un
   **verdict**.
9. **Le corps est plafonné EN FLUX**, avant matérialisation — on lit `plafond + 1` octet, celui qui sert à
   savoir qu'on l'a dépassé. Lire d'abord et mesurer ensuite est un plafond qui ne protège de rien.
10. **Seul le schéma d'URL est contrôlé** (`http`/`https`) ; la redirection est **suivie sans réserve**. On
    ne fait confiance ni à l'hôte, ni au certificat, ni au chemin — uniquement à la signature. C'est aussi
    ce qui rend inoffensive la surcharge d'URL par l'environnement : **l'URL est un paramètre, pas une
    garantie.** Le refus des autres schémas empêche qu'une surcharge transforme un GET en lecture de
    fichier local.
11. **Sans racine de confiance embarquée, AUCUNE requête n'est émise.** Une édition qui ne peut rien
    vérifier jetterait la réponse : aller la chercher serait une exposition pour rien.
12. **Le tirage a lieu au démarrage PUIS à intervalle**, et il part dans un **thread** : l'I/O est
    bloquante, et un sleep-loop qui appellerait `urllib` en direct gèlerait la boucle d'événements — donc
    toutes les requêtes HTTP et tous les WebSocket du daemon — jusqu'au timeout. Le premier tour précède le
    premier sommeil : un daemon qu'on vient de redémarrer est le moment où quelqu'un veut savoir.
13. **L'état vit sur le DISQUE, écrit atomiquement**, et sépare `last_success` de `last_attempt` : un réseau
    injoignable fait **vieillir** ce qu'on savait, il ne l'efface pas. La raison est celle déjà payée par
    `update.run_state` : le processus qui répond à la question n'est ni celui qui a mesuré, ni forcément le
    même binaire.
14. **Chaque façon d'échouer a son PROPRE état** — `unreachable`, `malformed`, `unknown-key`,
    `bad-signature`, `no-trust-root`, `internal`, `never`. Elles n'appellent pas les mêmes réparations, et
    un `error` unique obligerait à lire un message libre pour savoir quoi faire. En particulier
    `unknown-key` ≠ `bad-signature` (cf. `update-channel-trust-root.md` §8) et `never` ≠ `ok`.
15. **`refresh` ne lève jamais** — un canal muet ne fait pas tomber un daemon. Le filet de dernier recours
    rend l'état **dédié** `internal` plutôt que de se déguiser en panne réseau : avaler un défaut de code
    sous « injoignable » ferait chercher une panne d'infrastructure.
16. **`update check` rend toujours rc 0** — c'est une question, pas un geste (parité avec `update wheels` et
    `update aptitude`). Un réseau injoignable est une **réponse**, pas un échec de commande.

## Les deux documents

```json
{ "schema": "forgemaster-update-channel/1",
  "payload": "<base64url — octets opaques jusqu'à vérification>",
  "signatures": [ { "key_id": "<16 hex, dérivé>", "sig": "<base64url>" } ] }
```

```json
{ "schema": "forgemaster-edition-announce/1",
  "published_at": "<ISO 8601>",
  "edition": { "version": "…", "sha": "<commit>", "committed_at": "<ISO 8601>",
               "wheel": { "name": "…whl", "sha256": "…", "size": 0 },
               "maps": [ { "name": "code-map", "sha": "…" } ] },
  "lineage": [ "<sha de l'édition publiée précédente>", "…" ] }
```

**Pourquoi une lignée et pas seulement la dernière édition.** La divergence « classe B » — *l'édition amont
descend-elle de ce que cette instance exécute ?* — doit être décidable **sans miroir git**, puisque c'est
précisément ce qui manque à l'utilisateur visé. La lignée le permet : l'instance cherche son propre SHA de
build dedans. Son absence est un **aveu** (« je ne peux pas te situer »), jamais un verdict de divergence :
trois causes distinctes produisent la même absence — instance plus ancienne que la fenêtre, wheel bâti
maison depuis un commit jamais publié, ou vraie divergence. On ne les distingue pas, donc on ne choisit pas.

**Pourquoi le manifeste servi est versionné alors que `_maps/maps.json` ne l'est pas.** Le manifeste local
voyage **dans le même wheel que son lecteur** (`tools.edition_maps_dir()`), donc aucun écart de version
n'est possible. Le manifeste servi est lu par des binaires **arbitrairement anciens**. Asymétrie mesurée,
pas oubli.

## Hébergement

Un **asset de Release** — une URL qui ne change pas de version en version, servie anonymement, **sans jeton
et sans l'API GitHub** (le poll de Releases est écarté : il exige un compte que l'utilisateur n'a pas de
raison d'avoir, et il est limité en débit ; le **téléchargement** d'un asset public, lui, reste anonyme).

Écarté : un fichier suivi sur `main`. Publier une annonce deviendrait un **commit sur `main`**, que ce dépôt
n'avance que promu depuis un `dev` vert — la publication entrerait en collision avec la règle de branche.

## Ce que cette spec ne décide PAS

- **Le verdict** : ce que l'utilisateur voit, ce qu'une édition non descendante déclenche, ce qui est écrit
  au log. Le module rend un état ; personne ne le lit encore.
- **La cérémonie de génération de la paire** et la publication de la première Release.
- **La proposition et le consentement** — accepter / différer / refuser est une autre pièce.

## Invariants de test

Ce que `tests/test_update_channel.py` garde, et qu'aucune évolution ne doit rendre vert par accident :

- un schéma inconnu (enveloppe **ou** annonce) est refusé en **nommant les deux** ;
- `signatures` scalaire, vide ou absent ⇒ refus ;
- **parse-after-verify falsifié** : un payload volontairement illégal en JSON avec une signature fausse doit
  produire un refus de **signature**, jamais un refus de **parseur** ;
- un octet modifié dans le payload ⇒ refus ;
- `key_id` inconnu et signature invalide sont deux exceptions **sans lien d'héritage** ;
- une signature qui désigne A mais vérifie sous B ⇒ **refus** ;
- deux signatures, une seule clé connue ⇒ **vérifie** (chevauchement de rotation) ;
- une racine **absente** rend `[]` ; une racine **présente** dont le `key_id` ne se re-dérive pas ⇒ **lève** ;
- racine vide ⇒ **zéro appel** au tirage (compté, pas déduit de l'état final) ;
- corps au-delà du plafond ⇒ refus, et la lecture elle-même n'a demandé que `plafond + 1` ;
- schéma d'URL non http(s) ⇒ refus ;
- réseau injoignable ⇒ `last_success` **survit** ; état tronqué sur disque ⇒ lu comme absent, sans lever ;
- chaque panne rend son **propre** état, chacun porteur d'une raison non vide ;
- la boucle tire **avant** le premier sommeil, **ne bloque pas** la boucle d'événements (battement mesuré
  pendant un tirage bloquant), et **survit** à un tour qui lève ;
- **et, côté `update.py` : le chemin complet de `apply --dry-run` n'ouvre AUCUNE socket** — `socket.socket`
  et `socket.create_connection` rendus impossibles, `rc 0` exigé. C'est la garde que
  `update-channel-trust-root.md` annonçait manquante ; elle existe désormais.
