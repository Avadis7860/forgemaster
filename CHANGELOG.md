# Changelog

Format [Keep a Changelog](https://keepachangelog.com/). Un changement de **schéma** (SQLite / roadmap.yaml
/ API HTTP — cf. `docs/schema-contract.md`) est une entrée dédiée + un bump, jamais en douce.

> **Le produit s'appelait `cockpit` jusqu'au 2026-08-04.** Les entrées antérieures gardent ce nom : elles
> décrivent des faits qui ont eu lieu sous lui, et un changelog qu'on réécrit n'est plus un changelog.
> Motif du renommage et périmètre : voir l'entrée du 2026-08-04 ci-dessous.

## [Unreleased]

### Le retour arrière sait défaire une MAJ qui n'a pas migré la base

`forgemaster update rollback` refusait **tout** après une mise à jour non migrante — « il correspond au venv
DÉJÀ actif », sur chaque instantané d'affilée. La plupart des MAJ ne migrent pas : l'obligation « revenir
d'un geste » était donc défaite dans le cas le **plus courant**, et invisible pour tous nos bancs, qui
reviennent tous après une MAJ migrante. Mesuré sur vrai systemd le 2026-08-07, jamais relu.

- **Ce que le résolveur départage, et sur quoi.** La cible d'un retour est le binaire qui tournait **quand
  l'instantané a été pris**. L'égalité de schéma n'en était qu'une *dérivation*, exacte tant qu'un seul venv
  la satisfaisait ; quand rien n'a migré ils sont deux, et le plus récent est celui qu'on cherche à quitter.
  Le run qui a pris l'instantané le nomme dans le même `result.json` (`instantane` / `instantane_surete` +
  `venv_avant`) : c'est ce qui **choisit** désormais. Aucun état nouveau, `SCHEMA` inchangé, et ça vaut pour
  les instantanés **déjà pris**.
- **L'égalité de schéma reste la garde**, et **l'appariement promeut sans jamais bloquer** : un venv apparié
  absent, non sondable ou d'un autre schéma n'est pas une cible, et on retombe alors sur l'ordre par récence.
  Refuser à la place priverait d'un retour parfaitement valide — le repli satisfait le même garde de
  `restore.check_compatibility` — pour un journal incohérent.
- **Une règle, deux marches.** `snapshot list` disait `restaurable` ✔ pendant que le verbe refusait : l'état
  et la résolution consultent maintenant le même appariement, comme elles lisent déjà la même liste.
- **Le refus qui reste dit ce qu'il n'a pas pu départager** : quand aucun journal ne nomme l'instantané (pris
  à la main, run effacé), il le dit, au lieu d'avoir l'air arbitraire.
- **Un cinquième refus, ouvert par le correctif lui-même et mesuré à la table** : le garde qui empêchait le
  va-et-vient comparait les schémas, donc il était muet quand rien n'a migré — un `update rollback` rejoué
  après un retour non migrant serait reparti vers le binaire qu'on venait de quitter. Un instantané né d'un
  `rollback` (sa prise de sûreté) n'est plus jamais une cible, et `snapshot list` le dit sous sa ligne : il
  reste `restaurable`, mais ce n'est pas la cible du prochain retour.

### Le détecteur de panne — un wheel à interface blanche ne passe plus

Le critère « ça sert » d'une MAJ était `/health` **200** + le SHA de `/api/version`. C'est de la **plomberie** :
un wheel dont la SPA est cassée (`_web_dist` vide, bundle tronqué, erreur JS avant le mount) satisfait les
deux et sert une page blanche en se déclarant posé. Les deux vérifications de `apply_update` chargent
désormais la page dans un vrai navigateur.

- **Le contrat d'interface voyage avec le wheel** (`forgemaster/_ui_contract.json`), jamais en dur dans
  l'applicateur : `update.spawn` copie l'`apply_update.py` de la version **installée**, donc c'est le vieux
  applicateur qui juge le nouveau wheel — un libellé épinglé dans le script ferait échouer toute MAJ qui le
  renomme. Le lockstep avec `web/src/App.tsx` est tenu par un test.
- **Deux endroits, deux questions.** En **isolation** (home vierge) c'est le *build* qui est jugé, et le refus
  y coûte un venv jetable : le wheel à interface blanche n'atteint jamais le vivant. **En vivant** c'est la
  *rencontre* du binaire et des données réelles — et cet échec-là déclenche le **retour arrière automatique**,
  par le chemin qui existe depuis le premier jour.
- **Le juge vient de l'hôte**, jamais du wheel qu'il juge : la cascade du gate Tier-1.5, telle quelle
  (`$FORGEMASTER_VERIFY_RUNNER` → `<home>/runners/render_check.js`), et le runner doit avoir son
  `node_modules/playwright-core` à côté — un runner sans sa dépendance n'est pas un runner. Le runner
  embarqué dans le wheel a été envisagé en repli puis **écarté sur mesure** : son `node_modules` est
  gitignoré, donc absent du wheel ; le retenir aurait transformé une dégradation annoncée en MAJ refusée.
- **Absence n'est pas panne.** Pas de Node, pas de runner, pas de contrat → on retombe sur `/health` + SHA
  **et on l'annonce** : `describe` porte la ligne, donc le panneau `/settings` l'affiche *avant* le geste, et
  le verdict la porte après. Les refus de ce module restent réservés à ce qui **casserait**. En revanche un
  juge présent qui plante, expire ou rend un verdict illisible est un **échec** — jamais blanchi.
- **Revenir n'exige pas de preuve.** Une cible de retour arrière antérieure à cette vérification ne déclare
  aucun contrat : son absence ne bloque jamais un rollback.
- Chaque passage archive sa capture (`ui-isolation.png`, `ui-live.png`) dans le dossier du run.
- Garde-fou de packaging : `deploy/build-wheel.sh` refuse un wheel sans `forgemaster/_ui_contract.json`.

Aucun changement de schéma (le contrat est un fichier du paquet, pas une surface).

### Le cycle de MAJ depuis le produit — et ce qu'il montre quand son backend meurt (web + 3 champs additifs)

Panneau **Mise à jour** en tête de `/settings` : déposer → prévisualiser → poser → suivre → revenir, sans
jamais ouvrir un terminal. Il consomme les sept routes de `/api/update` et n'en invente aucune.

- **Rien ne vit dans le navigateur** — pas de `localStorage`, pas de machine à états, pas d'identifiant de run
  gardé entre deux vies de la page : au montage, le panneau **redécouvre** le geste en cours par
  `GET /api/update/runs`. C'est la symétrie exacte du serveur, qui relit tout du disque pour la même raison —
  le daemon qui sert cette page est celui que la MAJ arrête et remplace.
