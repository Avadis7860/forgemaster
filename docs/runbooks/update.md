# update — runbook (poser un wheel local en bleu/vert, et revenir — automatiquement ou sur demande)

Le cycle de mise à jour est la **moitié locale et hors-ligne** d'un canal : il pose le `.whl` qu'on lui désigne, rien
d'autre. Aucune détection de version, aucun manifeste servi, aucune signature, aucun réseau. Deux modules, et la
frontière entre eux est un contrat : `update.py` vit dans le paquet, voit la base et git, et porte **tout ce qui
refuse** ; `apply_update.py` est **stdlib-pur, zéro import `forgemaster`**, copié hors du paquet puis lancé détaché
sous le `python3` du système — il doit survivre au venv qu'il remplace, et rester jouable à la main quand
l'installation est cassée, c'est-à-dire exactement quand on en a besoin.

Trois invariants tiennent tout le reste. **On prouve avant de toucher** : le wheel est installé dans un venv neuf, à
côté, et sondé sur un port et un `FORGEMASTER_HOME` jetables — tant que ce n'est pas vert, l'instance vivante n'a subi
ni arrêt, ni migration, ni bascule. **La bascule est un lien remplacé atomiquement**, jamais une réinstallation en
place : revenir coûte alors le même geste dans l'autre sens, et c'est la seule façon qu'un retour arrière soit fiable.
**Aucune moitié n'est atteignable** : le geste déplace le binaire *et* les données, ou il refuse — une base montant en
forward-only, un binaire ancien sur des données neuves est définitif.

Le substrat qu'ils pilotent (prendre un instantané, le remettre) a son propre runbook : `snapshot.md`.

## UpdateRefused — le refus fail-closed, levé avant tout effet
`src/forgemaster/update.py:54` · levée par `preflight`, `preflight_rollback`, `parse_exec_start` · rattrapée par `cli_dispatch`
L'instance est **intacte** quand cette exception sort : c'est ce que « fail-closed » veut dire ici. `cli_dispatch` la
rend en `✗ … refusé(e) — rien n'a été touché` et rc 1. Son pendant côté applicateur est `UpdateFailed`
(`apply_update.py:67`), qui signifie la même chose un cran plus bas : échec **arrêté avant la bascule**.

## preflight() — tout ce qui doit être vérifié avant que la moindre chose bouge
`src/forgemaster/update.py:58` · appelé par `cli_dispatch` · retourne le plan (chemins + URL de sonde) · lève `UpdateRefused`
Vérifie le wheel (existe, suffixe `.whl` — aucune résolution, aucun réseau : ce verbe ne pose que le fichier désigné),
délègue le socle de service à `_preflight_service`, puis refuse sur le travail non commité. Le verdict d'autorité
`authority` est **calculé par l'appelant** et passé en argument (injection explicite) : ce module ne va pas chercher
une connexion DB tout seul, et un préflight qui refuse ne doit pas avoir ouvert quoi que ce soit en écriture.

## _preflight_service() — les quatre refus que l'aller ET le retour partagent
`src/forgemaster/update.py:77` · appelé par `preflight` et `preflight_rollback` · lève `UpdateRefused`
Quatre refus, tous explicites, jamais un devinage — et chacun nomme le geste qui débloque :

1. **portée système sans être root** — `systemctl` échouerait en plein milieu, service arrêté. Refuser avant ;
2. **pas d'unité systemd** — la bascule exige un service gérable ; on n'invente pas une façon de redémarrer le
   forgemaster de quelqu'un ;
3. **pas de lien stable** — sans `<home>/current`, il n'y a rien à remplacer, donc rien à défaire ;
4. **une unité qui lance un venv EN DUR** — l'état de toute installation antérieure au bleu/vert. Elle n'est pas
   cassée, elle est *non migrée*, et la MAJ n'aurait **aucun effet** sur le service. Réécrire l'unité sous les pieds de
   l'utilisateur serait pire que refuser.

Extrait de `preflight` en écrivant `preflight_rollback` : dupliquer ces quatre refus aurait produit deux jeux de
messages qui divergent, alors que c'est le même invariant de déploiement qui est en jeu.

## _refuse_uncommitted_work() — le refus d'autorité, porté par les DEUX gestes
`src/forgemaster/update.py:108` · appelé par `preflight` et `preflight_rollback` · consomme `projects.authority.blocking`
`projects_root` **n'entre pas dans l'instantané** : si le geste tourne mal, le travail qui n'est qu'là ne reviendra
pas. Le motif « git fait autorité » n'est vrai que là où il *y a* une autorité — le refus la vérifie au lieu de la
supposer. On ne bloque **que** sur du non-commité : « aucun remote » est un cas normal du produit distribué, et
refuser dessus interdirait toute mise à jour à qui n'en veut pas. Porté aussi par le retour depuis le 2026-08-06 —
revenir en arrière pendant qu'un travail non commité vit dans un worktree est exactement le geste à refuser.

