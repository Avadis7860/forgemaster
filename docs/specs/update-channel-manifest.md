# spec — le manifeste servi du canal de mise à jour (format, tirage, cache)

> **Livré.** `src/forgemaster/update_channel.py` implémente ce contrat ; `tests/test_update_channel.py` le
> garde. Le **verdict** rendu à l'utilisateur est livré depuis le 2026-08-08 (§« Le verdict » plus bas) et
> se lit sur `GET /api/version` (volet `channel`) comme dans `forgemaster update check`. Ce qui n'est
> **pas** livré et reste explicite ci-dessous : la vraie paire de clés (donc `_keys/release-keys.json`) et
> la publication d'une Release.
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
14. **Le cache est VERSIONNÉ, et un cache de schéma inconnu se lit comme ABSENT.** Il est écrit par le
    binaire d'avant une mise à jour et relu par celui d'après : contrairement à `_maps/maps.json`, il ne
    voyage **pas** avec son lecteur — l'asymétrie qui dispense l'un de se versionner condamne l'autre à le
    faire. Le jeter est honnête (l'état se reconstruit au tour suivant) là où refuser inventerait une panne
    et où le lire au jugé ferait planter une CLI sur une clé absente.
15. **Chaque façon d'échouer a son PROPRE état** — `unreachable`, `malformed`, `unknown-key`,
    `bad-signature`, `no-trust-root`, `internal` pour un tour de canal, plus `never` que seul le **lecteur**
    produit (aucun tour n'a encore eu lieu, ou le cache était illisible). Elles n'appellent pas les mêmes
    réparations, et un `error` unique obligerait à lire un message libre pour savoir quoi faire. En
    particulier `unknown-key` ≠ `bad-signature` (cf. `update-channel-trust-root.md` §8) et `never` ≠ `ok`.
    Quand rien ne vérifie, `bad-signature` **l'emporte** sur `unknown-key` : quelqu'un qui prétend détenir
    une clé qu'on accepte est un signal plus fort qu'une rotation non suivie.
16. **Un `key_id` déclaré deux fois dans la racine est refusé** — la vérification indexe par `key_id`, donc
    l'une des deux clés serait ignorée en silence. Une clé qu'on croit accepter sans l'accepter est le pire
    état possible d'une rotation.
17. **`refresh` ne lève jamais** — un canal muet ne fait pas tomber un daemon. Le filet de dernier recours
    rend l'état **dédié** `internal` plutôt que de se déguiser en panne réseau : avaler un défaut de code
    sous « injoignable » ferait chercher une panne d'infrastructure.
18. **`update check` rend toujours rc 0** — c'est une question, pas un geste (parité avec `update wheels` et
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
  "lineage": [ "<sha du commit qui précède, du plus récent au plus ancien>", "…" ] }
```

**Pourquoi une lignée et pas seulement la dernière édition.** La divergence « classe B » — *l'édition amont
descend-elle de ce que cette instance exécute ?* — doit être décidable **sans miroir git**, puisque c'est
précisément ce qui manque à l'utilisateur visé. La lignée le permet : l'instance cherche son propre SHA de
build dedans. Son absence est un **aveu** (« je ne peux pas te situer »), jamais un verdict de divergence :
trois causes distinctes produisent la même absence — instance plus ancienne que la fenêtre, wheel bâti
maison depuis un commit jamais publié, ou vraie divergence. On ne les distingue pas, donc on ne choisit pas.

**La lignée est une ASCENDANCE de commits, pas la liste des éditions publiées** — recalé le 2026-08-08 (phase
5·5), et ce n'est pas une décision neuve : c'est la lecture que les trois causes ci-dessus imposaient déjà.
« Instance plus ancienne que la **fenêtre** » et « wheel bâti maison depuis un commit **jamais publié** » ne
veulent rien dire si la lignée ne contient que ce qui a été publié — le second serait alors la règle et non
l'exception. Une instance exécute un **commit** ; la question posée est *descends-tu de lui ?*. Mesurée par
`git rev-list --first-parent` depuis l'édition annoncée (exclue : elle n'est pas dans sa propre lignée),
bornée au plafond **à la publication** comme elle l'est **à la lecture**.

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

## Le verdict — de l'état à ce qu'on en FAIT

L'état d'un tour dit ce qui **s'est passé** ; le verdict dit ce qu'on en **fait**. Les fondre condamnerait
l'un des deux à mentir : « injoignable » n'est pas une réponse à *dois-je mettre à jour ?*, et « une édition
existe » n'est pas une réponse à *mon dernier contrôle a-t-il abouti ?*. Le volet rend donc **les deux** —
`state` (le verdict) et `attempt` (le dernier tour) — jamais l'un à la place de l'autre.

19. **Sept issues, et aucune ne verdit par défaut** : `never` · `no-trust-root` · `unverified` ·
    `unreachable` · `up-to-date` · `available` · `cannot-situate`.
20. **`no-trust-root` est prioritaire sur tout.** Une capacité absente n'est pas un échec de vérification ;
    la présenter comme tel enverrait chercher une panne là où il n'y a qu'une édition sans clé.
21. **Un échec DUR l'emporte, un échec MOU fait seulement VIEILLIR.** `bad-signature` / `unknown-key`
    prennent la tête **même sur une annonce déjà vérifiée** — des octets qui se réclament d'une clé qu'on
    accepte sont plus urgents qu'une bonne nouvelle d'hier. Un réseau injoignable, lui, ne dégrade **pas**
    le verdict : c'est la contrepartie exacte de la survie de `last_success` (règle 13), sans quoi une panne
    de wifi produirait une amnésie. L'annonce vieillie reste rendue dans les deux cas, avec sa date.
22. **`available` est le SEUL état qui propose**, et il exige que le SHA de build soit **dans la lignée** :
    l'édition annoncée doit **descendre** de ce qu'on exécute. C'est ce qui rend la divergence classe B
    décidable **sans miroir git**, chez qui n'en a pas.
23. **`cannot-situate` est un AVEU, jamais un verdict de divergence.** Trois causes distinctes produisent la
    même absence de la lignée — instance plus ancienne que la fenêtre publiée, wheel bâti maison depuis un
    commit jamais publié, divergence réelle. On ne les distingue pas, **donc on ne choisit pas** : rien
    n'est proposé, rien n'est reproché, et **le ton ne rougit pas**. Une instance sans tampon de build tombe
    dans le même aveu — on ne sait pas d'où elle vient, donc on ne peut pas dire où elle est.
24. **Une signature invalide est IGNORÉE côté produit et BRUYANTE côté log.** Les deux ne se contredisent
    pas : l'un refuse d'agir sur des octets non authentifiés, l'autre refuse de les taire. Le niveau est
    **gradué** — `bad-signature` en ERROR, `unknown-key` / `malformed` / `internal` en WARNING, un réseau
    injoignable en INFO. Un niveau unique nierait que ces états n'ont pas le même poids.
25. **Le volet ne rend pas le manifeste entier**, seulement ce qu'une surface a besoin de nommer. Un volet
    d'API qui recopie le document d'un tiers fait dépendre son propre contrat de la forme de ce document.
26. **Le verdict est composé chez l'APPELANT** (la route HTTP, la CLI), pas dans `build_provenance` : ce
    module porte la contrainte « aucun accès réseau » comme propriété de son graphe d'imports, et lui faire
    importer le canal la dégraderait en affaire de confiance. Le `build_sha` est **passé**, sans valeur par
    défaut — un défaut ferait rendre « non situable » à une instance tamponnée dont l'appelant aurait oublié
    l'argument.
27. **Un seul rappel poussé, et le canal l'emporte dès qu'il fait autorité.** Le canal est la référence que
    l'utilisateur visé peut joindre et qui ne vieillit pas avec son instance ; le miroir local vieillit avec
    elle. « Faire autorité » n'est pas « exister » : un canal muet, injoignable ou non vérifié ne fait taire
    personne. Sur une machine qui a les deux références, deux rappels pour le même fait apprennent à ignorer
    le centre de notifications.

## Publier — le producteur, et pourquoi il ne vit pas chez le client

`src/forgemaster/channel_publish.py` produit ce que `update_channel.py` passe sa vie à lire. Les deux sont
**symétriques et jamais fondus** : le producteur importe le client (schémas, plafond, dérivation de `key_id`,
message signé) et **l'inverse est impossible**, gardé par AST. Deux propriétés en sortent, qu'aucun
commentaire ne pourrait tenir — **rien du chemin de mise à jour ne peut atteindre le signeur**, et le
producteur ne redéfinit aucune constante (un schéma ou un domaine de signature qui aurait deux valeurs se
découvrirait le jour où plus rien ne vérifie).

Règles verrouillées :

28. **L'annonce est fonction de l'ARTEFACT**, pas du poste qui l'a bâti : `version` (le `Version:` du
    METADATA, pas le nom du fichier), `sha`, `committed_at` et `maps` sont lus **dans le wheel** ; le
    SHA-256 et la taille sont ceux du fichier qu'on publiera. Un producteur qui lirait le répertoire de
    travail pourrait annoncer un commit et publier un autre binaire — et la signature couvrirait le mensonge.
29. **Le `key_id` est dérivé de la clé qui signe**, jamais passé en argument. Le passer rouvrirait
    exactement le mensonge que la dérivation existe pour rendre impossible.
30. **La privée arrive sur l'entrée standard** (`scripts/publish_channel.py`) — jamais `argv` (visible dans
    `ps`), jamais l'environnement (hérité), jamais un fichier. Le script ne résout aucun secret : le coffre
    reste le seul résolveur, et il vit hors de ce dépôt.
31. **L'annonce produite est relue et vérifiée avec le code du CLIENT, sous la racine embarquée dans le
    wheel annoncé**, avant d'être écrite. Sans ce contrôle, les deux moitiés ne se rencontreraient que chez
    l'utilisateur — et le symptôme y serait `unverified`, le pire verdict, celui qui apprend à ignorer
    l'alarme.
32. **Ce qui n'est PAS distribué est la CLÉ, pas le code.** Le module producteur vit sous
    `src/forgemaster/` et voyage donc dans le wheel ; seul le point d'entrée mainteneur (`scripts/`) en est
    exclu. Dire « le producteur n'est pas distribué » serait de la rhétorique : le packaging ne le fait pas.
    Les deux propriétés qui portent vraiment sont ailleurs — le **graphe d'imports** (rien du chemin de
    mise à jour ne peut atteindre le signeur) et le fait qu'une privée n'existe **que** dans le coffre. Le
    module embarqué est inerte sans elle.

## Ce que cette spec ne décide PAS

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
  un `key_id` déclaré **deux fois** ⇒ **lève** ;
- un cache de **schéma inconnu** est lu comme absent, et `update check` reste **debout** dessus (rc 0) ;
- racine vide ⇒ **zéro appel** au tirage (compté, pas déduit de l'état final) ;
- corps au-delà du plafond ⇒ refus, et la lecture elle-même n'a demandé que `plafond + 1` ;
- schéma d'URL non http(s) ⇒ refus ;
- réseau injoignable ⇒ `last_success` **survit** ; état tronqué sur disque ⇒ lu comme absent, sans lever ;
- chaque panne rend son **propre** état, chacun porteur d'une raison non vide ;
- la boucle tire **avant** le premier sommeil, **ne bloque pas** la boucle d'événements (battement mesuré
  pendant un tirage bloquant), et **survit** à un tour qui lève ;
- **le verdict** : `up-to-date` / `available` / `cannot-situate` décidés à la table sur les trois formes de
  lignée · `unverified` l'emportant sur un `last_success` présent · un `unreachable` qui **ne dégrade pas**
  le verdict et voyage dans `attempt` · les trois silences (`never`, `unreachable`, `no-trust-root`)
  **distincts** · le volet qui ne rend PAS le manifeste entier ;
- **le niveau de log MESURÉ** (`levelno`, pas le message) : `bad-signature` ⇒ ERROR, injoignable ⇒ INFO. Un
  test qui n'assertait que le texte resterait vert si tout retombait en `info` ;
- **le volet n'émet aucune requête** — `fetch` et `refresh` rendus explosifs, la sonde reste debout ;
- **les DEUX tâches de fond du `_lifespan` démarrent et sont coupées au shutdown**, falsifié par mutation
  tâche par tâche (`tests/test_daemon.py`) — le module était couvert, le **branchement** ne l'était pas ;
- **côté surface** : l'arbitrage du rappel (le canal fait taire le miroir dès qu'il fait autorité, le miroir
  reprend la parole quand le canal est muet) et un `ChannelSchema` qui **refuse** un état inconnu au parse
  plutôt que de le laisser prendre l'apparence du cas par défaut ;
- **et, côté `update.py` : le chemin complet de `apply --dry-run` n'ouvre AUCUNE socket** — `socket.socket`
  et `socket.create_connection` rendus impossibles, `rc 0` exigé. C'est la garde que
  `update-channel-trust-root.md` annonçait manquante ; elle existe désormais.

Ce que `tests/test_channel_publish.py` garde en plus, côté **producteur** :

- **l'aller-retour complet** : produire avec `channel_publish`, relire avec `update_channel`. C'est la garde
  qui vaut le plus — elle échoue le jour où l'un des deux côtés dérive, ce qu'aucun test de forme ne verrait
  puisque les deux resteraient individuellement corrects ;
- **la frontière par AST** : `update_channel` n'importe **jamais** `channel_publish` — rien du chemin de mise
  à jour ne peut atteindre le signeur ; le producteur, lui, ne **redéfinit** aucun schéma ;
- **la séparation de domaine est porteuse** : signer le payload **nu** produit une enveloppe qui ne vérifie
  pas — mesuré, pas supposé ;
- **l'annonce est lue dans le wheel** : un wheel dont le tampon dit autre chose que le dépôt annonce ce que
  dit le **wheel** ; un wheel sans tampon n'est **pas annonçable** ;
- **la lignée au-delà du plafond est refusée** (jamais tronquée), et le SHA annoncé ne peut pas figurer dans
  sa **propre** lignée ;
- **une privée de 64 octets est refusée en nommant les 32 attendus** — c'est la forme « étendue » d'autres
  bibliothèques, le cas où un refus muet enverrait chercher un défaut de crypto là où il y a un défaut de
  pipe ;
- **`key_id` dérivé** de la clé qui signe, et une enveloppe signée par une autre clé produit un `key_id`
  **inconnu**, jamais une signature invalide.