- **Le silence du daemon n'est pas une erreur, c'est un état — avec un âge.** `lib/updateLiaison` (pur, testé
  à la table) rend quatre états : `servie` · `bascule` (« c'est attendu », un geste est en vol) · `perdue`
  (au-delà de la borne du produit, on l'**avoue** au lieu de faire tourner un sablier sans fin) ·
  `injoignable` (aucun geste parti — un daemon muet reste un daemon muet, **jamais** maquillé en bascule).
  La reconnexion n'a pas de code : `refetchInterval` continue de battre pendant qu'une requête est en erreur.
- **La prévisualisation EST le consentement** : pas de modale, le geste mutant est gardé par un `GET`
  idempotent (même doctrine que `git/sync` → `reconcile`). Un **409** affiche son texte **intégral** et
  **désarme** le bouton.
- **Trois champs additifs**, tous pour la même raison — *une borne annoncée vient de la borne qui s'applique*,
  et une surface ne re-déclare pas ce que le cœur déclare : `GET /runs/{id}` rend **`impact`** (jusqu'où ça a
  été, distinct du verdict qui dit ce qui s'est passé ; `null` tant qu'aucun verdict n'est écrit) ·
  `GET /runs` rend **`follow_timeout`** · `GET /wheels` rend **`max_bytes`**. Additifs → aucun bump de
  `SCHEMA_VERSION`.

### L'artefact arrive par la route, et l'aire de dépôt a une politique (routes neuves, aucun schéma touché)

`POST /api/update/wheels` (multipart) → **201** `{stamp, name, path, size, sha256, staged_at, pruned}` ·
`GET /api/update/wheels` → `{wheels, total, keep}` · `forgemaster update wheels` (vue CLI en lecture).

- **Pourquoi.** `apply` ne pose que le fichier qu'on lui désigne, et **HTTP n'a pas de système de fichiers** :
  un utilisateur distribué a son wheel dans son navigateur, pas sur le disque de son instance. Le `path` rendu
  se repasse tel quel à `GET /plan` puis `POST /apply` — après quoi le cycle est atteignable **sans terminal**.
- **`apply` n'est PAS confiné à l'aire**, et c'est délibéré : le canal servi fera arriver un wheel ailleurs.
  Le dépôt **ajoute une source**, il ne devient pas la seule.
- **Quatre gardes, et l'ordre porte du sens** — nom nu (**400**) · extension `.whl` (**415**) · confinement du
  chemin résolu (**400**) · taille mesurée **pendant** le flux (**413**). Un artefact hostile de 100 Mo visant
  `../` est refusé **pour son nom** : le rejeter pour son poids laisserait croire qu'un plus petit passerait.
  Les trois exceptions sont celles de `content.upload` — les handlers globaux les mappent déjà.
- **Lecture en flux, handler synchrone.** Le patron d'origine matérialise toute la part avant de la borner :
  tenable sous 10 Mo, pas sur le daemon qui s'apprête à se remplacer lui-même. Ce que le flux n'achète pas :
  le parseur multipart a déjà déversé la part sur le disque — autre défaut, fiché, pas maquillé.
- **Rétention déclarée dès le premier jour** (`KEEP_WHEELS` = 3), appliquée **à l'écriture** et non par un
  minuteur, qui **épargne** tout dépôt qu'un run **sans verdict** nomme encore et **dit** ce qu'elle purge.
  Elle ne dérive pas de `ROLLBACK_DEPTH` : un wheel déposé n'est pas un barreau de l'échelle de retour.
- Écriture **atomique** (`.part` + `os.replace`), tout échec efface le dépôt entier ; deux dépôts dans la même
  seconde → **409**, même contrat que `spawn`.

### La route de MAJ, et un état de run qui SURVIT au daemon (routes neuves, aucun schéma touché)

Poser une mise à jour depuis le produit, sans terminal — et retrouver son verdict de l'autre côté de la
bascule. `GET /api/update/plan` (préflight + description, **idempotent**) · `POST /api/update/apply` ·
`POST /api/update/rollback` (**202** + identifiant du run) · `GET /api/update/runs[/{id}]`.

- **Rien ne vit en mémoire.** Le processus qui répond au `GET` d'après n'est ni celui qui a reçu le `POST`,
  ni même le même binaire. `update.run_state` relit tout du **disque** et tranche cinq états :
  `done`/`failed` (`result.json` écrit) · `unknown` (verdict absent, unité non sondée) · `running` (unité
  transitoire active) · `interrupted` (parti, jamais conclu — l'état que le fire-and-forget d'avant ne savait
  pas dire) · `never_started`. `unknown` est le garde-fou de l'économie de sonde : `GET /runs` n'en dépense
  qu'**une** pour toute la liste, sinon un gestionnaire systemd coincé ferait attendre la page qui sert
  justement à le regarder.
- **`run.json`, l'intention écrite avant l'effet.** Le `mode` ne se dérive de rien (`result.json` ne le porte
  pas et n'existe qu'à la fin) : un run qui n'a jamais démarré garde quand même sa raison d'avoir existé.
- **Le POST exécute, il ne prévisualise pas.** Pas de `dry_run` dans un corps de requête : la
  prévisualisation d'un geste mutant est un `GET` idempotent, comme `git/sync` avant `git/sync/reconcile`.
- **Sixième refus, dans le préflight PARTAGÉ** — un dispatch en cours **bloque** les deux gestes. L'arrêt du
  service tue le worker, le boot suivant le réape `killed`, sa task retombe `todo` et les jetons dépensés
  sont perdus. La CLI arrête le même service : elle en hérite. Les shells du terminal web, eux, sont **dits**
  et ne bloquent pas — un onglet ouvert n'est pas du travail en cours.
- **Surface plus étroite que la CLI** : ni `unit`, ni `systemctl`, ni `service` dans le corps ; `scope` défaut
  `user`. **409** = l'instance refuse (texte intégral du refus) · **503** = `systemd-run` n'a pas enregistré
  l'unité (l'identifiant du run voyage quand même) · **404** = run inconnu ou identifiant hors forme (forme
  **et** confinement du chemin résolu).
- `update.spawn` est extrait de `launch` : le cœur ne parle pas (aucun `print`, aucune exception qui
  s'échappe), la CLI et le daemon en sont deux vues.
- **Deux gestes dans la même seconde ne s'écrasent plus.** L'horodatage d'un run est à la seconde, et le
  handler HTTP est synchrone (donc servi par un fil du pool) : avec `mkdir(exist_ok=True)`, le second
  écrasait le `run.json` du premier — l'intention d'un run **en vol** — avant d'échouer de toute façon sur
  un nom d'unité déjà pris. Il refuse maintenant (**409**), et rien de l'autre n'est touché.
- **Les corps de requête sont `extra="forbid"`** (**422** sur un champ inconnu) : sur cette route, un champ
  ignoré en silence se lit « honoré » par qui l'a écrit.

### Les deux gardes de l'invariant de retour arrière (aucun changement de schéma, aucun format touché)

L'invariant : *aucune séquence de gestes accessible à l'utilisateur ne peut rendre sa base illisible, ni lui
faire perdre du travail sans qu'on le lui ait dit.* Trois faits en formaient un piège — la base monte en
**forward-only** (aucune down-migration n'existe, et il n'en sera pas écrit), le retour arrière demande **deux
gestes** (l'instantané couvre la donnée, jamais le binaire), et **rien ne vérifiait leur cohérence**.

- **Garde de compatibilité** (`restore.py`, et nulle part ailleurs). Une restauration dont l'instantané porte
  un **schéma de base** que le forgemaster en place ne sait pas lire est refusée **avant la première
  écriture**. La ligne qui rendait la panne possible est nommée : `db/store.migrate()` ne réagit qu'à une base
  **en retard** (`user_version < SCHEMA_VERSION`) — une base trop **neuve** passait en silence, et l'ancien
  binaire travaillait dessus.
- **La comparaison porte sur le schéma, ni sur la version produit ni sur le SHA de build.** Deux versions
  peuvent partager un schéma : refuser sur la version produirait des refus faux, et un SHA n'ordonne rien.
  Conséquence : le schéma se lit **dans le `.db` de l'instantané** (`PRAGMA user_version`), donc le format
  d'instantané **ne change pas** (`SCHEMA` reste `1`) et le garde protège les instantanés **déjà pris**.
- **Le binaire s'interroge par sa constante, pas par un verbe CLI** : `installed_schema` demande
  `SCHEMA_VERSION` au python de `<home>/current`, avec repli sur `cockpit.db.schema`. Un verbe neuf
  n'interrogerait que les binaires *postérieurs* au garde — or le binaire dangereux est l'**ancien**.
- **Trois issues, toutes explicites** : *compatible* · *incompatible* (refus sec, la panne est certaine) ·
  *indéterminable* (lien `current` mort, venv cassé → refus, et le message porte la sortie
  `--allow-unverified-binary`). La porte lève un **doute**, jamais une **certitude** : elle ne couvre pas une
  incompatibilité constatée. Un refus sec sur l'indéterminable bloquerait le secours dans la situation même
  qu'il sert ; un simple avertissement ne tiendrait plus l'invariant.
- **Le retour arrière automatique n'est pas bloqué** : `apply_update` a rebasculé le lien sur le venv qui a
  *pris* l'instantané, donc il lève le doute — mais il ne passe le drapeau que si le `restore.py` **figé dans
  l'instantané** le connaît. Un instantané d'avant ce garde ferait sinon sortir argparse en usage, et ferait
  échouer le retour arrière au pire moment.
- **Verdict d'autorité par projet** (`projects/authority.py`, pure lecture, `GitBackend` injecté). Cinq états
  rendus : `clean_pushed` · `uncommitted` · `unpushed` · `no_remote` · `unreachable`. `update apply` refuse
  **fail-closed** sur du travail **non commité** — `projects_root` n'entre pas dans l'instantané, donc ce
  travail-là ne reviendrait pas. « Non poussé » et « aucun remote » sont **dits**, jamais bloquants : un
  utilisateur sans miroir est un cas *normal* du produit distribué, et refuser dessus lui interdirait toute
  mise à jour. L'exclusion de `projects_root` devient une **constatation vérifiée** au lieu d'une hypothèse.

### Une install fraîche ne réclame plus les tokens du mainteneur — schéma **v19 → v20**
- **Le défaut, mesuré sur une install vierge** (2026-08-04, VM neuve) : `GET /api/onboarding` rendait
  `complete: false` avec **quatre** exigences insatisfiables — un token de **push** vers
  `github.com/Avadis7860/{code-map,docs-map,front-map,forgemaster-catalogs}`. Un inconnu ne peut pas les
  fournir : la bannière ne s'éteignait jamais.
- **La cause tient en une ligne** : `bootstrap` adoptait avec `mirror_remote=source_url`. Or les deux
  colonnes disent des choses différentes — `source_url` est la **provenance** (l'`origin` du clone, ce que
  `toolsync` re-fetch en pull-only), `mirror_remote` la **destination de push**, dont `onboarding.status()`
  déduit qu'un token est *requis*. Le modèle portait déjà la distinction ; une recopie l'a annulée.
- **Correctif** : une adoption pose une **provenance seule**. Qui veut pousser un outil pose son miroir
  explicitement (`PATCH /api/projects/<slug>` → `set_mirror_remote`), ce qui matérialise le remote au
  passage.
- **Migration v20** (données, aucune forme de table ne change) : `mirror_remote → NULL` sur les seules
  lignes `kind='tool'` **et** `mirror_remote = source_url`. Un miroir posé par l'utilisateur porte une
  valeur différente de la provenance → **épargné**, sa bannière continue d'avoir raison. Idempotente.
  Rien à défaire côté git : sur le chemin d'adoption le remote `mirror` n'était **jamais** posé (cette
  pose vit dans la branche SEED de `create_project`) — la colonne ne matérialisait rien, elle n'était que
  l'exigence.
- **La bannière n'est pas neutralisée** : elle reste juste pour un projet que l'instance pousse vraiment.

### La surface publique s'adresse à un inconnu — README + CONTRIBUTING en US, et un chemin perso retiré
- **`README.md` réécrit en anglais**, pas traduit : un lecteur extérieur a besoin de savoir ce que l'outil
  **n'est pas** (pas un SaaS, pas un modèle, pas un CI, pas un gestionnaire d'infra, pas une forge hébergée,
  pas autonome) autant que ce qu'il est. La section vient des **frontières délibérées** déjà écrites dans
  `docs/architecture.md` — elle les expose, elle ne les invente pas.
- **Le « Statut : privé » disparaît.** Il devenait faux à la seconde de la bascule de visibilité.
- **La liste des dépôts du framework ne nomme plus `Vault-V1` ni `mcp-catalogs-data`** : ces deux-là ne
  seront jamais publics, donc les lister annonçait notre structure privée et posait deux liens morts.
- **`CONTRIBUTING.md` en US** — même politique (issues bienvenues, PR de code sur CLA préalable), même mots.
- **Le runner Playwright n'a plus de chemin ABSOLU par défaut.** `web/tools/ui_shot.py` et
  `scripts/e2e_runtime.py` le résolvent par `$FORGEMASTER_UI_RUNNER`, sinon par l'emplacement conventionnel
  **`web/tools/render_check.js`** (gitignoré : un slot à remplir par un fichier ou un symlink). L'ancien
  défaut nommait un répertoire personnel et un dépôt privé — il ne pouvait résoudre que chez son auteur, et
  l'annonçait à tout lecteur. Le contrat d'E/S du runner est désormais écrit en tête de `ui_shot.py`, pour
  que quelqu'un d'autre puisse en fournir un.

### Renommage produit — `cockpit` → `forgemaster`
- **Le nom entier change** : distribution, package d'import, commande, unité systemd (`forgemaster.service`),
  les 14 variables `FORGEMASTER_*`, l'état local (`~/.forgemaster/forgemaster.db`) et le répertoire de
  contrat in-repo des projets semés (**`.cockpit/` → `.forgemaster/`**, cf. `docs/schema-contract.md §2`).
- **AUCUN alias de compatibilité, et c'est assumé.** L'assise installée était mesurée nulle au moment de la
  bascule. Un alias n'aurait ménagé personne et aurait laissé un chemin mort à retirer plus tard.
- **Motif** : `cockpit-project.org` (sponsorisé Red Hat, LGPL-2.1+) est une console web d'administration de
  serveurs Linux — **notre catégorie exacte**, pas une homonymie lointaine. Le nom est aussi pris sur PyPI.
  `forgemaster` est déjà notre mot : la forge est l'axe 2 du produit, et le couple *master → workers* décrit
  littéralement ce qu'il fait.
- **`tools` → `toolchain`** (même fenêtre, pour ne casser la CLI qu'une fois) : `forgemaster toolchain
  install|check` (outillage hôte) ne se confond plus avec `forgemaster tool sync` (outil adopté du rail) —
  deux verbes à une lettre d'écart pour deux objets sans rapport.
- **Ce qui n'a PAS bougé** : les 4 cartes (`code-map`, `docs-map`, `front-map`, `task-map`) gardent leurs
  noms descriptifs, et les identifiants qui **pointent** (ids de missions, noms d'épics, slugs de décisions)
  sont préservés — 45 occurrences sur 32 clés distinctes, épargnées et comptées.

### Le serveur co-installé porte désormais son offre de source AGPL §13
- **`mcp.local.SERVER_REF` bumpé** `0d481d3` → `e216b12` : `GET /version` du serveur MCP porte un bloc
  `source` désignant le code de la version **réellement servie** (dépôt, révision, lien profond, licence).
- **Ce n'est pas un bump de confort.** C'est le co-install qui met l'utilisateur en position d'exposer un
  service AGPL sur un réseau — donc c'est lui que le §13 vise. Laisser la ref en arrière ferait tourner,
  chez chaque instance co-installée, un serveur incapable d'honorer l'obligation qu'on vient de lui créer.
- Ref épinglée, donc **une entrée de CHANGELOG et une édition** : c'est la règle §3 de la décision
  d'édition, pas une exception.

### All-in-one : une instance peut faire tourner SON serveur MCP, et dit laquelle des deux topologies elle est
- **Schéma API** — `GET /api/version` gagne la clé **`mcp`** `{topology, sha, endpoint, reason}` (aussi visible
  sous `build` dans `GET /api/onboarding`). Pas de bump `SCHEMA_VERSION` : champ additif d'API HTTP, aucune
  migration SQLite déclenchée (cf. politique de versionnage). Contrat écrit dans `docs/schema-contract.md`.
- **Ce que ça répond** : la décision d'édition du 2026-08-02 (§4) déclarait **deux** topologies MCP —
  co-installée et endpoint distant — et exigeait que l'instance dise laquelle elle est. Seule la seconde
  existait, et rien ne la nommait. `topology` ∈ `co-installed` | `remote` | `none` | `unknown`.
- **`cockpit mcp install --data-root <racine>`** (et `provision-ct.sh --with-mcp <racine>`, étape `[8/9]`)
  co-installe `forgemaster-catalogs` sur l'hôte : venv dédié `$COCKPIT_HOME/mcp/venv` au **SHA épinglé** de
  l'édition, secret HS256 **généré**, `EnvironmentFile` en `600`, unité systemd, câblage **loopback**. Aucune
  valeur à saisir ; le secret ne passe par aucun argv.
- **On installe un LECTEUR, pas un corpus.** `--data-root` est **obligatoire et doit exister** — sans lui la
  commande **refuse**, plutôt que de démarrer un serveur qui répondrait `200` sur un corpus vide. Le cockpit
  ne clone aucun corpus : la racine est la donnée de l'opérateur.
- **La topologie est déduite du disque, jamais déclarée.** Pas de clé d'env `…_TOPOLOGY` : elle mentirait au
  premier re-câblage. Deux faits lisibles localement — serveur installé sous `mcp/venv` ? endpoint consommé en
  loopback (aucun DNS résolu, `0.0.0.0` exclu — c'est une adresse de bind) ? — et leur conjonction EST la
  réponse. `sha` n'est rendu que pour `co-installed` ; un serveur distant se **demande** (`GET /version` sous
  JWT), il ne se devine pas.
- **`none` est un état normal**, pas une panne : une install sans corpus n'a rien à interroger.
- **Le piège pip-git-SHA re-appliqué d'emblée** : `forgemaster-catalogs` est figé à `0.1.0` comme les 3 cartes,
  donc `--upgrade` seul sauterait l'install en rendant rc 0. Deux passes, la seconde en
  `--force-reinstall --no-deps`, verrouillées par un test.
- **`tools.map_provenance` → `tools.dist_provenance`**, et `tools._run_step` → `tools.run_step` : elles ne
  lisaient déjà rien de spécifique aux cartes, et le co-install les réutilise telles quelles plutôt que
  d'entretenir une seconde lecture du même format PEP 610.
- **Renumérotation** des étapes de `provision-ct.sh` : `[n/8]` → `[n/9]`. `--help` n'imprime plus un
  intervalle de lignes en dur (il fuyait déjà le `set -euo pipefail` de la ligne 30).

### Le serveur MCP frère s'appelle désormais `forgemaster-catalogs`
- **Ce qui bouge ici** : le dépôt adopté par le rail (`deploy/bootstrap.yaml` — `slug` + `source_url`,
  verrouillé par `test_bootstrap`), et le nom du serveur partout où il est **visible par l'utilisateur**
  (`McpCorpus`, wizard `/setup`, réglages `/settings`) ou cité en doc. Rendu vérifié à l'écran : le bloc
  « Corpus capital (MCP) » ne casse pas — le nom, 7 caractères plus long, tient sa ligne.
- **Ce qui NE bouge PAS** : `aud=vault-catalogs` / `iss=vault-mcp` du JWT. Le cockpit **reproduit** le
  contrat validé par le serveur ; le renommer ici seul serait la demi-migration que `provision/mcp.py`
  refuse explicitement. Le retrait du verbatim historique reste coordonné serveur-d'abord (backlog vault
  `mcp-catalogs-naming-coherence` — un **id**, qui garde son nom).
- **Ni `mcp-catalogs-data`** : le dépôt de DONNÉES ne change pas de nom (`launch_templates/*.yaml`,
  graines de bundles). Il est un **préfixe** du nom renommé — une substitution naïve l'aurait emporté.
- **Effet de bord assumé** : le `slug` adopté changeant, une instance qui avait déjà adopté
  `mcp-catalogs` en verra un **nouveau** (l'ancien clone reste, inutilisé). Aucune instance distribuée
  n'existe à ce jour ; pas de migration écrite pour un parc vide.

### `cockpit tools install` ne remettait RIEN à niveau et rendait « 🟢 » — le piège pip-git-SHA
- **Trouvé par la sonde livrée juste avant** : après un `tools install` répondant vert sur ses 4 étapes,
  `cockpit tools check` est resté **rouge** et `codemap --help` n'avait pas récupéré son verbe. La garde a
  attrapé un défaut que rien d'autre ne voyait — un `rc 0` sur un no-op.
- **Cause, mesurée** (VM 9311, 2026-08-03) : `pip install --upgrade git+<url>@main` clone, **résout `main` au
  bon commit** (`d04c2776d8c8`), prépare les métadonnées… puis **saute l'install** parce que la version
  installée est identique. Les 3 cartes sont figées à `0.1.0` : la version ne discrimine **jamais**. pip
  faisait le travail réseau, apprenait la bonne réponse, et la jetait — `direct_url.json` restait sur le
  commit périmé. C'est le même piège que le cutover de `forgemaster-catalogs`, sur un autre chemin.
- **Fix** : `install_plan` pose désormais les cartes en **deux passes** — `--upgrade` (qui résout les
  **dépendances**, correcte sur une install fraîche) puis `--force-reinstall --no-deps` (qui force le **code**
  des cartes à la réf demandée sans retoucher aux deps). L'ordre est load-bearing : `--no-deps` seul
  n'installerait aucune dépendance sur une machine vierge.
- Trois gardes verrouillent la parade (vues rouges d'abord) : étape supprimée · `--force-reinstall` retiré ·
  `--no-deps` retiré · épinglage placé avant la résolution des deps.

### Une instance sait quelles cartes elle sert, et si elles ont dérivé — **API additive, aucun bump de schéma**
- **Défaut de produit** (prérequis de publication) : les 3 cartes hôte sont tirées **une fois**, au
  provisioning, à `MAP_REF = "main"` — une réf **mobile** — puis plus jamais. Rien ne re-synchronise, et rien
  ne le **disait** : `preflight_tools` teste une *présence* (`shutil.which`), jamais une version, et
  `/api/version` ne parlait que du wheel. Une instance servait donc des cartes vieillissantes à la population
  qui travaille **sans humain devant l'écran** — des adresses `fichier:ligne` fausses, en silence.
  Mesuré le 2026-08-03 sur la VM 9311 : provisionnée à 00:34, les 3 cartes déjà différentes de leur amont à
  04:19. Le figeage ne demande pas des semaines, il commence à la première heure.
- **Aucun tampon n'a été écrit** : pip pose déjà `direct_url.json` (PEP 610) avec le `commit_id` **résolu** à
  l'install. `tools.maps_provenance()` le **lit** — stdlib, **zéro réseau**, ne lève jamais, `sha=null`
  toujours accompagné d'un `reason`. Même mécanisme que la provenance de `forgemaster-catalogs` : un mécanisme, deux
  consommateurs. (Le cockpit tamponne son `_build.json` parce qu'il *construit un wheel* ; ce n'est pas le
  cas des cartes.)
- `GET /api/version` gagne `maps: [{name, sha, requested_ref, source, reason}]` — **champ additif**, et
  **étiqueté à part** du wheel : les deux moitiés bougent indépendamment (cartes à `tools install`, wheel à la
  réinjection), un verdict unique serait faux dès que l'une bouge seule.
- `cockpit tools check` (neuf) compare les cartes servies à leur amont par `git ls-remote` **anonyme**, et
  **rapporte sans muter**. Trois issues distinctes — exit **0** à jour · **1** au moins une diffère · **2**
  fraîcheur **non vérifiée** : « je n'ai pas pu vérifier » n'est ni « à jour » ni « périmé ». Ne dit jamais
  « en retard de N commits » (`ls-remote` ne rend que des réfs ; un compte inventé retirerait le doute qui
  doit déclencher la vérification).
- **Délibérément PAS dans `preflight_tools`**, et **pas** pour un motif hors-ligne (les 3 appelants du
  preflight spawnent `claude`, donc exigent l'API Anthropic — un dispatch hors ligne n'existe pas ici) :
  la question est **que ferait le dispatch de la réponse ?** `MAP_REF` est mobile et la dérive commence en
  quelques heures. **Refuser** bloquerait presque tous les spawns passé la demi-journée — un check qui
  s'allume sur ce qui est normal par construction. **Avertir** donnerait au worker un fait sur lequel il ne
  peut rien (il ne réinstalle pas son outillage en vol). La comparaison va donc là où quelqu'un peut agir
  dessus. La remise à niveau reste `cockpit tools install` (idempotent) — une re-sync automatique
  remplacerait un binaire sous un worker en vol.

### Endpoint MCP — **plus aucun défaut en dur** (`endpoint` devient nullable) — **API, aucun bump de schéma**
- **Défaut de produit** (prérequis de publication) : `provision/mcp.py` portait `_DEFAULT_MCP_ENDPOINT =
  "http://192.168.0.153:8080/mcp"` — **notre** CT. Toute install sans câblage explicite tapait donc chez nous
  au premier dispatch. Invisible tant que nous étions les seuls à l'exécuter ; indéfendable dès qu'un tiers
  installe. Le défaut est **supprimé**, pas déplacé dans une config : un cockpit n'a pas d'instance
  `forgemaster-catalogs` par défaut, et l'absence se **dit** au lieu de se deviner.
- `current_endpoint()` rend désormais `str | None` (`COCKPIT_MCP_ENDPOINT` vide ⇒ non configuré). Chaîne de
  dégradation, honnête de bout en bout : `inject_mcp_config` → **no-op** (aucun `.mcp.json`, jamais de crash de
  dispatch) · `blueprint_resolver` / `CapitalBrowser` → `None` **sans appel réseau** · `render_mcp_config` →
  `ValueError` (on n'écrit pas une config sans URL) · `wire()` → `MCPWireError` **avant tout effet de bord**
  (rien dans le coffre, rien dans `cockpit.env`) : câbler un secret sans dire vers quoi est un demi-câblage.
- `check_lifecycle` (doctor) gagne l'état **« ref posée, aucune cible »** → `healthy=False`. Sans lui, le
  retrait du défaut aurait créé un trou silencieux : dispatch sans MCP, et pas un mot.
- **API** — `endpoint` devient **nullable** sur `GET /api/capital/status`, le champ `mcp` de
  `GET /api/onboarding`, et la réponse de `POST /api/onboarding/mcp`. Côté UI, quand aucune cible n'est
  configurée, l'endpoint devient **obligatoire** dans le formulaire de câblage (le serveur refuserait en 400)
  et le placeholder ne propose plus une adresse à nous.

### Tier-0 natif — contrat d'applicabilité **UNIVERSEL** (groupe `declared`) — **aucun bump de schéma**
- **Défaut** (P0, structurel pour tout utilisateur distribué) : l'applicabilité du Tier-0 natif dérivait d'une
  **allowlist de 3 motifs** (`web/`, `*.py`, suffixes node). Un diff qu'aucun ne touchait sortait en `[]` →
  `native_status.applicable = false` → `compose_merge_decision` l'ignorait. **Un diff 100 % Go / Rust / Ruby /
  shell mergeait donc sans qu'aucun étage déterministe ne se soit allumé, et sans un mot.** Le Tier-0 est le
  **seul veto non-overridable** de la pile (Tier-1 est levable par override, Tier-1.5 dépend d'une UI, le juge
  esthétique est advisory) : son extinction silencieuse est pire qu'un gate absent — c'est un gate qui *prétend*.
  Invisible chez nous (stack Python + TS), fatal chez qui distribue.
- **Renversement POSITIF** : la charge de la preuve porte désormais sur l'**absence de source**, jamais sur la
  reconnaissance du langage. Les 3 routes connues sont inchangées ; tout **résidu** de source qu'aucune ne
  couvre déclenche un 4ᵉ groupe **`declared`**. `N/A` est réservé aux diffs **sans source** — prose ⊕ verrous de
  dépendances ⊕ assets binaires (`is_tier0_source`, denylist Tier-0 distincte de `DOC_SUFFIXES` qui sert au
  Tier-1 : une *review* veut voir un `.png` bouger, une *toolchain* n'a rien à en dire). Doctrine :
  **inférer, c'est prétendre ; déclarer, c'est répondre.**
- **Agnosticité par délégation, zéro hardcode de stack** : le projet déclare sa toolchain dans une table
  `[bundle.gate]` de son `.cockpit/bundle.toml` (`steps = [{ name, argv, cwd? }]`). Le lecteur
  (`toolchain._declared_steps`, calqué sur `provision/facet.resolve_facet_model`) ne valide que la **forme** de
  l'`argv`, jamais son contenu. **Déclaration malformée = déclaration absente** (fail-CLOSED) : manifeste
  illisible, table absente, liste vide, `argv` non exploitable, `cwd` absolu ⇒ `None` → step rouge synthétique.
  Une déclaration cassée ne dégrade **jamais** vers le vert, sinon un TOML mal tapé rouvrirait le trou.
- **Pureté préservée (invariant V4)** : `applicable_triggers` reste **diff-only** — le `GET /api/gate` est
  poll-é et n'a que le diff sous la main. L'**applicabilité** (pure) et la **montabilité** (`_steps_for`, qui
  reçoit déjà le worktree) sont séparées ; le chemin fail-closed « déclenché mais non couvert → step rouge »
  existait déjà : **zéro plomberie neuve**. Le message d'absence dit **quoi faire** (bloc TOML exact à écrire),
  pas seulement ce qui manque — c'est le seul recours de l'utilisateur dont la stack n'a aucune route connue.
- **Coût nul pour un projet semé** : les 5 types portent leur propre `[bundle.gate]` (un overlay surcharge
  `bundle.toml` en **whole-file** — le bloc de la base n'est PAS hérité, piège déjà connu de `[bundle.mcp]`),
  qui **duplique** leur route ; la dédup `(name, cmd, cwd)` de `run_toolchain` l'absorbe intégralement. `mypy`
  est volontairement **absent** des déclarations Python : sa cible est layout-dépendante (la route calcule
  `src` si le dossier existe, sinon `.`) — la déclarer statiquement faisait diverger les deux dès qu'un projet
  grandissait un `src/` → mypy joué deux fois, le second en duplicate-module → **faux rouge sur un projet
  normal** (défaut mesuré au balayage de non-régression, corrigé, gardé par test).
- **Versionnage** : aucun bump. `docs/schema-contract.md:201` classe `bundle.toml` **hors contrat figé**,
  aucune colonne SQLite ne bouge, `native_status` garde ses clés (`cmd` gagne une *valeur*, pas un champ) ; la
  politique `:376+` fait du bump le **déclencheur de migration** — sans migration, pas de bump. Le contrat qui
  change réellement est **la spec** (`docs/specs/tier0-native-toolchain-gate.md` §Amendement 2026-07-31, qui
  amende les règles verrouillées 2 et 3 **par leur propre clause d'échappement** : « pas de config déclarative
  *tant qu'un 2ᵉ projet ne diverge pas* » — l'utilisateur distribué **est** ce 2ᵉ projet).
- **Migration des projets déjà semés** : un projet créé avant ce changement n'a pas de `[bundle.gate]` → un
  diff de **résidu seul** y passera rouge, une fois, avec le bloc TOML à copier dans le message. C'est le
  chemin de migration voulu (auto-réparable, une fois par projet), pas un reseed forcé.
- **Tests** : `tests/test_gate.py` — le test du trou (`["main.go"] → ["declared"]`, langages inconnus, cas
  mixtes), `N/A` réservé aux diffs sans source, rouge-sans-déclaration puis vert-déclaré, 6 corps malformés
  (« malformée = absente, jamais verte »), dédup inter-groupes. `tests/test_provision.py` — les 5 types semés
  montent leur résidu (garde du piège whole-file) et `declared` leur coûte **0 step**.
  `tests/test_orchestrator.py` — garde de **boucle** : une feature dont le projet ne déclare rien est drainée
  mais **jamais `merge_ready`**, blocker à l'appui.

### Canal content — CLI `cockpit upload` + route `POST /api/projects/{slug}/upload` (Phase 2) — **API : +1 route**
- **Contexte** : le canal d'injection d'asset (cf. `docs/specs/project-content-upload.md`) exposé sur la spine.
  Un opérateur dépose un fichier (charte, schéma, image, doc) dans un projet ; le worker/l'IA d'interview le lit
  sous `docs/design/<dest>/`. Parité stricte **CLI ↔ route** : les deux délèguent au **même** cœur
  `content.ingest.ingest_upload` (Phase 1) — livraison worktree-aware (live si worktree actif, sinon voie forge
  `content-<x>` mergée sur **GO humain**), jamais de commit direct sur `dev`.
- **CLI** : `cockpit upload <projet> <chemin> [--dest <slug>] [--feature <f>]` (`cli._h_upload`, import
  fastapi-free). Lit le fichier local, délègue ; borne rejetée / fichier illisible → code 1 (pas d'exception).
- **Route** (`daemon/routes/projects.py`) : `POST /api/projects/{slug}/upload` **multipart** (`file` +
  `dest?`/`feature?` en Form). 1ᵉʳ `UploadFile`/multipart du repo → **dép runtime `python-multipart>=0.0.9`**
  ajoutée (`pyproject.toml`). Mapping HTTP des exceptions typées via deux handlers globaux (`daemon/app.py`) :
  `UploadTooLarge`→**413**, `UploadTypeRejected`→**415** (résolus par MRO au-dessus du `ValueError`→400) ;
  `UploadRejected` nu (secret/traversal/nom)→**400** ; projet/feature absents→**404**.
- **Contrat** : `docs/schema-contract.md` (section API `projects`) mis à jour — nouvelle route documentée. Pas
  de bump SQLite/roadmap (aucun schéma de données touché ; ajout d'une route HTTP additive).
- **Tests** : `tests/test_content.py` étendu — parité route (201 forge/live/noop, 413/415/400/404, champ Form
  `dest`) + parité CLI (délégation même cœur, `--dest`, fichier illisible/type rejeté → code 1).

### Signal du gate honnête (fiabilité qualifiée + findings consultatifs surfacés) — **bump SQLite v18 → v19**
- **Problème** (relevé au drain vitrine `avagency`) : le dashboard montrait des signaux **faussement verts**. (1)
  La **fiabilité** affichait `100%` alors qu'aucune issue n'était marquée ET qu'une feature était 🔴-bloquée — le
  taux `(n − n_adverse)/n` vaut 100 % dès que rien n'est marqué mauvais, et une feature bloquée n'entre jamais
  dans `merge_outcomes` (invisible). (2) Les findings **🟡/🟣 consultatifs** du reviewer Tier-1 ne remontaient
  nulle part durablement — un défaut jaune mourait dans la preview éphémère du gate.
- **Fiabilité qualifiée** (`db/merge_outcomes`, aucun bump — lecture) : `_tally` expose `provisional`
  (`n_adverse=0` → le taux est une borne optimiste, pas « santé verte prouvée »), `n_marked`, `n_held` (= non
  jugés) ; `reliability` **tempère** par les blockers ouverts via un read des `alerts` (`kind='gate_red'`) →
  `n_blocked_open` + `blocked_features`, SANS les injecter dans `taux`. CLI `reliability` en miroir.
- **Findings consultatifs surfacés** (**v19**) : le `CHECK` d'`alerts.kind` gagne `review_findings` (rebuild de
  table `_migrate_v19_...`, gardé + idempotent ; **recrée `ux_alerts_open`/`ix_alerts_status` dans la migration**
  — divergence correcte du patron v15/v16 car l'index unique est la cible de l'`ON CONFLICT` d'`emit_alert`).
  `gate/review.write_verdict` émet une alerte `review_findings` (`severity='info'`) quand un verdict porte des
  🟡/🟣, la résout sur ré-review propre ; n'émet pas sur 🔴 (déjà couvert par `gate_red` au merge).
- **Front** : `ReliabilityStrip` rend le taux **qualifié** (badge « provisoire », bandeau features bloquées, jamais
  vert quand non prouvé) ; `GatePanel` gagne un panneau dépliable listant les corps des findings consultatifs (via
  `GET …/verdicts`) ; `NotificationCenter`/`AlertSchema` (zod) gagnent le kind `review_findings` (lockstep). Change
  visuel → `.cockpit/verify-markers.json` (Tier-1.5).
- Schéma SQLite → additif, versionné (`docs/schema-contract.md`) ; front zod/`KIND_LABEL` en lockstep du CHECK.

### Terminal/WS/UI — frame `exit` : l'end-state d'interview distingue crash-au-démarrage de incomplète — pas de bump
- L'état de fin d'interview (`InterviewEndState`) dérivait la fin de la **seule roadmap** (`productive =
  stage.current >= 2`, sinon « Interview incomplète » en `warn`). Un **crash au démarrage** — le PTY lance
  `bash -lc 'exec cockpit interview <p>'` ; `cockpit` hors du PATH de login → `exec: cockpit: not found`, EOF
  immédiat, exit 127 — tombait dans le **même** cadrage *métier* « pas de roadmap », alors que le log résiduel
  montrait un échec **technique** (dissonance de scent relevée par la critique UX du 2026-07-24). L'unique recours
  « Reprendre » rejouerait le même échec.
- **Signal serveur (net-neuf)** : `serve_project_terminal` émet, **sur EOF réel uniquement** (pas sur
  déconnexion/replaced — aucun spectateur à qui rendre un verdict), une frame de contrôle finale
  `{"t":"exit","code":int|null,"reason":"clean|failed_start|crash"}` **avant** le close. `reason` dérivée du code
  de sortie du shell par la fonction pure `terminal.pty.classify_exit` (0→clean ; 126/127→failed_start ;
  autre/None→crash) ; `PtySession.exit_code` expose le `returncode` réappé au teardown.
- **Front** : `parseSessionFrame`→`parseControlFrame` (union discriminée `session`|`exit`|`unknown`). Un `t:`
  **inconnu** est désormais **ignoré**, jamais réécrit brut dans le terminal (durcissement : évite qu'un futur
  `t:` s'affiche en clair). `InterviewEndState` reçoit `exitReason` et rend, pour `failed_start|crash`, une branche
  **`Alert tone="danger"`** *prioritaire* (avant toute lecture roadmap — sur un `failed_start` il n'y a PAS de
  roadmap), qui renvoie au log de session et n'offre qu'un « Reprendre quand même » dé-emphasé.
- Contrat WS (frame de contrôle texte) → additif, versionné par CHANGELOG (`docs/schema-contract.md` §4,
  terminal) ; **pas** de bump `SCHEMA_VERSION` (SQLite-only). Preuve : `classify_exit` + émission EOF-only
  (`tests/test_terminal_pty.py`, WS/session factices) ; `parseControlFrame` + branche danger prioritaire
  (`TerminalPane.test.tsx`, `InterviewEndState.test.tsx`).

### Roadmap/CLI — édition validée du DAG : `roadmap set-deps` / `task set-deps` — pas de bump
- Le DAG de roadmap n'avait **aucune surface d'édition** : `features.depends_on` (v10) et `tasks.depends_on` ne
  se mutaient qu'à la **création** (`--depends-on` d'`add-feature` / `task add`, INSERT-only). Une dépendance
  découverte **après coup** — cas *par design*, la critique de complétude de `roadmap-decompose` révèle un
  prérequis manquant — forçait le worker à ouvrir `cockpit.db` en **raw-SQL** (`UPDATE features SET depends_on…
  WHERE slug=…` → near-miss `ambiguous column name: slug`), contournant l'autorité de validation.
- Ajout de deux verbes CLI + leurs helpers data-layer : `roadmap set-deps <projet> <feature> --depends-on …`
  (`model.set_feature_deps`) et `task set-deps <projet>/<feature> <task> --depends-on …` (`model.set_task_deps`).
  Sémantique **remplace** (cohérent avec `set`). Write-validate-rollback en une transaction : écriture **scopée
  par id/feature_id** (tue le footgun `ambiguous column name`), puis **réutilise l'unique autorité**
  `resolver.classify_features` / `classify` (dangling→`ERROR`, cycle/self-dep→`CYCLE`) sur l'écriture
  non-commitée → refus propre (`ValueError`, exit 1) + `rollback` si l'arête casse le DAG ; `commit` sinon.
- La skill semée `roadmap-decompose` pointe désormais ces verbes (et interdit explicitement l'édition raw-SQL
  de `cockpit.db`) ; point 3 corrigé (les deps inter-feature **sont** un champ `depends_on` depuis v10, pas
  seulement l'ordre de merge). Additif : verbes CLI nouveaux, colonnes existantes, aucun schéma / API HTTP /
  `roadmap.yaml` touché → **pas de bump `SCHEMA_VERSION`**.

### API/capital — erreur serveur MCP honnête : 502 (détail réel) ≠ 503 (indispo) — pas de bump
- Les routes `GET /api/capital/*` distinguent désormais **3 états** au lieu de 2. Avant : toute défaillance du
  parcours capital (y compris une **erreur d'outil serveur** — ref cassée, silo en défaut alors que le MCP
  **répond**) était repeinte en **503** « MCP non câblé ou injoignable » — un **mislabel** (le MCP EST joignable).
  Après : (a) non câblé / (b) injoignable → **503** générique ; (c) le MCP **répond mais échoue** sur la ressource
  → **502** + le **détail serveur réel** (`ApiError.detail`, rendu tel quel par le front, zéro changement `web/`).
- Cœur : `CapitalBrowser._invoke` (`mcp/client.py`) discrimine dans son `except` l'erreur d'outil serveur
  (`fastmcp.ToolError`/`McpError`, propagée en `CapitalServerError` typée) du transport (`RuntimeError`/réseau →
  `None` honnête inchangé) ; `routes/capital._served` mappe `CapitalServerError`→502, `None`→503. `blueprint_resolver`
  **intouché** (garde son `None` total pour taskmap). Additif au contrat (`docs/schema-contract.md` §3, entrée
  `capital`) → **pas de bump `SCHEMA_VERSION`** (règle API HTTP : CHANGELOG seul).

### Git/UI — palette « rechercher dans le code » (grep, parité GitHub) — pas de bump
- Un bouton « Rechercher » (en-tête de l'explorateur, à côté de « Go to file ») ouvre une palette `Dialog` :
  la requête, **débouncée** (≈200 ms), interroge `GET …/git/search` (livré) ; chaque correspondance
  `chemin:ligne` + extrait est un `<Button>` (R1) qui **ouvre le fichier à la ligne** (deep-link `line`,
  surbrillance + scroll). `truncated`/`count` du serveur affichés (cap SIGNALÉ, jamais un « tout » trompeur).
  Hook `useGitSearch` paresseux (`enabled` = palette ouverte + requête non vide). Front-only (consomme
  l'endpoint E.1) → **pas de bump ni de changement d'API**. Schéma `GitSearchSchema` (calque `GitPaths`).

### Git/UI — ouvrir un fichier à une ligne (surbrillance + scroll, permalink épinglé) — pas de bump
- La vue git accepte un paramètre d'URL `line` (`?file=…&line=N`) : la ligne ciblée est **surlignée** et
  **défilée au centre** (deep-link depuis la recherche de code, à venir, ou un permalink). Le bouton
  « Permalink » épingle désormais aussi la ligne. Front-only (état d'URL + rendu) → **pas de bump ni de
  changement d'API**. Ancré dans les DEUX rendus par défaut (code coloré `HighlightedCode` + texte nu) ;
  un `.md` ciblé par une ligne retombe sur le rendu source (une ligne n'a de sens que dans la source). Toute
  navigation autre qu'un ciblage explicite efface la surbrillance (pas de ligne fantôme). Surbrillance via
  token `@theme` (`bg-accent-500/15`), scroll via `useScrollToLine` (no-op propre sous jsdom).

### Git/API — endpoint `search` (recherche de code / grep, parité GitHub) — pas de bump
- `GET /api/projects/{p}/git/search?ref=&q=` rend les correspondances plein-texte d'une réf
  (`{project, ref, q, results:[{path, line, text}], truncated, count}`). **Nouvelle route** additive → **pas de
  bump `SCHEMA_VERSION`** (une route HTTP neuve n'a pas de déclencheur de migration, cf. §Politique de versionnage).
- Primitive read-only `InternalGit.search` (`git internal-first`, `git grep -z -n -I -F -i`) : **fixed-string,
  insensible à la casse, binaires exclus**. **Cap signalé** : `truncated=true` au-delà de `_MAX_GREP_RESULTS`
  (500), `count` = total avant cap — jamais de cap silencieux (invariant). `q` vide → `results=[]` (pas de
  match-tout). Seule primitive à invoquer `_git` (pas `_checked`) : `git grep` sort en **code 1 quand il n'y a
  aucun match** (vide légitime, pas une erreur) ; code ≥2 (réf introuvable) → 404.

### Git/API — endpoint `blame` + gouttière ligne-à-ligne (parité GitHub) — pas de bump
- `GET /api/projects/{p}/git/blame?ref=&path=` rend le blame ligne-à-ligne d'un fichier (`{project, ref, path,
  lines:[{sha, author, date, summary}]}`, une entrée par ligne). **Nouvelle route** additive → **pas de bump
  `SCHEMA_VERSION`** (une route HTTP neuve n'a pas de déclencheur de migration, cf. §Politique de versionnage).
- Primitive read-only `InternalGit.blame` (`git internal-first`, `git blame --line-porcelain`). Gardes calquées
  sur `read_blob` : non-blob/binaire → 404, au-delà de 10 Mo → **413 signalé** (blame d'un binaire/gros fichier
  = non-sens, refusé plutôt que bruit).
- Côté front : toggle « Blame » dans l'en-tête fichier → gouttière (sha court · âge) insérée dans les DEUX
  grilles de ligne (texte nu ET code coloré), `sha`/âge affichés une fois par **run** de même commit (collapse
  façon GitHub), auteur+résumé en infobulle.

### Git/API — endpoint `paths` + palette « go to file » (parité GitHub) — pas de bump
- `GET /api/projects/{p}/git/paths?ref=` rend la liste **plate récursive** de tous les fichiers d'une réf
  (`{project, ref, paths, truncated}`), servant la palette « go to file » (filtrage fuzzy client-side). **Cap
  signalé** : `truncated=true` au-delà de `_MAX_TREE_PATHS` (10 000) — jamais de cap silencieux (invariant).
  **Nouvelle route** additive → **pas de bump `SCHEMA_VERSION`** (une route HTTP neuve n'a pas de déclencheur
  de migration, cf. §Politique de versionnage).
- Primitive read-only `InternalGit.list_paths` (`git internal-first`, `ls-tree -r --name-only`). Côté front :
  palette `Dialog` + `Input` + fuzzy subsequence client-side (aucune lib, aucune dép) ouverte par un bouton
  « Go to file » ; chaque résultat écrit l'URL du fichier (deep-link).

### Git/API — `tags` dans la vue git (sélecteur de réf branches + tags, parité GitHub) — pas de bump
- `GET /api/projects/{p}/git` expose désormais `tags: [{name, sha, subject}]` (même forme que `branches`,
  triés par date de création décroissante ; `subject` = message du tag annoté, ou sujet du commit pointé pour
  un tag léger) à côté de `branches`. Champ **additif, rétro-compatible** → **pas de bump `SCHEMA_VERSION`**
  (une extension de payload HTTP n'a pas de déclencheur de migration, cf. §Politique de versionnage).
- Primitive read-only `InternalGit.tags` (`git internal-first`, `for-each-ref refs/tags`) — calque exact de
  `branches` sur les tags ; le sélecteur de réf du repo-browser unifie branches et tags (deux `<optgroup>`).

### Git/API — endpoints `raw` + `download` d'octets bruts (bouton Raw/Download, parité fichier GitHub) — pas de bump
- `GET /api/projects/{p}/git/raw?ref=&path=` et `.../git/download?ref=&path=` servent les **octets bruts** d'un
  fichier (binaire ET texte tels quels), comblant le trou de `git/blob` (qui blanchit binaire/too_large,
  `content=""`). `raw` = inline pour « voir le brut » ; `download` = pièce jointe pour enregistrer. Deux
  **nouvelles routes** additives (aucune existante changée) → **pas de bump `SCHEMA_VERSION`** (une route HTTP
  neuve n'a pas de déclencheur de migration, cf. §Politique de versionnage).
- **Sécurité** (le daemon sert l'app ET les octets, même origine) : `raw` coerce le Content-Type deviné en
  `text/plain; charset=utf-8` pour tout type actif (text/*, html, svg, inconnu) — seuls png/jpeg/gif/webp/pdf
  gardent leur type — + `X-Content-Type-Options: nosniff` (aucun HTML/JS/SVG du dépôt n'exécute dans notre
  origine) ; `download` force `application/octet-stream` + `Content-Disposition: attachment` ; `filename`
  assaini (strip CR/LF/`"`, anti-injection d'en-tête).
- Primitive read-only `InternalGit.read_blob_raw` (`git internal-first`) : mêmes gardes que `read_blob` mais
  **sert** les octets ; au-delà de 10 Mo (`_MAX_BLOB_READ`) lève `BlobTooLargeError` → **413 signalé** (jamais
  un flux tronqué en silence). Introuvable/non-blob → 404.

### Git/API — dernier-commit par entrée + « latest commit » sur `git/tree` (parité liste GitHub) — pas de bump
- `GET /api/projects/{p}/git/tree` enrichit chaque entrée d'un `last_commit:{short, date, subject}|null`
  (dernier commit qui la touche) et coiffe la réponse d'un `latest_commit:{short, author, date, subject,
  count}|null` (dernier commit du dossier courant + nb total de commits). Champs **additifs, rétro-compatibles**
  (`null` honnête si aucun commit) → **pas de bump `SCHEMA_VERSION`** (une extension de payload HTTP n'a pas de
  déclencheur de migration, cf. §Politique de versionnage).
- Deux primitives read-only sur `InternalGit` (`entry_last_commits`, `latest_commit`) — `git internal-first`,
  `log -1`/`rev-list` bornés (chaque `log -1 -- <path>` s'arrête au 1ᵉʳ commit ; aucun cap silencieux).
  `ls_tree` **reste pur** (l'enrichissement vit dans la route, le chemin codemap/archive n'est pas ralenti).

### Gate — preuve Tier-1.5 DEUX-TEMPS (jalon jouable) + bump `feature-verify-v1 → v2` (`gate/verify.py`)
- **Bump du contrat de verdict** `feature-verify-v1 → feature-verify-v2` : la sémantique d'un vert Tier-1.5 se
  **renforce** (présence at-rest → **transition observable après un geste**). `is_fresh` traite désormais un
  verdict d'un `contract_version` antérieur comme **non frais** → re-gate forcé (durcissement du bump).
- **Contrat étendu, rétro-compatible** : `.cockpit/verify-markers.json` accepte un bloc `interaction`
  (`clicks` read-only + `after_markers` + `wait_for_text`) via `read_verify_contract` (nouveau) ;
  `read_declared_markers` reste un wrapper mince (legacy `{"markers":[…]}` inchangé). `build_payload` /
  `verify_target` / `autoverify_feature` / le manifeste stdin threadent les champs d'interaction **ssi non
  vides** (payload legacy identique).
- **Runner deux-temps** (`deploy/runners/render_check.js`, additif) : capture l'état at-rest **avant** les
  gestes, exige les `after_markers` **après** ET **absents** at-rest (`pre_present` non vide ⇒ 🔴) — prouve une
  transition d'état, pas un label statique. Sans `after_markers` : comportement legacy inchangé.
- **Seed** : `browser-game/.claude/facets/frontend/METHOD.md` (point 2) et la skill `first-session-interview`
  exigent le contrat deux-temps pour un jalon jouable (« jouable = observable **après un geste** »).
- Prouvé par un self-check réel (`scripts/verify_interaction_selfcheck.py` + fixtures) : positif 🟢,
  geste-sans-effet 🔴, marqueur-déjà-présent 🔴. E2E jeu réel (VM golden) différé avec `tick-substrate-seed`.

### Gate/API — surface de LECTURE de la trace (findings, toolchain, historique) — pas de bump
- `GET /api/gate` porte désormais **`toolchain`** (Tier-0 natif, miroir de `review`/`verify`) : `evaluate_gate`
  surface le `native_status` **déjà calculé** (aucune double-lecture).
- `GET /api/gate/{p}/{f}/verdicts` : vue de lecture **read-only** — verdict Tier-1 COMPLET (findings : claim,
  evidence, `file:line`) + Tier-0 natif (steps). Findings au niveau **ROUTE**, jamais versés dans
  `review.status` (l'entrée-de-gate consommée par `compose_merge_decision` reste inchangée).
- `GET /api/gate/{p}/{f}/history` : historique des verdicts **par SHA** (`gate_verdicts`) — la trace se lit
  dans l'UI, plus besoin d'ouvrir `~/.cockpit/cockpit.db` à la main.
- **Doc soldée** : 3 routes non documentées (`GET …/dispatch/{p}/{f}/jobs`, `POST …/toolchain`, `POST …/review
  -dispatch`) inscrites ; `WS /ws/dispatch/{job}` re-marqué **porté** (le contrat le disait « différé P5 » à
  tort — il tourne). **Pas de WS neuf** (tail borné à la demande). **Pas de bump** : une route n'a pas de
  déclencheur de migration (contrairement à une colonne).

### Gate — historique des verdicts PAR SHA + capture du GO humain (`gate/history.py`)
- `write_verdict(conn=…)` (review + toolchain) **archive** chaque verdict dans `gate_verdicts` (v11) par SHA :
  un rouge à SHA-A **survit** à un vert à SHA-B (l'ancien `write_text` écrasait → T1 perdu). Le fichier
  `gate/<p>/<f>/*.json` reste le **courant** (fraîcheur en un `read_text`) ; la table est l'**historique**.
- `run_merge` capture le **GO humain** (fait daté, non-rejouable, `gate='merge'`, ancré au SHA) et **borne**
  l'historique de la feature (tail-N) au même point que `delete_branch` — qui sinon orpheline les verdicts.
- La `decision` composée n'est **pas** persistée : `compose_merge_decision` est PURE → rejouable depuis les
  verdicts historisés (persister une sortie déterministe serait de la duplication).
- **Best-effort** : un historique en échec ne fait JAMAIS échouer un merge autorisé ni un verdict écrit.

### Build — `allow-direct-references` pour la dép git privée `task-map`
- `pyproject.toml` : `[tool.hatch.metadata] allow-direct-references = true`. La dép runtime `task-map` est une
  référence directe (`git+https`, repo privé épinglé au SHA) ; sans cet opt-in, `pip wheel .` échoue en
  `metadata-generation-failed`. Fix build-time pur, aucun changement de comportement.

### Schéma DB v11 — trace durable des échecs : `dispatch_jobs.kind`/`.error` + tables `non_runs`/`gate_verdicts` (bump `SCHEMA_VERSION` 10→11)
- **Additif non-breaking** → bump `SCHEMA_VERSION = 11` + migration `ensure_columns` (ALTER idempotent) pour les
  colonnes + `CREATE IF NOT EXISTS` pour les tables neuves. Le bump est le déclencheur de migration (cf. la
  politique de versionnage du contrat) — un ajout de colonne SANS bump ne migrerait jamais une base existante.
- **`dispatch_jobs.kind`** (`TEXT NOT NULL DEFAULT 'task'`) : identité du run. Enum **complet posé dès maintenant**
  (`task|review|toolchain|fix`) — SQLite ne sait pas ALTER un CHECK, l'élargir coûterait un rebuild (précédent
  v8). Les jobs pré-v11 sont tous des runs d'ouvrier → `'task'` est exact pour l'existant. `fix` anticipe le
  dispatch de re-fix sur gate rouge.
- **`dispatch_jobs.error`** (nullable `TEXT`) : raison d'échec **courte** (snippet). Le `raw` complet n'est PAS
  recopié — il vit déjà sur `log_path` (transcript JSONL local, durable).
- **Table `non_runs`** : journal **PUR** des runs jamais lancés (un skip a une `reason`, un `feature_ref`, un
  `kind` attendu et un `created_at` **injecté** — jamais un pid/port/session ; ce n'est pas un job). Pas de FK :
  découplé du cycle de vie de la feature, jamais lu par une garde d'idempotence.
- **Table `gate_verdicts`** : historique des verdicts **par SHA** (`gate ∈ review|toolchain|merge`, `sha`,
  `verdict` JSON, `created_at`) — là où `write_verdict` faisait un `write_text` qui **écrasait** (un rouge à T1
  puis vert à T2 perdait T1). `merge` (le fait-de-merge, GO humain daté) posé dans l'enum dès maintenant.
- **Schéma SEUL** : ce commit pose les contenants. Les puits qui écrivent (`record_finish` gardant `error`,
  dé-squat du reviewer, historisation au `write_verdict`/`run_merge`) sont câblés par les filles suivantes.

### Schéma DB v10 — DAG inter-features : `features.depends_on` (bump `SCHEMA_VERSION` 9→10)
- **Additif non-breaking** → bump `SCHEMA_VERSION = 10` + migration `ensure_columns` (ALTER idempotent). `features`
  gagne `depends_on` (`TEXT NOT NULL DEFAULT '[]'`, liste JSON de slugs de features) : le **DAG INTER-feature**,
  symétrique de `tasks.depends_on` (intra-feature). Défaut littéral `'[]'` (ALTER-safe, aucun `CHECK`) → les
  lignes existantes prennent « aucune dépendance ».
- **Sémantique** : une feature reste non-dispatchable (`orchestrator._discoverable_features`) tant qu'une feature
  prérequise n'est pas `merged` ; prédicat « prérequis satisfait = `merged` » dans `resolver.classify_features`
  (même moteur taskmap que le DAG des tasks, une couche au-dessus).
- **Validation** `check` : `DANGLING_FEATURE_DEP` / `FEATURE_CYCLE` / `DEAD_FEATURE_DEP` (prérequis `cancelled` →
  deadlock surfacé, jamais silencieux).

### Schéma DB v9 — board-native blueprint : `features.blueprint` (bump `SCHEMA_VERSION` 8→9)
- **Additif non-breaking** → bump `SCHEMA_VERSION = 9` + migration `ensure_columns` (ALTER idempotent). `features`
  gagne `blueprint` (nullable `TEXT`, aucun défaut → `NULL` pour l'existant) : la **ref STAMP** (id d'un blueprint
  du capital central) portée par une feature. Patron identique à `facet` (v6).
- **Modèle** `roadmap/model.py` : `add_feature(..., blueprint=None)` le stocke ; `_feature_doc` émet `blueprint`
  dans `roadmap.yaml` **seulement si présent** (contrat rétro-compatible, comme `facet`/`acceptance`).
- **Route** `GET /api/projects/{p}/roadmap` : chaque `features.blueprint` (ref brut) est **résolu en direct** via
  le client MCP runtime (`cockpit.mcp.blueprint_resolver` P2, seam `taskmap.context._blueprint_verdict`) →
  `{blueprint:{id, posture, resolved, reason, …champs fusionnés}}`. **Dégradation honnête** : MCP non câblé /
  coupé / vide → `resolved:false` + raison, jamais inventé. Feature sans blueprint → `blueprint: null`. Contrat
  existant (`tasks`/`state`/`blockers`/`next`) inchangé.
- **HTTP** `POST /api/projects/{p}/features` : `FeatureCreate` gagne `blueprint` (optionnel).

### Schéma DB v8 — registre de bundles registre-driven : `project_type` CHECK RETIRÉ (bump `SCHEMA_VERSION` 7→8)
- **Breaking (schéma SQLite)** → bump `SCHEMA_VERSION = 8` + migration. Le `CHECK (project_type IN (...))` figé
  sur `projects` est **retiré** : les types de projet sont désormais **découverts sur le filesystem**
  (`provision.discover_types` — un type = un dossier `bundles/types/<type>/`), et validés fail-closed par
  `provision.validate_bundle` (manifeste `.cockpit/bundle.toml` : `version`, `project_type`, `facets`,
  `default_facet` + dossiers `.claude/facets/<f>/` de support) dans `registry.create_project`.
- **Migration** `schema._migrate_v8_drop_project_type_check` : rebuild de table **gardé** (no-op si pas de CHECK)
  + **idempotent** ; `foreign_keys` désactivées le temps du rebuild (id préservé, FK enfants intactes).
- **CLI** `cockpit project create --type` : `choices` dérivés de `discover_types()` (plus de liste en dur).
- Nouveaux helpers `provision.{discover_types, read_bundle_manifest, validate_bundle}` + exception `BundleError` ;
  le manifeste `.cockpit/bundle.toml` gagne `version` (provenance amont, consommée par la sélection/stamp en P3).

### Sync miroir SoT↔GitHub — P6 : re-sync des outils adoptés **pull-only ff** (CLI + endpoint) — dégèle le CT
- **Nouvelle sous-commande CLI** `cockpit tool sync <slug>` (`cli.py` + `toolsync.py`) **et** route HTTP
  (non-breaking → note, pas de bump `SCHEMA_VERSION`) : `POST /api/projects/{slug}/tool/sync` → `{project,
  slug, kind, remote, fetched, actions:{<b>:{action, from?, to?, reason?}}, changed, blocked, state,
  index_refreshed}`. Re-fetch un **outil adopté** (`kind=tool`) et avance ses refs suivies (`dev`, `main`)
  quand l'amont GitHub a pris de l'avance — **pull-only, ff-only, jamais de push** (frontière read-only stricte).
- **`InternalGit.sync_tracking`** (nouvelle primitive, **PAS** sur le Protocol figé — op spécifique au clone bare
  d'un outil, contrairement à `reconcile` qui est un concept forge partagé ; ajouter au contrat forcerait un stub
  vide de sens sur `GitHubGit`) : recalcule la divergence puis par branche — `remote_ahead` → **ff** local ;
  `local_ahead` → `local_ahead_skipped` (**jamais de push amont** : un outil read-only n'a aucune autorité
  d'écriture) ; `diverged` → `blocked_diverged` ; garde-fou worktree (`blocked_worktree`) et dégradation honnête
  (`unreachable`/`no_mirror`) hérités.
- **Correctif de fond `ensure_fetch_refspec`** : un `git clone --bare` (voie d'adoption, `clone_sot`) NE POSE
  AUCUN refspec de fetch → `git fetch origin` échouait à peupler `refs/remotes/origin/*` (les 4 outils du rail
  étaient donc **infetchables**, pas seulement figés). `sync_tracking` **auto-répare** le refspec avant de fetcher
  → dégèle aussi les clones déjà adoptés sans les ré-adopter.
- **Fail-close symétrique** : la route/CLI ne cible QUE `kind=tool`. Un **projet** (SoT autoritatif) la **refuse**
  → **409** (`NotAToolError`, intercepté avant le handler `ValueError→400` global) : sa voie de sync est la
  réconciliation gatée `reconcile` (P5), jamais ce pull-only descendant.
- **Fraîcheur Flow gratuite** : l'index code-map est **caché par (SHA, schema)** → un `dev` avancé reconstruit
  au prochain accès Flow (aucune « bust » : le symptôme « Flow périmé » venait du SoT jamais re-fetché). On
  **pré-chauffe** best-effort après un sync qui bouge (`index_refreshed`) — jamais bloquant (code-map absent →
  `False`, honnête).
- **Verrous** : primitive (ff remote_ahead → synced · local_ahead **jamais poussé** · diverged bloqué · garde-fou
  worktree · self-heal refspec bare-clone · dégradation unreachable) ; module (fail-close projet → 409 · absent →
  KeyError · pré-chauffe best-effort · no-change n'appelle pas l'index) ; endpoint TestClient (ff + already_synced
  · 409 projet · 404 absent) ; CLI (codes de sortie).

### Sync miroir SoT↔GitHub — P5 : réconciliation un-clic **ff-only** gatée (route + primitive + UI)
- **Le contrat figé `GitBackend` gagne une méthode** (`git/backend.py`, Protocol runtime-checkable) :
  `reconcile(sot, *, remote, branches, creds_ref=None) -> dict`. Extension de contrat (note dédiée, cf.
  `docs/schema-contract.md`) ; `GitHubGit` la stub (P6, `NotImplementedError`), `InternalGit` l'implémente.
- **Nouvelle route HTTP** (non-breaking → note, pas de bump `SCHEMA_VERSION`) : `POST
  /api/projects/{p}/git/sync/reconcile` → `{project, remote, fetched, actions:{<b>:{action, from?, to?,
  reason?}}, changed, blocked, state}`. **Preview d'ABORD via le `GET .../git/sync` idempotent** (source unique
  = l'`state` par branche) ; ce POST **exécute** — jamais un dry-run POST (doctrine preview-gated-via-GET).
- **Réconciliation ff-only, jamais de merge non-ff ni de `--force`** (`InternalGit.reconcile`) : recalcule la
  divergence (l'état frais fait autorité) puis agit **par branche** : `remote_ahead` → **ff** local vers la
  ref de suivi (`merge_ff`) ; `local_ahead` → **push ff** cette seule branche (refspec explicite, best-effort) ;
  `diverged` → **bloqué**, aucune mutation (spec forge-sot-local : jamais d'auto-merge). La granularité
  par-branche réconcilie chaque branche ff-able même quand le rollup projet est `diverged` (cross-branch).
- **Garde-fou worktree** : on ne ff **jamais** une branche checked-out dans un worktree actif (`branch -f` la
  refuserait) → `blocked_worktree`, aucune mutation. **Dégradation honnête** héritée : `no_mirror`/`unreachable`
  → `fetched=False`, aucune action. Auth transitoire partagée (`_authed_env`, factorisé avec `fetch_remote`).
- **UI (`web/`)** : bouton « ⟳ Réconcilier » (en-tête GitTab, visible sur une divergence réelle) → **panneau
  inline preview** (plan dérivé de l'état : `dev → ff depuis GitHub (+N)`, `main → à jour`) → **Confirmer
  (ff-only)** → résultat par branche → re-fetch du badge. Un état tout-divergé n'offre pas d'exécution : il
  **explique** la résolution manuelle (Alert danger). Hook mutation `useReconcileSync` (invalide la vue git +
  le rail ; le badge sync enabled:false est re-fetché à la main). Tons `reconcileTone` — blocages/échecs
  **jamais verts** (anti-faux-succès).
- **Verrous** : primitive (ff remote_ahead → synced · push local_ahead → miroir reçu · diverged bloqué sans
  mutation · garde-fou worktree · cross-branch réconcilié indépendamment · dégradations) ; endpoint TestClient
  (ff → badge `synced` · vraie divergence bloquée sans mutation · 404) ; vitest (plan/reconcilable/outcome/ton)
  ; **boucle visuelle** (badge « GitHub +2 » + panneau preview `ff depuis GitHub (+2)` / `à jour` rendus).

### Sync miroir SoT↔GitHub — P3 : endpoint `GET /api/projects/{p}/git/sync` (réseau, dédié)
- **Nouvelle route HTTP** (non-breaking → note, pas de bump `SCHEMA_VERSION` — cf. politique de versionnage) :
  `GET /api/projects/{p}/git/sync` expose `InternalGit.remote_divergence` (P2) → `{project, remote, fetched,
  branches:{<b>:{ahead, behind, state}}, state}`. Auth transitoire via le `credential_ref` du projet (résolu
  à l'usage par le `cred_resolver` du store actif, jamais le token).
- **Séparé du `GET .../git` idempotent** : `/git/sync` fait un `git fetch` **réseau, non-idempotent** — il ne
  doit PAS être atteint par le runner de boucle visuelle goto-only ni par du polling ; l'UI (P4) le rattache
  au refresh manuel. `/git` reste la vue read-only bare-safe (branches, log, ahead/behind local-vs-local).
- **Dégradation honnête** propagée telle quelle : `no_mirror` (miroir non câblé), `unreachable` (fetch KO) —
  jamais 0/0 faux-vert. Projet absent → 404 ; SoT illisible → 422. Verrou e2e (TestClient sur SoT réel +
  miroir bare cloné) : `test_git_sync_endpoint_reports_divergence_and_degrades` (no_mirror → synced →
  remote_ahead après avance du miroir → 404).

### Sync miroir SoT↔GitHub — P2 : primitive `remote_divergence` (**bump du contrat `GitBackend`**)
- **Le contrat figé `GitBackend` gagne une méthode** (`git/backend.py`, Protocol runtime-checkable) :
  `remote_divergence(sot, *, remote, branches, creds_ref=None) -> dict`. Bump de contrat au sens de la
  politique de versionnage (`docs/schema-contract.md`) : entrée dédiée, jamais en douce. `GitHubGit` stub le
  reste cohérent (P6, `NotImplementedError`) ; `InternalGit` l'implémente.
- **Écart SoT↔remote par branche + rollup projet, avec dégradation honnête** (`InternalGit.remote_divergence`,
  à côté d'`ahead_behind`) : `fetch` best-effort (auth transitoire réutilisée de P1) puis
  `rev-list --left-right --count <remote-ref>...<local-ref>` par branche → `{ahead, behind, state}` avec
  `state ∈ {synced, local_ahead, remote_ahead, diverged}`. Renvoie
  `{remote, fetched, branches, state}` où `state` est le rollup projet.
- **Jamais de 0/0 faux-vert** (invariant central) : remote non configuré → `no_mirror` ; fetch échoué
  (injoignable/auth) → `unreachable` — dans les deux cas `fetched=False` + `branches={}`. Une branche absente
  d'un côté est un écart réel (`local_ahead`/`remote_ahead`), pas `synced`. Des branches qui tirent en sens
  opposés (l'une local-avance, l'autre remote-avance) roulent en `diverged` (aucun ff unique) — la
  réconciliation reste une op **séparée** (P5), la primitive ne mute pas le SoT.
- Classifieurs **purs testables** extraits (`_branch_sync_state`, `_rollup_sync_state`). Verrous :
  conformité au contrat étendu (`test_internal_git_satisfies_extended_backend_contract`) + matrice d'états
  (synced / remote_ahead / local_ahead / non-ff sur une branche / rollup cross-branch `diverged` /
  branche absente du remote / dégradations `no_mirror` + `unreachable`).

### Sync miroir SoT↔GitHub — P1 : remote fetchable + fetch authentifié (prérequis)
- **Le miroir se matérialise enfin dans git** : `mirror_remote` n'était qu'une string en DB (jamais un
  `git remote`). `registry.create_project` (projet seedé) et `registry.set_mirror_remote` câblent désormais
  le remote `mirror` sur le SoT bare (`add`/`set-url`, retrait sur `None`). Les entités adoptées gardent leur
  `origin` (posé par `clone_sot`) — projets ⟶ `mirror`, outils ⟶ `origin`.
- **Trois primitives `InternalGit`** (hors contrat figé `GitBackend` — le bump est réservé à P2/`remote_divergence`) :
  `set_remote(sot, name, url)` (idempotent), `remove_remote(sot, name)` (best-effort), et
  `fetch_remote(sot, remote, *, creds_ref=None) -> bool` — fetch **best-effort** (False sur remote absent/
  injoignable/auth, ne lève jamais) avec **auth transitoire** : le token est résolu à l'usage via le
  `cred_resolver` injecté et injecté par `credential_env` (jamais persisté, jamais en argv), `GIT_TERMINAL_PROMPT=0`.
- Verrous : `test_set_remote_is_idempotent_add_then_seturl`, `test_remove_remote_is_best_effort`,
  `test_fetch_remote_updates_tracking_and_resolves_creds_transiently`,
  `test_fetch_remote_missing_remote_returns_false_without_raising`.

### Consommateur code-map : cache (SHA, schema_version) + découplage du layout interne (code-map P6)
- **Clé de cache d'index enrichie de `schema_version`** (`codemap/index.py`) : le dossier dérivé passe de
  `home/codemap/<projet>/<sha>` à `home/codemap/<projet>/<sha>/<schema>`. Un upgrade de code-map (nouveau
  contrat : `schema_version` différent, lu via `codemap --schema-version` **sans index**) ouvre un dossier
  neuf → **rebuild automatique**. Ferme le trou « il fallait vider le cache à la main après un déploiement
  de code-map » (l'ancien index n'est jamais servi périmé). Verrou : `test_index_cache_key_includes_schema_version`.
- **Découplage du nom de fichier interne de code-map** : le cache-hit n'inspecte plus `.codemap/calls.manifest.json`
  (organe interne de code-map) mais un **marqueur propre au cockpit** (`.cockpit-index-built`) écrit après un
  build réussi. On dépend du **contrat** de code-map (rc, `--schema-version`, stdout JSON), plus de son
  arborescence — code-map peut réorganiser ses fichiers internes sans casser le cockpit.

### Indicateur d'auth Claude dans l'UI — l'état d'auth devient visible
- **`ClaudeAuthStatus.tsx`** (nouveau) : composant réutilisable qui rend l'état `claude_auth` du GET
  onboarding — `ClaudeAuthBadge` (pastille inline) + `ClaudeAuthBlock` (encart avec l'instruction
  `claude login` quand l'auth manque, miroir exact du `AUTH_HINT` backend). Présence, jamais la valeur.
- **Wizard 1er-démarrage** : nouvelle étape « Compte Claude » (connecté via *source* / non connecté →
  `claude login`). Répond à la surprise « il ne m'a rien demandé » : l'usage n'est plus silencieux.
- **Onglet Dispatch** : pré-flight — sans auth Claude, le bouton « Dispatcher » est **désarmé** et le bandeau
  s'affiche **avant** le clic (au lieu d'un 403 après coup). Le backend reste l'autorité (fail-closed serveur).
- **Schéma** : `OnboardingStatusSchema` gagne `claude_auth` (front zod, miroir du champ backend). +2 vitest.

### Gate d'auth Claude explicite — jamais d'usage silencieux d'un compte hérité
- **`cockpit/auth.py`** (nouveau) : `claude_auth_status(home, env)` détecte de façon **déterministe** si
  l'hôte est authentifié pour spawner des workers `claude` — présence de `$HOME/.claude/.credentials.json`
  ou d'une clé d'env (`ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`), **sans jamais lire la valeur**.
- **Gate dur aux 3 points d'entrée** (`cockpit dispatch`, `cockpit run`, `POST /api/dispatch`) : refus
  **AVANT tout spawn** si non authentifié — CLI exit 2 + message actionnable (`claude login` dans le
  terminal), API `403`. Fini l'héritage silencieux de l'auth du host (perçu comme un contournement d'auth) ;
  un install neuf ne travaille qu'après un `claude login` **explicite** de l'utilisateur, sur **son** compte.
- **Onboarding** : `status()` porte un champ `claude_auth` (axe **orthogonal** à `complete`) pour surfacer
  l'état d'auth au wizard. Le cockpit n'embarque, ne partage ni n'injecte aucun credential : auth **par
  machine** via le CLI officiel. +8 tests (détection, surface, refus CLI/API, gate laisse passer si authed).

### Contenu méthodologique semé (P1 cockpit-typed-bundles, Phase 6)
- **Deux skills de méthodo dans `bundles/base/.claude/skills/`** (⇒ semés dans TOUT projet, tout type) :
  `roadmap-decompose` (intention → features[facette] → tasks[`depends_on` DAG + `acceptance`] : ce qui rend le
  travail dispatchable, séquencé, parallélisable) et `docs-authoring` (rédiger la mémoire `docs/` :
  audience-first, intention avant mécanique, tenue interrogeable par `docsmap`). Ce sont **les sections que le
  projet sait remplir seul** — au-delà de la boucle git (`work-loop`/`quality-gate`). Le CLAUDE.md socle et
  `docs/architecture.md` les référencent (planifier → exécuter → mémoriser). Données pures, gate inchangé.
- +4 tests (`test_provision.py`) : les deux skills présents et non vides dans chaque type + référencés dans le
  CLAUDE.md ; chaque type porte une `architecture.md` non-stub (« Comment ce projet se travaille » présent).

### Orchestrateur parallèle — cœur `run_project` (P1 cockpit-typed-bundles, Phase 4)
- **`dispatch/orchestrator.py`** (nouveau) : `run_project(conn, settings, *, project, max_parallel=2, git,
  runner)` draine la roadmap et **parallélise les features indépendantes prêtes** (feature = worktree =
  mutex ; N features prêtes ⇒ N workers). `ThreadPoolExecutor`, `in_flight` muté **à la soumission** (seul
  le thread principal assigne → zéro double-dispatch), `_dispatch_one` en **connexion par thread** (réutilise
  `dispatch_next` intact). **La boucle possède la transition `done`** (le dispatch mono laisse `in_progress`) :
  sans elle le résolveur ne ferait jamais avancer le DAG. Tolérance à l'échec (feature KO → task revenue
  `todo` + exclue du run) ; terminaison garantie (le travail restant décroît strictement).
- +5 tests (`test_orchestrator.py`, DB fichier + git réel + runner injecté qui mesure la concurrence) :
  drainage DAG intra-feature, parallélisme borné (`peak==max_parallel`), mutex par feature (`feature_peak==1`),
  isolation d'échec, terminaison. **Pas** de merge auto : la boucle ne produit que des commits sur branches.
- **CLI `cockpit run <project> [--max-parallel N]`** (Phase 5) : `orchestrator.cli_dispatch` draine et imprime
  le rapport (dispatchées / ok / échouées / drainé) ; exit 0 si drainée sans échec, 1 sinon (relance
  reprend là où ça a bloqué). `cockpit dispatch <feature>` (mono) inchangé. +2 tests (parse + smoke rapport).

### Facette de feature — activation + prompt (P1 cockpit-typed-bundles, Phase 3)
- **`provision/facet.py`** (nouveau) : `resolve_facet(root, feature_facet)` (feature.facet → default_facet du
  `.cockpit/bundle.toml` → fallback `doc`) et `activate_facet(wt, facet)` (copie
  `.claude/facets/<f>/settings.local.json` → `.claude/settings.local.json` gitignoré). Déterministe, fail-soft.
- **`dispatch/worktree.reserve`** : active la facette de la feature DANS la worktree après checkout (hooks/
  permissions du type de travail ; idempotent).
- **`roadmap/prompt.build_worker_prompt`** : injecte **persona + méthode** de la facette (lues des `.md`
  committés) + les **critères d'acceptation** de la task (`## Critères d'acceptation (DoD)`). Un même worker
  passe du backend au frontend en étant **ré-aligné par la facette de sa feature**.
- **CLI/API** : `roadmap add-feature --facet`, `task add --acceptance` ; `FeatureCreate.facet`,
  `TaskCreate.acceptance`. +9 tests (activation gitignorée, injection persona/méthode/critères, fail-soft).

### Bundles par type — `create --type` (P1 cockpit-typed-bundles, Phase 2)
- **`provision/`** : `payload/` → `bundles/base/` (git mv) + `bundles/types/{service-api,cli-tool,front-ts}/`
  (overlays). `load_bundle(project_type)` compose **base ⊕ overlay** (whole-file : `base | overlay`,
  déterministe) ; `load_payload()` = shim `load_bundle("generic")`. Chaque bundle porte `.cockpit/bundle.toml`
  (facettes + default_facet) et des facettes `.claude/facets/<f>/` (PERSONA/METHOD/settings.local.json).
- **`projects/registry.create_project(project_type=…)`** : valide l'enum (re-check du CHECK DDL) + persiste
  + sème `load_bundle(project_type)`. CLI `project create --type {generic|service-api|cli-tool|front-ts}` +
  route `POST /api/projects` (`ProjectCreate.project_type`).
- Design DRY : les overlays surchargent `docs/architecture.md` (identité du type) + ajoutent leurs facettes,
  **sans dupliquer** le `CLAUDE.md` commun (base reste la source unique du contrat). +10 tests (compose,
  override whole-file, déterminisme, cohérence facettes↔dossiers, seed typé).

### Schéma **v6** — fondations typed-bundles (P1 cockpit-typed-bundles, Phase 1)
- **`db/schema.py`** `SCHEMA_VERSION` **5 → 6** : `projects` gagne `project_type`
  (`generic|service-api|cli-tool|front-ts`, défaut `generic`, `CHECK` en DDL + validé par
  `registry.create_project`) — le bundle semé à la création ; `features` gagne `facet` (nullable :
  `backend|frontend|tool|doc`, la facette de dispatch qui aligne le worker) ; `tasks` gagne `acceptance`
  (nullable TEXT : critères de DoD injectés dans le prompt worker). Migration `ensure_columns` (ALTER
  idempotent), non-breaking. Cf. `docs/schema-contract.md` (SQLite + roadmap.yaml).
- **`db/store.py`** : `PRAGMA busy_timeout=5000` (prérequis de l'orchestrateur parallèle à venir — évite
  `SQLITE_BUSY` entre connexions-par-thread ; bénin pour le mono).
- **`roadmap/model.py`** : `add_feature(facet=…)` (validé contre `FACETS`), `add_task(acceptance=…)`,
  `to_yaml` émet `facet`/`acceptance` **seulement si présents** (contrat roadmap.yaml rétro-compatible).

### Claude Code dans le terminal web — `provision-ct.sh --with-claude` (P1 cockpit-workspace-ux)
- **`deploy/provision-ct.sh`** gagne un flag opt-in `--with-claude` (défaut off) : une étape d'install (recette
  renumérotée `[n/7]`) pose le CLI `claude` via l'**installeur natif officiel** (`claude.ai/install.sh`, binaire
  autonome — **aucun Node, aucune clé API**) dans le `~/.local/bin` de l'utilisateur du service (propriétaire du
  PTY du terminal). Durci : download-puis-exec (jamais `curl | bash`), retry x2, guard PATH `~/.profile`+`~/.bashrc`
  (le PTY lance `bash -l`), idempotent (skip si présent), vérif dure `claude --version`. Login OAuth au 1ᵉʳ run.
- **`docs/install.md`** : section « Claude Code dans le terminal web ». Aucun changement de schéma.

### Recette CT reproductible — dernière version + batteries incluses (P3 cockpit-batteries-included)
- **`deploy/build-wheel.sh`** (mainteneur, Node build-time) : build la SPA puis `pip wheel --no-deps` **depuis
  HEAD** (jamais un snapshot en retard, D5) ; garde-fou qui vérifie que l'UI est bien embarquée dans le wheel
  (`cockpit/_web_dist/index.html`).
- **`deploy/provision-ct.sh`** (hôte cible, Python seul, aucun Node) : venv → install du wheel → `install-service`
  → dépôt du manifeste → **`cockpit bootstrap`** → activation systemd, **en une commande**. Idempotent
  (ré-exécution sûre), fail-loud, imprime chaque étape, aucun secret en argv (token via `--token-file`).
- **`deploy/bootstrap.yaml`** : l'**édition maintainer** livrée — les 5 outils du framework (`cockpit`,
  `code-map`, `front-map`, `docs-map`, `forgemaster-catalogs`) en `kind=tool`. Donnée versionnée, aucun secret ;
  gate-protégée par un test qui la fait relire par le vrai `load_manifest`.
- **`docs/install.md`** : section « Édition maintainer — recette CT reproductible » (build → provision →
  publier plus tard sans changement de code, D7).
- Aucun changement de schéma (scripts + docs + donnée d'édition).

### Amorçage des outils du framework — manifeste + `cockpit bootstrap` + étape wizard (P2 cockpit-batteries-included)
- **Module `bootstrap.py`** : lit un manifeste `<COCKPIT_HOME>/bootstrap.yaml` (édition maintainer, SEC,
  aucun secret) et **adopte** chaque outil via P1 (`create_project(source_url=…)`), classé `kind=tool` (rail
  section « Outils »), miroir câblé sur la source. **Idempotent** (slug présent → `skipped`) ; échec d'une
  entrée isolé (`failed`, la boucle continue) ; manifeste **absent → no-op** propre ; manifeste **invalide →
  abort** (fail-loud). Credential **par entrée** (`credential_ref`, un token de lecture par repo) avec repli
  sur un `shared_ref` partagé, sinon clone anonyme (repos publics, forward-compatible).
- **CLI `cockpit bootstrap`** : `--init` écrit un gabarit (garde no-overwrite) ; `--token-file <f>` lie un
  token de lecture partagé (voie fichier → store, jamais en argv/DB/manifeste). Sortie 1 s'il reste des échecs.
- **Schéma HTTP** : `GET /api/bootstrap` (aperçu **idempotent** goto-only : `{available, tools:[{slug,
  source_url, kind, adopted}], adopted, total}`) · `POST /api/bootstrap` `{shared_ref?}` (exécute ;
  `{created, skipped, failed, available}`). `docs/schema-contract.md` §2b + §3 à jour.
- **Wizard `/setup`** : étape « Outils du framework » (shallow : installer la boîte à outils / ignorer) —
  liste les outils du manifeste + « Installer la boîte à outils (N) » d'un clic ; manifeste absent = note
  générique (wizard intact). `useBootstrap`/`useRunBootstrap` + schémas Zod. Boucle visuelle : rendu vérifié.
- Aucun bump `SCHEMA_VERSION` (route + fichier-manifeste additifs, aucune colonne).

### Adopter un dépôt existant — clone au lieu de semer (P1 cockpit-batteries-included)
- **Primitif bare-safe** `InternalGit.clone_sot(sot, url, *, creds_env=None)` : `git clone --bare` du repo
  distant (son vrai historique) ; auth **optionnelle** via `credential_env` (privé → token transitoire ;
  public → anonyme, forward-compatible) ; `_normalize_forge_branches` garantit `dev`+`main` (synthèse depuis
  `master`/`main`-only). Le résolveur de credential `cred_resolver(settings)` est **remonté** dans
  `secrets/` (partagé merge + registry).
- **`create_project(..., source_url=…)`** : branche le SoT sur un **clone** au lieu du seed, en **clone/insert
  atomique** (un clone échoué ne laisse aucune row → reprise propre ; échec → `ValueError`/400 avec hint).
- **Schéma (v4→v5)** : `projects.source_url` (nullable, provenance d'un projet adopté ; métadonnée, jamais un
  secret). `POST /api/projects` accepte `source_url?` (repos publics via l'API) ; CLI `cockpit project create
  --from <url>`. `docs/schema-contract.md` §1 + §3 à jour.
- **`ui_shot.py --click TEXTE[#N]`** (répétable, séquentiel) : joue des gestes **read-only** après le goto,
  avant la capture, pour rendre LIVE une surface pilotée par un state React sans route (détail de commit,
  visionneuse, historique). S'appuie sur le champ additif `clicks` du runner `render_check.js` (usage strict :
  n'ouvrir que du read-only, jamais un geste mutant). A servi l'acceptance P4 (les 3 pièces click-gated
  capturées LIVE + Read). Aucun changement de schéma/route.

### Intelligence git read-only — détail de commit + diff de feature + historique fichier (P3 git-repo-explorer)
- **Primitives bare-safe** dans `git/internal.py` : `commit_detail(sot, sha)` (métadonnées + fichiers touchés
  avec `+/-` par fichier, `null` pour un binaire) et `file_history(sot, ref, path)` (commits touchant un
  fichier, récents d'abord). Le diff `base...head` réutilise `diff_text`/`diff_names` déjà écrits.
- **Schéma HTTP** : `GET /api/projects/{p}/git/commit/{sha}`, `GET /api/projects/{p}/git/diff?base=&head=`
  (diff unifié three-dot ; `diff=""` si réfs alignées — 200), `GET /api/projects/{p}/git/history?ref=&path=`
  (fichier sans historique → `[]` — 200). Tous read-only, idempotents (goto-only safe) ; **404** projet/réf/
  sha introuvable. `docs/schema-contract.md` §git mis à jour.
- **Front** : détail de commit (clic sur une entrée de log/branche), **Diff de feature** rendu unifié coloré
  (base/head réutilisant les branches chargées ; tokens sémantiques, aucune teinte inline), historique par
  fichier (basculeur dans la visionneuse). Aucune mutation.

### Explorateur de dépôt read-only — arbre + contenu (P1 git-repo-explorer)
- **Primitives bare-safe** dans `git/internal.py` : `ls_tree(sot, ref, path="")` (entrées d'un dossier à une
  réf, dossiers d'abord, via `ls-tree --long <ref>:<path>`) et `read_blob(sot, ref, path)` (contenu d'un
  fichier via `cat-file`, **octets bornés**). Gardes L4 : `too_large` au-delà de 10 Mo (aucune lecture),
  `binary` si NUL détecté, `truncated` au-delà de 512 Ko — **jamais d'octets bruts émis**. Read-only,
  bare-safe (ni index ni working-tree).
- **Schéma HTTP** : `GET /api/projects/{p}/git/tree?ref=&path=` et `GET /api/projects/{p}/git/blob?ref=&path=`
  (idempotent, goto-only safe ; **404** projet/réf/chemin introuvable). Aucune mutation. `docs/schema-contract.md`
  §git mis à jour.

### Production serve — service systemd + `docs/install.md` (P3 turnkey-install)
- **`cockpit install-service`** (nouvelle sous-commande) : génère une unité systemd pour `cockpit serve` —
  portée **user** (défaut, sans root, `~/.config/systemd/user/`) ou **`--system`**. Écrit aussi un
  `cockpit.env` gabarit (store/bind, jamais un secret ; conf existante préservée) et **imprime** les
  commandes `systemctl` (n'exécute pas systemctl → pas de footgun privilège). `Environment=HOME` épinglé
  (sinon git ne lit pas le helper de credentials). Module pur `cockpit.service` + gabarit manuel
  `deploy/cockpit.service`.
- **`docs/install.md`** : guide turnkey self-hosted — wheel packagé (aucun Node) vs sources, wizard 1er
  démarrage, service systemd + note reverse-proxy/TLS (pas d'auth intégrée), coffre file/BWS, mise à jour.
  README + index docs mis à jour.

### Wizard : le token de push vit dans Réglages, pas dans le wizard (retour terrain P2)
- Retrait de l'étape « Miroir GitHub & token » du wizard `/setup` : elle ne gérait que les projets déjà
  à-miroir-sans-token et ne permettait pas d'ajouter un miroir à un projet fraîchement créé → cul-de-sac.
  La gestion **miroir + token par repo** reste dans **Réglages** (surface complète, éditable à tout moment).
  Le wizard s'y contente d'un **renvoi** quand un token de push est en attente ; le **bandeau** « token requis »
  pointe désormais vers **Réglages** (et non plus le wizard).

### Wizard 1er-démarrage guidé (`/setup`) + first-run (P2 turnkey-install)
- **`GET /api/onboarding`** gagne `project_count` + **`first_run`** (aucun projet → instance neuve). Corrige
  le faux « complet » sur une instance vide : le wizard **guide** (« crée ton 1er projet ») au lieu d'annoncer
  qu'il n'y a rien à faire. `onboard status` (CLI) affiche aussi l'invite 1er-démarrage.
- **Wizard `/setup`** (`SetupWizard`) : page guidée à étapes vivantes — bienvenue → coffre de secrets (backend
  + `health` + hint BWS) → **créer ton 1er projet** → miroir GitHub + token (optionnel) → prêt. **Non
  bloquant**, quittable, ré-ouvrable. Il **séquence** les affordances existantes (il ne les réécrit pas) :
  `NewProjectForm` (extrait du rail, **source unique** de création), `MirrorForm`, `CredentialForm`.
- **Surfaçage first-run** : le bandeau du shell invite à `/setup` sur instance neuve ; la Landing affiche une
  carte de bienvenue → wizard ; le bandeau « incomplet » et le rail « à régler » pointent aussi `/setup`.
- **Distribution turnkey** : l'utilisateur final n'installe **que Python**. Le hook de packaging
  `hatch_build.py` **force-include `web/dist`** dans le wheel sous `cockpit/_web_dist` — `pip install <wheel>
  && cockpit serve` sert la SPA **sans Node requis**. Le front se build via `npm run build`/`cockpit setup`
  **avant** de packager (le hook ne lance pas npm : éviter le footgun `pip install -e`). Dist **jamais**
  re-committée (respecte `docs/specs/web-cockpit-spa.md`).
- **`web_dist_dir()`** cherche désormais dans l'ordre : `COCKPIT_WEB_DIST` → dist empaquetée
  (`cockpit/_web_dist`, wheel) → layout source (`web/dist`, dev).
- **`cockpit setup`** (nouvelle sous-commande) : build l'UI depuis les sources (from-clone) ; **fail-loud**
  avec instructions si Node/npm absent ; no-op sur une install wheel (UI déjà incluse). Module réutilisable
  `cockpit.webbuild`.
- **`_mount_spa` fail-loud** : dist absente → **page d'aide à `/`** (« UI non buildée → `cockpit setup` ou
  wheel packagé ») + warning au log, **au lieu d'un 404 muet**. L'API (`/api`, `/health`) reste valable.

### Projet GitHub-backed depuis l'UI : config du miroir + token (phase 4c-2, suite)
- **`registry.set_mirror_remote`** + route **`PATCH /api/projects/{slug}` `{mirror_remote?}`** (édite le
  miroir GitHub d'un projet existant ; `null`/vide le retire). Créer un projet à l'UI puis le rendre
  GitHub-backed sans passer par la CLI. Un miroir posé rend un token de push *requis*.
- **Front** : champ « miroir GitHub (optionnel) » au formulaire de création ; `MirrorForm` (configure/édite/
  retire le miroir, partagée Réglages + vue projet) ; le panneau Réglages et la carte Git montrent désormais
  **Miroir** (toujours éditable) puis **Token** (une fois le miroir posé) ; hooks `useSetMirror` +
  `api.updateProject`. Corrige le trou remonté : un projet créé à l'UI (local-only) pouvait afficher « aucun
  miroir » sans aucune voie pour ajouter un secret.

### Onboarding — wizard web : bandeau + panneau Réglages + token/repo (phase 4c-2, front)
- **Bandeau non bloquant** (`OnboardingBanner`) dans le shell : rappelle une config incomplète (coffre
  injoignable ou N tokens de miroir requis) → renvoie vers Réglages. Le cockpit reste utilisable.
- **Panneau Réglages** (`/settings`, `SettingsTab`) : carte racine du coffre (backend + `health` + état) +
  liste des credentials par repo (lié / requis / aucun miroir) avec l'affordance de liaison.
- **Affordance token/repo** (`CredentialForm`, partagée) : **backend-aware** — voie fichier (token masqué,
  `type=password`) vs voie BWS (UUID). Un token lié n'affiche QUE sa référence tronquée, jamais la valeur.
  Réutilisée sur la vue projet (`ProjectCredentialCard`, onglet Git — là où vit le push/mirror).
- **Data layer** : `ProjectSchema` gagne `credential_ref` ; schémas `OnboardingStatus`/`SecretStoreHealth`/
  `OnboardingRequirement` + `CredentialLinkInput` ; hooks `useOnboarding`/`useLinkCredential`/
  `useUnlinkCredential` (invalident onboarding + projets + projet — source unique Python, jamais deviné).
- Boucle visuelle : `ui_shot.py` seede un état credential mixte (1 lié / 1 requis) → routes vérifiées au
  screenshot. Gate front vert (eslint + vitest 26 + tsc/build) + `front_conformance` OK.

### Onboarding — check config-requise + `cockpit onboard` + routes credential (phase 4c-1, backend)
- Nouveau module **`src/cockpit/onboarding.py`** : `status()` (racine du store joignable via `health()` +
  exigences par projet — un projet à `mirror_remote` a **besoin** d'un token ; `complete` sans faux-vert) et
  `link_credential()` / `unlink_credential()`. Deux voies unifiées : **fichier** (`token` → `store.put` → réf
  opaque) et **BWS** (`ref`/UUID bring-your-own, validé via `store.get` avant liaison). La DB ne reçoit que
  la **référence** ; le store la valeur — jamais de token en log/argv/retour d'API.
- **`SecretStore.health()`** (nouvelle méthode du Protocol) : racine de confiance joignable ? `file` =
  zéro-config (toujours prêt) ; `bws` = prêt ssi `BWS_ACCESS_TOKEN` se résout (check **local**, aucun login
  réseau, ne révèle pas le token).
- **CLI `cockpit onboard`** : `status` (défaut — ce qui manque, exit 1 si incomplet), `link <project>
  --token-file <f>` (jamais le token en argv) `| --ref <uuid>` `[--label]`, `unlink <project>`.
- **API** : `GET /api/onboarding`, `POST /api/projects/{p}/credential` `{token?|ref?, label?}` (réponse =
  `credential_ref`, jamais le token ; 400/404), `DELETE /api/projects/{p}/credential`. `Deps.secret_store()`
  expose le store actif par injection.

### credential_ref par entité + résolution au writeback (phase 4b — onboarding self-hosted)
- **Schéma SQLite v4** (bump `SCHEMA_VERSION=4`) : `projects` gagne `credential_ref` (`TEXT`, nullable,
  aucun défaut → `NULL` rétroactif). Migration en place idempotente (`ensure_columns`). La DB ne stocke que
  la **référence** opaque, jamais le token (spec merge-writeback).
- **`registry`** : `create_project(..., credential_ref=None)` + nouvelle `set_credential_ref(conn, slug, ref)`
  (lie/délie la référence — l'affordance « token par repo » de l'onboarding écrit ici).
- **`git/internal`** : nouvelle primitive pure `credential_env(token, base=…)` — injecte le token pour un
  push GitHub HTTPS via `GIT_CONFIG_*` (`url.insteadOf`, `x-access-token`), **le temps du push seulement**,
  jamais dans un `.gitconfig` ni dans l'argv, `GIT_TERMINAL_PROMPT=0`. `InternalGit(cred_resolver=…)` résout
  la référence à l'usage ; `merge_writeback` l'injecte quand une `creds_ref` est présente (sinon push
  ambiant — compat). Le paquet git n'importe **jamais** `cockpit.secrets` (la policy vit chez l'appelant).
- **`gate/merge`** : `run_merge` lit `project['credential_ref']` et construit `InternalGit` doté du résolveur
  adossé au store actif (`build_store`, **lazy** : le store n'est bâti que si une réf est présentée ;
  **total** : secret absent/illisible → `''` → push best-effort, jamais bloquant). **0 token en DB**.

### Secret store pluggable (phase 4a — onboarding self-hosted)
- Nouveau paquet **`src/cockpit/secrets/`** : Protocol `SecretStore` (`put→ref` / `get` / `delete` / `has` /
  `list_entries`) + deux backends. La DB stocke une **référence opaque** (`credential_ref`), jamais le token ;
  le store résout à l'usage. Socle stdlib-pur (crypto/SDK importés paresseusement).
- **`EncryptedFileStore`** (défaut) : chiffrement authentifié au repos via **Fernet** (dép cœur `cryptography`),
  clé-600 + blob sous `home/secrets/`. Écritures atomiques (`O_EXCL`/`os.replace`). Invariant testé : **0
  plaintext au repos** (la valeur n'apparaît nulle part en clair), refus si blob altéré/clé absente.
- **`BwsStore`** (extra optionnel `cockpit[bws]`) : Bitwarden Secrets Manager via le **SDK officiel**
  (`bitwarden-sdk`, région configurable `BWS_API_URL`/`BWS_IDENTITY_URL`), secrets par **UUID**, cache
  process-lifetime (auth réutilisée), `client_factory` injectable. Racine = `BWS_ACCESS_TOKEN` (env ou
  fichier-600). `put`/`delete` non supportés (bring-your-own UUID) → `SecretUnsupported`.
- **`config`** : sélecteur `secret_store` (`COCKPIT_SECRET_STORE`, défaut `file`) + propriété `secrets_dir` ;
  `secrets.build_store(settings)` choisit le backend. `Settings` reste rétro-compatible (nouveau champ à défaut).

### Structure (phase repo-structure)
- Squelette du package `src/cockpit/` (src-layout, hatchling), CLI `cockpit` câblée (project/roadmap/task/
  dispatch/gate/merge/serve), imports serveur paresseux.
- **Socle fonctionnel** : `config` (résolveur générique des racines), `core/{run,ids,fs}` (exécution
  locale + slugs + accès borné), `db/{schema,store}` (schéma SQLite `SCHEMA_VERSION=1`, 4 tables).
- **Stubs documentés** pour toutes les couches (git/projects/roadmap/dispatch/gate/daemon/terminal), chacun
  pointant sa source vault + son refactor `#N` (`docs/weak-points.md`).
- `docs/` : architecture, schema-contract (SQLite + roadmap.yaml + API), weak-points (13 dettes refusées →
  refactor), multi-os (WSL-first), `specs/` (6 décisions distillées en contraintes de design).
- `.claude/` vendoré (persona `tool-builder`, skills `port-tool` + `quality-gate` adapté smoke-réponse,
  hook post-edit, templates), `.gitattributes` (eol=lf), `PORTING.md`, ce changelog.

### Portage (phase port-tools)
- Couches **git/internal**, **projects/registry**, **roadmap/model**, **roadmap/resolver** portées (SoT bare
  local, worktree flock, DAG `classify`+`eff_prio`).
- **Schéma SQLite v2** (bump `SCHEMA_VERSION=2`) : `dispatch_jobs` gagne `session_id` + métriques
  (`num_turns`/`cost_usd`/`wall_s`/`engine`) ; nouvelle table `port_reservations` (broker de ports mono-hôte).
  Migration en place idempotente (`ensure_columns`).
- Couche **dispatch** : `ports` (broker déterministe simplifié mono-hôte), `worktree` (réserve worktree+port,
  cleanup avant delete-branch), `worker` (spawn `claude -p` **local** via runner injectable, prompt sur stdin,
  gate **no-task-no-dispatch**), `jobs` (état + suivi de log **incrémental** offset/inode, normaliseur porté).
- Couche **roadmap/prompt** : synthétiseur de prompt worker (pattern `plan_prompt`, contexte in-repo, sans
  corpus vault).
- `git/internal.init_sot` **amorce** désormais `dev`+`main` (commit racine) pour qu'une feature ait une base.
- Couche **gate** (chaîne d'autorité, internal-first) : `review` (verdict Tier-1 **lié au SHA de la feature**
  + garde déterministe `evidence ⊂ diff`, état sous config clé (projet, feature)), `verify` (Tier-1.5
  feature-verified **N/A-safe** + **fail-closed**, runner node par config), `merge` (`compose_merge_decision`
  portée **verbatim** — Tier-0 non-overridable → natif N/A-safe → Tier-1 SHA-bound → Tier-1.5 conditionnel-UI
  → **GO humain** ; gate-vert-sans-go = `hold`) + `run_merge` **internal-first** : ff `feature→dev→main`
  (main-suit-dev), writeback identité injectée, **cleanup worktree AVANT `delete_branch`**, clôture DB
  (feature `merged`, tasks landées `done`). Merger une feature jamais dispatchée = outcome propre (pas de crash).
- `GitBackend` gagne les primitives `feature_sha` / `diff_names` / `diff_text` (lectures d'ancrage du gate) et
  `commit_worktree` (la forge committe le travail du worker, qui ne fait pas de git) ; `git/identity` (nouveau,
  neutre) : identité writeback déterministe `<projet>-<base>-<rôle>` (port de `worker_identity`). Le dispatch
  committe le travail worker en fin de run réussi (SHA d'ancrage pour le gate).
- Couche **daemon** (FastAPI, **DI explicite** anti god-module) : `deps` (conteneur `Deps` posé sur
  `app.state`, lu par `get_deps` — plus de `import server`), `app.build_app` (routers par domaine + handlers
  d'erreur `KeyError`→404 / `ValueError`→400, import fastapi/uvicorn **paresseux**), routers **fins**
  `routes/{projects,roadmap,dispatch,gate,terminal}` délégant aux couches portées. Dispatch en threadpool
  (le spawn `claude -p` peut bloquer). On **jette** gpu/host/proxmox/spawn/signals/qa/auth/ports-HTTP du legacy.
- Couche **terminal** : `pty.pty_bridge` **local** (PTY sur `bash -l`, `cwd=workdir` — plus de ssh `-tt`, #2) ;
  workdir borné par `core.fs.safe_path` (#4). Exposé en WebSocket `/ws/terminal/{project}`.
- Config ruff : `flake8-bugbear.extend-immutable-calls` (idiome FastAPI `Depends` en défaut d'argument).

### Modèle d'entité projet/outil (phase cockpit-productization P3)
- **Schéma SQLite v3** (bump `SCHEMA_VERSION=3`) : `projects` gagne `kind` (`project`|`tool`, CHECK,
  défaut `project`) + `owner` (nullable, compat multi-user). **Une seule table + discriminateur** (pas deux).
  Migration en place idempotente (`ensure_columns` — défaut littéral `kind='project'` sur l'existant) ;
  garde ajoutée : `ensure_columns` **saute** une table absente (ALTER sûr sur base partielle). Cf.
  `docs/schema-contract.md` §1 + migration v2→v3. Ajout **non-breaking**.
- `registry.create_project` accepte `kind`/`owner` (valide `kind∈{project,tool}` → `ValueError`/400) ; CLI
  `project create --kind {project,tool}` ; route `POST /api/projects` expose `kind`.
- **Front** : rail **2 sections** (`ProjectRail` → **Projets** / **Outils** partitionnés par `kind`) sous
  « Espace de travail » ; `ProjectSchema` + `kind`/`owner`, `CreateProjectInput.kind`. `ui_shot` seede des
  outils démo (section « Outils » VOYANTE). Feature-verified visuellement (`/`).

### Vue Git (phase cockpit-productization P2)
- **Route read-only** `GET /api/projects/{p}/git` (nouveau `routes/git`, monté dans `app.build_app`) : vue du
  SoT bare — branches, avance/retard `main` vs `dev` (le signal « main rattrape dev »), log court par réf
  protégée. Aucune mutation (le cycle git reste dans `gate/merge`). Ajout **non-breaking** (nouvelle route,
  pas de bump). Cf. `docs/schema-contract.md` §3.
- **Primitives bare-safe** dans `git/internal` : `branches` (for-each-ref → nom·sha·sujet), `log`
  (log --oneline parsé), `ahead_behind` (rev-list --left-right --count). Read-only, ni index ni working-tree.
- **Front** : onglet **Git** (`pages/GitTab` + route `git` + entrée `WorkspaceTabs`) — bannière de synchro
  dev↔main, branches teintées par réf (`gitBranchTone`), log par réf. Schémas Zod `GitView*` + `api.getGit` +
  `useGit`. Boucle visuelle : `ui_shot.py` seede un état « dev en avance sur main » (route `/…/git`).