## preflight_rollback() — le préflight du retour VOLONTAIRE, plus la résolution de la cible
`src/forgemaster/update.py:126` · appelé par `cli_dispatch` · retourne le plan (+ `snapshot`, `target_venv`) · lève `UpdateRefused`
Même socle de service, même refus d'autorité, plus la question propre au retour : **quel instantané remettre, et vers
quel venv rebasculer**. La correspondance instantané ↔ binaire se **dérive** par égalité de schéma, elle ne se stocke
pas — aucun état nouveau, aucune ligne de plus au manifeste, et le garde vaut aussi pour les instantanés *déjà pris*.
Sans référence explicite, on **parcourt** les candidats au lieu de prendre le premier `restaurable` : le plus récent
est souvent l'instantané de sûreté d'un retour déjà fait, qui ramènerait vers la version qu'on vient de quitter. Quand
aucun ne convient, le refus liste les motifs un par un puis `_PISTES` (`update.py:180`), les trois gestes voisins —
dont `update apply`, « le verbe qui va, lui, en AVANT ».

## _cible_utilisable() — quatre façons de ne pas être un retour en arrière
`src/forgemaster/update.py:185` · appelé par `preflight_rollback` · retourne `(venv cible, motif de refus)`
Instantané invalide · état autre que `restaurable` · venv introuvable alors qu'il se dit restaurable (la liste et la
résolution ne voient pas le même disque : on ne devine pas) · **le venv déjà actif** · et la quatrième, révélée en
revue : une cible dont le binaire lit un schéma **supérieur** au courant — « son binaire lit le schéma 21 et le tien
lit le 20 : ce serait aller EN AVANT, pas revenir ». Un état dit ce qu'un artefact *peut* faire, jamais si c'est le
geste qu'on *demande* : la direction est une propriété du verbe, pas de la cible.

## _venv_pour() — égalité de schéma, jamais « au moins »
`src/forgemaster/update.py:212` · appelé par `_cible_utilisable` · retourne le venv ou `None`
Cherche sous `<home>/venvs` le venv dont le forgemaster lit **exactement** le schéma de cet instantané
(`restore.snapshot_schema` vs `restore.python_schema`). Un binaire qui lit plus loin remettrait les données puis
migrerait la base en avant — l'état que `snapshot list` nomme `données seules`, et qui n'est pas un retour arrière.

## parse_exec_start() — l'unité est la SEULE vérité sur le bind du service
`src/forgemaster/update.py:232` · appelé par `_preflight_service` · retourne `(binaire, host, port)` · lève `UpdateRefused`
Lit le dernier `ExecStart=` de l'unité et en tire le binaire, `--host` et `--port`. Déduire le bind de
`forgemaster.env` ou d'un défaut sonderait une **autre** instance que celle qu'on vient de redémarrer, et conclurait au
vert sur la mauvaise. Port illisible → refus : sans lui, aucune vérification en vivant, donc aucun retour arrière
automatique.

## describe() / describe_rollback() — ce qui va se passer, dit avant de le faire
`src/forgemaster/update.py:253` · `src/forgemaster/update.py:274` · appelés par `cli_dispatch` · seul contenu de `--dry-run`
`describe_rollback` nomme **les deux gestes et leur ordre** — c'est l'unité que le retour rend exécutoire, et la dire
ici est ce qui permet de refuser en connaissance de cause. Les deux ajoutent une ligne « hors instantané » pour les
projets qui n'ont pas bloqué : un projet sans remote n'est pas une faute, mais l'utilisateur doit savoir que sa seule
copie est là et qu'elle n'entre pas dans l'instantané. Ce qui n'a pas bloqué est **dit quand même**.

## launch() — un seul lanceur détaché pour les deux gestes
`src/forgemaster/update.py:296` · appelé par `cli_dispatch` · retourne le rc de `follow` (ou 0 si `--detach`)
Crée `<home>/updates/<horodatage>/`, y **copie** `apply_update.py` sous le nom `apply.py`, et le lance détaché
(`start_new_session=True`) sous `_system_python()` (`update.py:347`) — jamais le python du venv qu'on remplace. Copié
et non lancé depuis le paquet : le script doit survivre au venv qu'il remplace. Détaché parce qu'une MAJ qui bascule
le venv et redémarre le service ne doit pas mourir parce que le shell a été fermé — ni, surtout, parce que c'est le
daemon lui-même qui l'a lancée et qu'on vient de l'arrêter. La sortie du `Popen` est redirigée dans `launch.log` : un
tuyau non lu bloquerait l'écrivain. `mode` ne change que les arguments de cible — même applicateur, même dossier de
run, même journal, même `result.json`.

## follow() — détaché ne veut pas dire aveugle
`src/forgemaster/update.py:326` · appelé par `launch` · retourne le rc lu dans `result.json`
Suit `journal.log` en flux jusqu'à ce que `result.json` apparaisse. Un suivi interrompu (délai `FOLLOW_TIMEOUT`,
`update.py:51`, 15 min) ne conclut **pas** à l'échec : il dit où regarder, et le script continue. La supervision se
fait par **fichier de verdict**, pas par le tuyau ssh — c'est ce qui la rend valable depuis un banc distant.

## _survey_authority() — dégrader honnêtement plutôt que bloquer sur ce qu'on ignore
`src/forgemaster/update.py:354` · appelé par `cli_dispatch` · retourne les verdicts, ou `[]`
N'ouvre la base **que si elle existe déjà** : un préflight qui refuse ne doit pas avoir créé la base de son refus. Une
base d'un schéma qu'on ne lit pas rend « je ne sais pas » — ce module ne bloque que sur ce qu'il **sait**.

## cli_dispatch() — `apply` et `rollback` suivent la MÊME séquence
`src/forgemaster/update.py:374` · appelé par `cli._h_update` (routé par `_HANDLERS`) · retourne le code de sortie
Préflight qui refuse avant tout effet → description → (`--dry-run` : on s'arrête là) → lancement détaché. La symétrie
est structurelle et pas cosmétique : c'est elle qui garantit qu'on ne découvre pas le chemin du retour le jour où il
compte.

---

## build_blue() — le venv neuf, à côté, jamais en place
`src/forgemaster/apply_update.py:73` · appelé par `apply` · retourne le chemin du `forgemaster` neuf · lève `UpdateFailed`
Étapes [1/6] et [2/6]. À côté parce qu'un processus ne remplace pas le wheel qu'il exécute, et surtout parce que
l'ancien venv doit rester **intact** pour pouvoir y revenir. Un wheel qui ne pose pas de commande `forgemaster` est
refusé ici : ce n'est pas un wheel de forgemaster.

## probe_isolated() — ce qu'elle prouve, et ce qu'elle ne prouve pas
`src/forgemaster/apply_update.py:88` · appelé par `apply` (étape 3/6) et `rollback` (étape 1/5) · retourne l'identité de build
Fait servir la version sur un port libre et un `FORGEMASTER_HOME` **jetable**, puis lit `/api/version`. Elle prouve
que *le wheel démarre et sert* ; elle ne prouve **pas** que ta configuration et ta base tiennent — son home est
vierge. C'est la vérification en vivant qui couvre ça, et son échec est précisément ce qui déclenche le retour. Le
wheel est **sa propre référence** : l'identité relevée ici devient l'attendu du vivant, sans manifeste ni signature.
Côté `rollback` elle a un **double effet** : elle produit l'attendu final *et* prouve que l'ancien binaire sert encore
— s'il ne servait plus, revenir vers lui n'aurait aucun sens, et rien n'a bougé.

## take_snapshot() — pris à FROID, par le forgemaster ANCIEN
`src/forgemaster/apply_update.py:121` · appelé par `apply` (4/6) et `rollback` (2/5) · retourne le dossier · lève `UpdateFailed`
Service arrêté, juste avant la bascule. Par l'**ancien** binaire (`<venv courant>/bin/forgemaster snapshot create`) et
jamais réimplémenté ici : deux implémentations, c'est une seule testée. Il protège de la **migration avant** — la
nouvelle version migre la base à sa première ouverture, et la base monte en forward-only. Un ancien forgemaster qui ne
connaît pas le verbe `snapshot` fait échouer la MAJ **ici, avant la bascule** : on ne bascule jamais sur une version
dont on ne saurait pas revenir. `--home` est porté par la sous-commande et non par la racine — constaté par
l'acceptance, pas par relecture.

## swap() — le remplacement atomique du lien
`src/forgemaster/apply_update.py:145` · appelé par `apply`, `rollback` · aucun retour
`symlink` sur un temporaire puis `os.replace`. Jamais `unlink` puis `symlink` : entre les deux, l'unité systemd
pointerait le vide. Le lien qu'il remplace est celui que `service.stable_link` nomme et que `service.pose_stable_link`
pose à l'installation (§ ci-dessous).

## matches() — un build non tamponné se DIT, il ne se conclut pas au vert
`src/forgemaster/apply_update.py:155` · appelé par `_verify_live` · **fonction pure** · retourne `(verdict, motif)`
Compare version puis SHA de build. Quand l'un des deux SHA est absent (checkout éditable, wheel sans tampon), la
comparaison est **impossible** — elle le dit et ne vérifie que la version, au lieu de faire passer une absence pour une
égalité.

## apply() — le geste complet, trois issues toutes explicites
`src/forgemaster/apply_update.py:171` · appelé par `main` · retourne `(rc, verdict, détails)`
Six étapes : venv neuf → sonde en isolation → **arrêt + instantané à froid** → bascule du lien + redémarrage →
vérification en vivant → retour arrière automatique si elle échoue. Trois issues : **posée** (rc 0), **refusée avant
bascule** (rc 1, rien n'a bougé), **revenue en arrière** (rc 1, l'instance est telle qu'avant). Le retour automatique
rebascule le lien puis restaure ; si la restauration échoue, il **re-bascule EN AVANT** (rc 2) — le binaire neuf sait
lire ces données-là, et un binaire ancien sur des données neuves est le seul état interdit.

## rollback() — le symétrique de l'aller, pas un second mécanisme
`src/forgemaster/apply_update.py:235` · appelé par `main` · retourne `(rc, verdict, détails)`
Cinq étapes, chacune réutilisant une fonction que `apply` exerce déjà. **L'ordre est contraint et non négociable : le
lien D'ABORD, la restauration ENSUITE** — `restore` interroge `<home>/current` pour savoir quel schéma le binaire en
place sait lire ; inversé, il verrait le binaire neuf et refuserait une restauration pourtant légitime.
`tests/test_rollback.py` lit cet ordre **dans le code** (AST) : le risque n'est pas l'appel d'aujourd'hui, c'est la
simplification de demain. Chaque moitié qui échoue est compensée par son appelant — bascule ratée → **aucune**
restauration tentée ; restauration ratée → re-bascule sur le courant ; retour du retour → rien à compenser (le lien
est déjà sur le binaire le plus haut), mais l'échec se **dit**. Un retour arrière qui ne partage pas le code de
l'aller est un chemin qu'on ne joue qu'en catastrophe, donc jamais joué pour de vrai avant le jour où il compte.

## _refuse_if_target_would_be_purged() — la prise de sûreté peut détruire sa propre cible
`src/forgemaster/apply_update.py:335` · appelé par `rollback` (étape 1/5) · lève `UpdateFailed`
Prendre l'instantané de sûreté consomme un cran de rétention et déclenche la purge. On refuse **avant le premier
geste** plutôt que de le découvrir entre les deux moitiés : un refus coûte une relance, une cible détruite ne se
rattrape pas. La cible survit si, et seulement si, moins de `KEEP_SNAPSHOTS - 1` instantanés complets lui sont
postérieurs. L'étape [3/5] **re-vérifie** ensuite l'existence de la cible, la prise ayant eu lieu entre-temps.

## _restore() — rendre `False` plutôt qu'avaler l'échec
`src/forgemaster/apply_update.py:393` · appelé par `apply` et `rollback` · retourne un booléen
Restaure par le script **figé dans l'instantané** — celui écrit en même temps que son manifeste, donc celui qui le
comprend ; c'est aussi le chemin de secours manuel, donc on exerce ici ce qu'on documente. Elle loggait un `⚠` et
rendait `None` : ses trois appelants concluaient « restauré » sur un échec et laissaient l'unique moitié que
l'invariant interdit. Le correctif n'est pas un message de plus, c'est un **type de retour** — la seule forme qu'un
appelant ne peut pas ignorer. Le drapeau `FORCE_FLAG` (`apply_update.py:64`) n'est passé que si `_supports_flag`
(`apply_update.py:422`) le trouve dans le texte du script figé : un instantané pris avant ce garde embarque un
`restore.py` dont l'argparse sortirait en usage, et ferait échouer le retour au pire moment. Ce n'est pas un
affaiblissement du garde de compatibilité — le lien vient d'être rebasculé sur le venv qui a **pris** cet instantané,
donc le seul état que le garde pourrait rendre est *indéterminable* ; le refus d'une incompatibilité **constatée**
reste absolu.

## _verify_live() / _wait_health() — trois issues, pas deux
`src/forgemaster/apply_update.py:432` · `src/forgemaster/apply_update.py:443` · appelés par `apply`, `rollback`, `probe_isolated`
`200` l'instance sert · **`503` portant `ready:false`** échec **immédiat, avec son motif** · rien d'autre → on attend.
Attendre la fin du délai pour conclure « ne répond pas » transformerait une réponse claire en silence, et c'est ce
silence qui remonterait à l'utilisateur comme diagnostic. `_unservable_detail` (`apply_update.py:474`) exige la forme
complète : un 503 de proxy ou d'un autre service sur ce port ne doit pas se faire passer pour un verdict de
l'instance. Si le processus sondé meurt, on n'attend pas non plus — un échec rapide vaut mieux qu'un long silence.

## ROLLBACK_DEPTH — la politique de rétention, déclarée UNE fois
`src/forgemaster/apply_update.py:56` · lu par `_purge_venvs` et par `snapshot.KEEP`
`KEEP_VENVS = DEPTH + 1` (le courant + les crans joignables) et `KEEP_SNAPSHOTS = DEPTH + 2` (la marge « échoué,
restauré, retenté, re-échoué ») en **dérivent**. Déclarée ici parce que ce module est stdlib-pur par contrat : c'est
`snapshot.py` qui lit la constante, l'inverse serait impossible. Ce qui change n'est pas le disque consommé — les
valeurs effectives sont inchangées — mais que « jusqu'où on sait revenir » devienne un **nombre nommé**, sur lequel les
deux rétentions s'accordent au lieu de coïncider.

## _purge_venvs() — une garde qui ne sait pas répondre se tait
`src/forgemaster/apply_update.py:515` · appelé par `apply` (seulement après un vivant vert) · aucun retour
Le **seul geste irréversible** d'une MAJ réussie. `keep` **est** la liste des venvs joignables : l'appelant vient de
faire la bascule, il la connaît. On ne re-devine plus par date de création — « le plus récent » et « le cran d'avant »
sont deux ordres différents qui ne coïncident qu'à `DEPTH = 1`, et au-delà l'ancienne formulation promouvait
silencieusement un bleu **ayant échoué en vivant** au rang de cible joignable. Déclaration incohérente avec la
politique → **on ne purge rien, et on le dit** : supprimer sur une liste qu'on ne comprend pas est le seul résultat
irréversible de cette fonction.

## stable_link() / pose_stable_link() — le lien qui rend la MAJ réversible
`src/forgemaster/service.py:74` · `src/forgemaster/service.py:82` · appelés par `_preflight_service` et par `install-service`
`<home>/current` est le seul chemin que l'unité systemd connaisse. Un lien plutôt qu'un venv en dur **parce que c'est
ce qui rend une MAJ réversible** : poser une version, c'est remplacer un lien ; revenir, c'est le remplacer dans
l'autre sens. `pose_stable_link` le pose vers `sys.prefix` de façon atomique et rend `None` quand on ne tourne pas
dans un venv portant la commande (checkout `python -m forgemaster`, install système) — cas où `update apply` refusera
honnêtement plutôt que de basculer un lien qui n'existe pas. Conséquence mesurée le 2026-08-06 sur VM : une
installation **fraîche** est donc déjà réversible, sans geste de migration préalable ; et le venv ainsi lié étant hors
de `<home>/venvs/`, il échappe à `_purge_venvs`.

## Zones non détaillées

- **`main()`** (`apply_update.py:359`) — point d'entrée du script figé : ouvre `journal.log`, route vers `apply` ou
  `rollback` selon `--mode`, et écrit `result.json` **quoi qu'il arrive** (une exception inattendue devient un verdict
  rc 2, jamais une trace nue). C'est ce fichier que `follow` attend.
- **`_parse`, `_write_json`, `_run`, `_free_port`, `_systemctl`, `_get_json`, `_identity`** (`apply_update.py:546`,
  `540`, `499`, `507`, `386`, `489`, `495`) — utilitaires stdlib sans décision propre. `_write_json` écrit par
  temporaire + `os.replace` (un verdict à moitié écrit ne doit pas se lire).
- **Le parser CLI** (`cli.py`, sous-commandes `update apply` / `update rollback`) : déclaration argposée, décrite dans
  `cli.md`. C'est `cli_dispatch` qui porte la logique.
- **La surface web/API du retour arrière** : elle n'existe pas encore. La découvrabilité du geste est portée par les
  messages de refus, jamais par de la prose — ce runbook est une carte pour qui développe, pas un manuel utilisateur.
