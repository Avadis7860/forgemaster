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
`src/forgemaster/update.py:101` · levée par `preflight`, `preflight_rollback`, `parse_exec_start` · rattrapée par `cli_dispatch`
L'instance est **intacte** quand cette exception sort : c'est ce que « fail-closed » veut dire ici. `cli_dispatch` la
rend en `✗ … refusé(e) — rien n'a été touché` et rc 1. Son pendant côté applicateur est `UpdateFailed`
(`apply_update.py:67`), qui signifie la même chose un cran plus bas : échec **arrêté avant la bascule**.

## preflight() — tout ce qui doit être vérifié avant que la moindre chose bouge
`src/forgemaster/update.py:105` · appelé par `cli_dispatch` · retourne le plan (chemins + URL de sonde) · lève `UpdateRefused`
Vérifie le wheel (existe, suffixe `.whl` — aucune résolution, aucun réseau : ce verbe ne pose que le fichier désigné),
délègue le socle de service à `_preflight_service`, puis refuse sur le travail non commité et sur un dispatch en cours.
Les **trois** connaissances extérieures — `authority` (verdicts par projet), `in_flight` (jobs `running`), `sessions`
(shells PTY vivants) — sont **calculées par l'appelant** et passées en argument (injection explicite) : ce module ne va
chercher ni connexion DB ni registre tout seul, et un préflight qui refuse ne doit pas avoir ouvert quoi que ce soit en
écriture. `sessions` n'est connu que du daemon (le registre vit sur `app.state`) : il ne bloque rien, il se **dit**.

## _preflight_service() — les cinq refus que l'aller ET le retour partagent
`src/forgemaster/update.py:129` · appelé par `preflight` et `preflight_rollback` · lève `UpdateRefused`
Cinq refus, tous explicites, jamais un devinage — et chacun nomme le geste qui débloque :

1. **pas de `systemd-run`** — sans lui l'applicateur ne peut pas sortir du cgroup de son lanceur, donc il se ferait
   tuer par l'arrêt qu'il émet lui-même (cf. `_echappement_cgroup`). Il est livré **avec** systemd, dont ce verbe
   dépend déjà : son absence signifie qu'il n'y avait de toute façon aucun service à piloter ;
2. **portée système sans être root** — `systemctl` échouerait en plein milieu, service arrêté. Refuser avant ;
3. **pas d'unité systemd** — la bascule exige un service gérable ; on n'invente pas une façon de redémarrer le
   forgemaster de quelqu'un ;
4. **pas de lien stable** — sans `<home>/current`, il n'y a rien à remplacer, donc rien à défaire ;
5. **une unité qui lance un venv EN DUR** — l'état de toute installation antérieure au bleu/vert. Elle n'est pas
   cassée, elle est *non migrée*, et la MAJ n'aurait **aucun effet** sur le service. Réécrire l'unité sous les pieds de
   l'utilisateur serait pire que refuser.

Extrait de `preflight` en écrivant `preflight_rollback` : dupliquer ces refus aurait produit deux jeux de
messages qui divergent, alors que c'est le même invariant de déploiement qui est en jeu. Le refus n°1 vit **ici** et
non dans `launch` pour une raison de vérité : au préflight, « rien n'a été touché » est exactement vrai — pas même un
dossier de run — et `--dry-run` le dit donc aussi.

## _refuse_uncommitted_work() — le refus d'autorité, porté par les DEUX gestes
`src/forgemaster/update.py:170` · appelé par `preflight` et `preflight_rollback` · consomme `projects.authority.blocking`
`projects_root` **n'entre pas dans l'instantané** : si le geste tourne mal, le travail qui n'est qu'là ne reviendra
pas. Le motif « git fait autorité » n'est vrai que là où il *y a* une autorité — le refus la vérifie au lieu de la
supposer. On ne bloque **que** sur du non-commité : « aucun remote » est un cas normal du produit distribué, et
refuser dessus interdirait toute mise à jour à qui n'en veut pas. Porté aussi par le retour depuis le 2026-08-06 —
revenir en arrière pendant qu'un travail non commité vit dans un worktree est exactement le geste à refuser.

## preflight_rollback() — le préflight du retour VOLONTAIRE, plus la résolution de la cible
`src/forgemaster/update.py:211` · appelé par `cli_dispatch` · retourne le plan (+ `snapshot`, `target_venv`) · lève `UpdateRefused`
Même socle de service, même refus d'autorité, plus la question propre au retour : **quel instantané remettre, et vers
quel venv rebasculer**. La correspondance instantané ↔ binaire se **dérive**, elle ne se stocke pas — aucun état
nouveau, aucune ligne de plus au manifeste, et le garde vaut aussi pour les instantanés *déjà pris*. Elle se dérive de
**deux** faits du même `result.json` : le run qui a pris cet instantané nomme le binaire qui tournait alors (c'est ce
qui **choisit**), et l'égalité de schéma reste la **garde**.
Sans référence explicite, ce parcours vit désormais chez `_resolution_cible` (ci-dessous) : ce préflight ne garde en
propre que la branche « instantané nommé explicitement ».

## _resolution_cible() — un seul parcours, deux lecteurs qui n'ont pas le droit de diverger
`src/forgemaster/update.py:255` · appelé par `preflight_rollback` et `aptitude` · retourne `(cible, venv, motif)`
On **parcourt** les candidats au lieu de prendre le premier `restaurable` : le plus récent est souvent l'instantané de
sûreté d'un retour déjà fait, qui ramènerait vers la version qu'on vient de quitter. Quand aucun ne convient, le motif
liste les refus un par un puis `_PISTES`, les trois gestes voisins — dont `update apply`, « le verbe qui va, lui, en
AVANT ».

Extrait de `preflight_rollback` le 2026-08-07 en écrivant `aptitude`, qui pose **exactement la même question sans
avoir le droit de lever**. Le laisser inline aurait produit deux parcours, donc deux réponses possibles à *« vers quoi
reviendrait-on ? »* — c'est le défaut que la 2a‴ a réparé entre `snapshot list` et le verbe, et qu'une troisième
marche ré-ouvrirait sans rien apporter. Le cas « aucun instantané du tout » sort de `_aucun_instantane`
(`src/forgemaster/update.py:280`), **une** phrase pour ses deux lecteurs — celui qui lève, celui qui rend un état.

## _cible_utilisable() — cinq façons de ne pas être un retour en arrière
`src/forgemaster/update.py:293` · appelé par `preflight_rollback` et `_resolution_cible` · retourne `(venv cible, motif de refus)`
Instantané invalide · état autre que `restaurable` · venv introuvable alors qu'il se dit restaurable (la liste et la
résolution ne voient pas le même disque : on ne devine pas) · **le venv déjà actif** · et la quatrième, révélée en
revue : une cible dont le binaire lit un schéma **supérieur** au courant — « son binaire lit le schéma 21 et le tien
lit le 20 : ce serait aller EN AVANT, pas revenir ». Un état dit ce qu'un artefact *peut* faire, jamais si c'est le
geste qu'on *demande* : la direction est une propriété du verbe, pas de la cible.

**La cinquième dit la même chose là où la quatrième est aveugle** : elle compare les schémas, donc elle est muette
quand rien n'a migré — le cas que le départage a rendu atteignable. Un instantané né d'un `rollback`
(`safety_of_rollback`, cf. `snapshot.md#_lecture_des_runs`) n'est jamais une cible : le viser ramènerait à l'état
d'avant CE retour, donc à la version qu'on vient de quitter. Mesuré **à la table en écrivant le correctif**, pas
supposé : sans elle, un `update rollback` rejoué après un retour non migrant repartait en avant. Il reste atteignable
par les deux gestes que `_PISTES` nomme (`snapshot restore` pour les données, `update apply` pour le binaire).

Le refus « déjà actif » nomme en plus, depuis le 2026-08-07, ce qu'il n'a **pas** pu départager : quand aucun journal
de MAJ ne nomme cet instantané (pris à la main, ou run effacé), on n'a que l'ordre par récence, et le dire évite un
refus qui aurait l'air arbitraire. Le fait vient de `snapshot.list_snapshots` (`named_by_run`), mesuré une fois pour
tous les instantanés plutôt que re-dérivé par cible.

## _venv_pour() — le binaire qui tournait quand l'instantané a été pris
`src/forgemaster/update.py:338` · appelé par `_cible_utilisable` · retourne le venv ou `None`
Le venv dont le forgemaster lit **exactement** le schéma de cet instantané (`restore.snapshot_schema`). Un binaire qui
lit plus loin remettrait les données puis migrerait la base en avant — l'état que `snapshot list` nomme `données
seules`, et qui n'est pas un retour arrière.

Le **choix** ne se fait pas ici : il vit chez `snapshot.venv_for_schema`, seul endroit qui énumère les candidats, les
ordonne et les **apparie**. L'instantané lui est passé, et pas seulement son schéma : après une MAJ **non migrante**
deux venvs lisent le même schéma, et le plus récent est justement celui qu'on cherche à quitter (défaut mesuré le
2026-08-07, cf. `runbooks/snapshot.md#_premier_du_schema--venv_for_schema`).

Cette marche parcourait `<home>/venvs` de son côté, comme `_restorability` du sien — et le troisième refus de
`_cible_utilisable` (« la liste et la résolution ne voient pas le même disque ») est précisément ce que ça produisait
quand les deux divergeaient. La liste partagée inclut le venv d'**origine**, hors `<home>/venvs` : sans lui, le premier
saut d'une install fraîche était sans retour (cf. `runbooks/snapshot.md#_venvs_candidats`).

## aptitude() — ce que l'instance sait faire, dit AVANT qu'on le demande
`src/forgemaster/update.py:377` · appelé par `aptitude_route` (`src/forgemaster/daemon/routes/update.py:106`) et `_cli_aptitude` (`src/forgemaster/update.py:1076`) · **ne lève jamais**
Rend `{deployable: {ok, reason}, reversible: {ok, reason, target}}`. Trois lectures partagent ce cœur — la route
(**200 toujours**), `forgemaster update aptitude` (**rc 0 toujours**), et le panneau **au repos** — et aucune n'a le
droit d'en avoir une variante : même règle que `_preflight_service`, extrait pour que l'aller et le retour ne refusent
pas avec deux jeux de messages qui divergent.

**La frontière que cette fonction pose, et qui est tout son objet** :

| | question | ce qui la change | où elle vit |
|---|---|---|---|
| **aptitude** | *cette instance sait-elle revenir ?* | un **acte** (`install-service`, une MAJ, un retour) | ici |
| **disponibilité** | *peut-elle le faire **maintenant** ?* | le **temps** (un dispatch finit, on commite) | les préflights |

Le travail non commité et les dispatches en vol n'entrent donc **pas** ici. Un worker qui tourne n'est pas « je ne
sais pas revenir », c'est « pas maintenant » — l'afficher au repos comme une aptitude serait un mensonge d'une autre
espèce, et il vieillirait en secondes sur une page qu'on ne relit pas. Le 409 de `/plan` reste leur endroit : il
prévisualise une **action**, au moment du geste.

**`reversible.ok` vaut `None` quand le socle refuse, jamais `False`.** C'est `_preflight_service` qui donne le venv
courant ; sans lui, rien n'a été **mesuré**, et « je n'ai pas pu mesurer » n'est pas « non ». Le module tient déjà cet
idiome ailleurs — `impact: null` sur un run sans verdict, l'état `unknown` de `run_state`, `restore.python_schema` qui
rend `None`. Conséquence de surface, qui est le vrai enjeu : le panneau affiche **un** refus, pas deux, donc **une**
réparation à chercher.

**Pas de champ `remedy`** : les cinq refus de `_preflight_service` nomment déjà la commande qui répare. Un second champ
obligerait à les ré-écrire ailleurs, donc à les laisser diverger. **Pas de numéro de schéma dans `target`** non plus :
il ne veut rien dire pour qui lit cette réponse dans un panneau, et le lire coûterait une sonde de plus.

**Ce qui n'est délibérément PAS fait** : aucune ligne d'aptitude dans `describe(apply)`. Après une MAJ, le venv actuel
*devient* la cible et un instantané est pris avant migration — sinon la MAJ refuse. La réponse serait « oui » toujours,
et du bruit dans la prévisualisation coûte l'attention qu'on veut pour les refus réels.

**Son coût, et pourquoi la surface ne la martèle pas** : `snapshot.venv_schemas` lance un interpréteur python **par
venv candidat** pour lire son schéma. Le hook web `useUpdateAptitude` n'a donc **aucun** `refetchInterval` — il relit
sur événement (un run qui atteint son verdict), seul moment où le produit lui-même a pu déplacer la réponse.

## parse_exec_start() — l'unité est la SEULE vérité sur le bind du service
`src/forgemaster/update.py:431` · appelé par `_preflight_service` · retourne `(binaire, host, port)` · lève `UpdateRefused`
Lit le dernier `ExecStart=` de l'unité et en tire le binaire, `--host` et `--port`. Déduire le bind de
`forgemaster.env` ou d'un défaut sonderait une **autre** instance que celle qu'on vient de redémarrer, et conclurait au
vert sur la mauvaise. Port illisible → refus : sans lui, aucune vérification en vivant, donc aucun retour arrière
automatique.

## describe() / describe_rollback() — ce qui va se passer, dit avant de le faire
`src/forgemaster/update.py:452` · `src/forgemaster/update.py:491` · appelés par `cli_dispatch` · seul contenu de `--dry-run`
`describe_rollback` nomme **les deux gestes et leur ordre** — c'est l'unité que le retour rend exécutoire, et la dire
ici est ce qui permet de refuser en connaissance de cause. Les deux ajoutent une ligne « hors instantané » pour les
projets qui n'ont pas bloqué : un projet sans remote n'est pas une faute, mais l'utilisateur doit savoir que sa seule
copie est là et qu'elle n'entre pas dans l'instantané. Ce qui n'a pas bloqué est **dit quand même**.

Depuis le 2026-08-06 les deux délèguent cette rubrique à `_a_savoir` (`update.py`), qui y ajoute les **shells du
terminal web** encore vivants : ils meurent avec le service qu'on arrête. Ils ne bloquent pas — un onglet ouvert n'est
pas du travail en cours, et refuser dessus rendrait la MAJ impossible à qui laisse un shell ouvert, c'est-à-dire à
tout le monde. **Asymétrie assumée** : le registre vit sur `app.state`, donc cette ligne n'apparaît que par la route.
La CLI est un autre processus, elle ne peut pas la dire — elle ne sait pas.

## _refuse_busy_dispatch() — le sixième refus : ne pas emporter un travail en cours
`src/forgemaster/update.py:188` · appelé par `preflight` et `preflight_rollback` · consomme `dispatch.jobs.running`
Un worker tourne, et le geste demandé **arrête le service qui le porte**. Le motif est mesuré, pas moral : l'arrêt tue
le worker in-process, puis `dispatch.reconcile.reconcile_orphans` le trouve `running` sans verdict au boot suivant, le
marque `killed`, et sa task retombe `todo`. Les jetons déjà dépensés sont perdus, et `record_finish` (gardé
`WHERE status='running'`) ne peut plus rien rattraper — `reconcile.py` documente ce footgun comme **déjà vécu**.

Décision B §1 (non-interruption fail-closed) : le consentement porte sur *appliquer la MAJ*, jamais sur *perdre le
travail en cours*. Le refus vit dans le **préflight partagé** et non dans la route, parce que la CLI arrête exactement
le même service : elle doit en hériter. La route n'apporte que la **connaissance** (`survey_in_flight`), pas la règle.

## spawn() — le cœur du lancement, sans une ligne de parole
`src/forgemaster/update.py:510` · appelé par `launch` et par `routes/update._lance` · rend `{run, unit, ok, detail}`
Crée `<home>/updates/<horodatage>/`, y **copie** `apply_update.py` sous le nom `apply.py`, écrit `run.json`, puis
lance le tout dans une **unité transitoire** (`_echappement_cgroup`) sous `_system_python()` — jamais le python du
venv qu'on remplace. Copié et non lancé depuis le paquet : le script doit survivre au venv qu'il remplace.

**Aucun `print`, aucune exception qui s'échappe.** Ce n'est pas de l'esthétique : le daemon appelle ce cœur depuis une
requête HTTP, où un `print` finirait dans le journal du service et une exception deviendrait un 500 nu. La spine est
le cœur déterministe ; la CLI et le daemon en sont deux **vues** (`launch` et `routes/update`).

**`run.json` porte l'INTENTION, et il est écrit avant l'effet** — avant même de savoir si le lancement partira. Le
`mode` ne se dérive de rien : `result.json` ne le porte pas et n'existe qu'à la fin ; le déduire de la prose de
`journal.log` serait un analyseur de phrases françaises. Ce qui ne se dérive pas s'écrit, une fois, à l'endroit qui le
sait — un run qui n'a jamais démarré garde donc quand même sa raison d'avoir existé. À ne pas confondre avec le
`venv_avant` écrit avant la bascule (angle mort connu, fiché côté vault) : celui-là est dans `apply_update`.

Le lancement passe par `subprocess.run` et **son code de retour est lu**. `systemd-run` rend la main dès l'unité
enregistrée, pas à la fin du travail : on peut donc distinguer « parti » de « refusé ». Avec le `Popen`
fire-and-forget d'avant, un applicateur qui ne partait pas passait pour un applicateur lent — `follow` attendait le
quart d'heure entier puis rendait « je ne sais pas », là où le système avait déjà répondu « non ». Le
`REGISTER_TIMEOUT` (30 s) borne l'aller-retour D-Bus **de l'enregistrement**, pas le travail : `Popen` ne bloquait
jamais, `run` si, et la route appelle d'ici — une requête qui ne rend jamais la main est pire qu'une requête qui
refuse.

`launch.log` n'est **pas** écrit ici : c'est l'unité qui y écrit (`StandardOutput=append:`). L'écraser avec le
« Running as unit… » de `systemd-run` effacerait précisément la trace qu'on garde. `mode` ne change que les arguments
de cible — même applicateur, même dossier de run, même journal, même `result.json`.

**Le dossier de run se crée en `exist_ok=False`** — l'horodatage est à la **seconde**, et depuis la route deux
requêtes peuvent y tomber ensemble (le handler est synchrone, donc servi par un fil du pool). Avec `exist_ok=True`, la
seconde écrasait le `run.json` de la première — l'intention d'un run **en vol**, perdue — avant d'échouer de toute
façon sur un nom d'unité déjà pris. La collision se **refuse** (`reason="collision"` → **409** côté route, et un chapô
de message distinct côté CLI : dire « systemd n'a pas enregistré » enverrait chercher la panne là où il n'y en a pas).

## launch() — la vue CLI : lancer, dire, suivre
`src/forgemaster/update.py:589` · appelé par `cli_dispatch` · retourne le rc de `follow` (ou 0 si `--detach`)
`spawn`, puis imprimer, puis suivre. **Les trois façons de ne pas partir rendent un rc, jamais une exception** :
lanceur disparu entre le préflight et ici, enregistrement refusé, gestionnaire muet. `launch` est appelé **hors** du
`try` de `cli_dispatch` — une `UpdateRefused` qui en sortirait deviendrait une trace nue, et son message « rien n'a
été touché » serait faux puisque le dossier de run existe.

## _echappement_cgroup() — pourquoi `setsid` ne suffisait pas, et pourquoi pas `KillMode`
`src/forgemaster/update.py:966` · appelé par `launch` · rend le préfixe `systemd-run` de la commande
**Le défaut, mesuré sur vrai systemd le 2026-08-06.** `Popen(start_new_session=True)` change la **session**, pas le
**cgroup**. Lancé par le daemon, l'applicateur restait dans le cgroup de `forgemaster.service` — que le
`systemctl stop` qu'il émet **lui-même** (`apply_update.py:191`) vide entièrement, `KillMode` valant `control-group`
par défaut. Conséquence constatée : service laissé à terre, `/api/version` muet, **`result.json` jamais écrit**, donc
aucun verdict à retrouver. Jamais rencontré avant parce que toutes nos preuves partaient d'un ssh, c'est-à-dire de
**dehors** — la propriété n'est pas observable hors d'un vrai cgroup systemd.

**Le remède.** L'applicateur devient sa propre unité transitoire, de la portée du service (`--user` ou non). Le nom
se **dérive** du dossier de run (`_unite_transitoire`) : un run et son unité ne peuvent pas diverger, et depuis un
dossier de run on sait quoi interroger sans mémoire externe. `--collect` efface l'unité à sa sortie, échec compris —
sinon un run raté occuperait son nom au suivant.

**Pourquoi pas `KillMode=process` sur l'unité du service** (l'autre échappement possible) : le daemon n'a pas qu'un
enfant. Les shells PTY (`terminal.pty`) et les workers de dispatch (`dispatch.worker`) vivent eux aussi dans son
cgroup, et `KillMode=process` les orphelinerait **tous**, à chaque arrêt, pour corriger ce seul cas. L'unité
transitoire ne change le sort que de l'applicateur, à l'endroit qui le concerne — son lanceur.

**Plancher, et ce qu'il vaut.** `StandardOutput=append:` exige systemd ≥ 240 ; le produit dépend déjà de systemd pour
tout le reste, et `systemd-run` est livré avec lui. Prouvé sur **systemd 255** (255.4-1ubuntu8.16, VM E2E 9311) — le
plancher est donc *déclaré*, pas *mesuré* : rien n'a été joué entre 240 et 255. Si une instance plus ancienne devait
être servie, c'est là qu'il faudrait aller voir, et le seul symptôme serait un `launch.log` vide.

**Ce que ce chemin est le seul à savoir faire.** Le lancement se lit désormais dans deux fichiers durables — `launch.log`
(écrit par l'unité) et `result.json` (écrit par l'applicateur). Leur absence conjointe distingue « le run n'a jamais
démarré » de « il tourne encore », ce que le fire-and-forget d'avant ne permettait pas. C'est le substrat que lit
`run_state`, donc la surface HTTP.

## run_state() / list_runs() / run_dir_for() — l'état se relit du DISQUE, jamais d'une mémoire
`src/forgemaster/update.py:666` · appelés par `routes/update` · lecture pure, aucun effet
**La contrainte qui décide de toute la forme** : le processus qui répond au `GET` d'après n'est ni celui qui a reçu le
`POST`, ni même le même binaire — la bascule est passée entre les deux. Un registre de tâches, un `BackgroundTasks`,
un dictionnaire sur `app.state` : tous rendraient « inconnu » exactement au moment où l'utilisateur attend son verdict.

Cinq états, tranchés **dans cet ordre** :

| lu sur le disque | état | ce que ça dit |
|---|---|---|
| `result.json` présent | `done` (rc 0) / `failed` | le verdict existe, avec son motif |
| sinon, **sans sonde** | `unknown` | verdict absent, et personne n'a demandé au gestionnaire |
| sinon, unité **active** | `running` | ça tourne encore, maintenant |
| sinon, `launch.log`/`journal.log` présents | `interrupted` | parti, jamais conclu |
| sinon | `never_started` | enregistré, jamais démarré — « rien n'a bougé » est exactement vrai |

`interrupted` est l'état que le fire-and-forget d'avant ne savait pas dire : il ne restait qu'un silence, impossible à
distinguer d'une attente. `unknown` en est le **garde-fou** : sans sonde on ne conclut pas à `interrupted`, ce serait
annoncer une mort qu'on n'a pas vérifiée. La sonde `is_active` est **injectée** (invariant : toute I/O l'est) ; en
production c'est `systemd_is_active` → `systemctl [--user] is-active <unité dérivée du run>`. `--collect` efface
l'unité à sa sortie, donc `systemctl` ne distingue pas « terminée » d'« introuvable » — sans conséquence : elle n'est
interrogée qu'**après** que `result.json` a été jugé absent, elle ne sert donc qu'à séparer `running` d'`interrupted`.

`list_runs` **dit sa borne** (`total` + `truncated`, `MAX_RUNS` = 50) : invariant « jamais de cap silencieux ». La
rétention de `<home>/updates` elle-même n'existe pas encore (fichée côté vault). Et il ne dépense **qu'une seule
sonde** systemd pour toute la liste, sur le run sans verdict le plus récent : en sonder chacun coûterait jusqu'à 50
allers-retours au gestionnaire sur une vue de liste, et ce plafond tomberait sur une requête HTTP le jour où ce
gestionnaire est coincé — c'est-à-dire le jour où l'on regarde justement cette page. Les autres rendent `unknown`, et
leur adresse propre sonde, elle.

Elle rend enfin `follow_timeout` (= `FOLLOW_TIMEOUT`, 900 s), la borne au-delà de laquelle le produit **lui-même**
cesse d'attendre un run. Une surface qui dit « elle aurait dû revenir » a besoin de ce chiffre ; le recopier chez elle
le ferait diverger le jour où il bouge. Règle uniforme avec `keep`/`max_bytes` : **une borne annoncée vient de la
borne qui s'applique**.

**Le verdict dit ce qui s'est passé, `impact` dit jusqu'où ça a été.** Les deux ne se déduisent pas l'un de l'autre :
« MAJ refusée — le vivant ne sert pas » ne renseigne pas sur l'état du service, et c'est exactement la question de
quelqu'un qui n'a pas de terminal pour aller voir. `apply_update` écrit la phrase (`src/forgemaster/apply_update.py:181`
et ses trois issues) ; `run_state` la propage telle quelle. Elle n'existe **qu'avec** un verdict : partout ailleurs
`impact` vaut `null`, qui se lit « je n'en sais rien » — la rendre à vide se lirait « rien n'a bougé », la pire des
réponses pour un run parti et jamais conclu.

`runs_dir` est la seule adresse du dossier (`<home>/updates`) : trois marches le nommaient déjà séparément, et une
adresse recopiée trois fois est une adresse qui divergera. `run_dir_for` pose **deux gardes**, parce qu'aucune ne
suffit seule : la FORME (`RUN_ID_RE` — un identifiant qui
arrive du réseau n'est pas un nom de dossier) puis le CONFINEMENT du chemin résolu sous `<home>/updates` (une forme
valide peut être un lien symbolique posé là par autre chose). Les deux rendent le même `KeyError` → 404 : distinguer
« mal formé » d'« absent » renseignerait sur ce qui existe.

## stage_wheel() / list_wheels() / prune_wheels() — l'aire de dépôt, et sa politique déclarée
`src/forgemaster/update.py:778` · appelés par `routes/update` et `cli_dispatch` · écriture bornée, purge à l'écriture

**Pourquoi cette aire existe.** `preflight` ne pose que le fichier qu'on lui **désigne** — la CLI a un système de
fichiers, elle passe un chemin. **HTTP n'en a pas** : un utilisateur distribué a son wheel dans son navigateur, pas
sur le disque de son instance. L'aire est exactement ce chaînon-là, et rien de plus : recevoir, borner, ranger. Le
`path` rendu se repasse tel quel à `GET /plan` puis `POST /apply`.

`wheels_dir` est la seule adresse de l'aire (`<home>/wheels`), pour la même raison que `runs_dir` : une adresse
recopiée par chaque marche est une adresse qui divergera.

**Ce qu'elle n'est pas.** `apply` **n'est pas confiné** aux artefacts déposés ici, et c'est délibéré : le canal servi
(phase 5) fera arriver un wheel ailleurs, et confiner aujourd'hui obligerait à ré-ouvrir demain. Le dépôt **ajoute une
source**, il ne devient pas la seule.

**Quatre gardes, et l'ordre porte du sens** — nom nu (400) · extension `.whl` (415) · confinement du chemin résolu
(400) · taille **pendant** le flux (413). Un artefact hostile de 100 Mo visant `../` doit être refusé **pour son
nom** : le rejeter pour son poids laisserait croire qu'un plus petit passerait. Les trois exceptions sont celles de
`content.upload` (`UploadRejected`, `UploadTypeRejected`, `UploadTooLarge`) — les handlers globaux du daemon les
mappent déjà 400/415/413 par la MRO. On reprend la **couture** du patron d'upload, pas la pièce :
`write_project_upload` écrit sous `docs/design/`, ce n'est pas le même sujet.

**Écriture atomique** (`.part` + `os.replace`) et **rien de tronqué ne survit** : tout échec efface le dossier de
dépôt entier. Un demi-wheel sur le disque serait la pire des traces, parce qu'il ressemble à un wheel. Deux dépôts
dans la même seconde → `UpdateRefused` (409), même contrat que `spawn` et pour la même raison : l'horodatage est à la
seconde, le handler est servi par un fil du pool.

**La rétention est déclarée, pas subie.** `KEEP_WHEELS` = 3, appliqué **à l'écriture** (une politique qui dépend d'un
minuteur ne tient pas sur une instance qu'on éteint), et la réponse du `POST` **dit** ce qu'elle a purgé — une purge
muette, c'est un cap silencieux avec un autre nom. `KEEP_WHEELS` ne dérive **pas** de `ROLLBACK_DEPTH` : un wheel
déposé n'est pas un barreau de l'échelle de retour, une fois posé c'est le **venv** qui porte le binaire. Et
`prune_wheels` **épargne** tout dépôt qu'un run **sans verdict** nomme (`wheels_in_use`) — une rétention qui ignore
ses références casse ce qu'elle croit ranger : purger le wheel d'un run en vol le ferait échouer en plein
`pip install`. Un dépôt épargné ne décale pas la fenêtre, il s'ajoute à ce qui reste.

`list_wheels` trie par **nom** (le nom EST l'horodatage), jamais par mtime — qu'une copie ou une restauration réécrit.
`size` et `sha256` sont ceux **mesurés à la réception** (`deposit.json`), pas recalculés à la lecture : c'est une
provenance de dépôt, et la relire à chaque affichage coûterait une relecture complète de l'aire. Un dossier posé à la
main n'en a pas — il est listé quand même, avec `sha256: null`, parce que « je n'ai pas mesuré » n'est pas « il n'y a
rien ».

Il rend les **deux** bornes qui régissent l'aire, pas seulement la rétention : `keep` (combien de dépôts survivent) et
`max_bytes` (lequel est refusé à l'entrée). Une surface qui n'obtiendrait que la première devrait ré-écrire la seconde
à la main — et un chiffre recopié dérive en silence le jour où la borne bouge. C'est la leçon que le banc de la 3a·3a a
déjà apprise en épinglant `3` au lieu de lire `keep` : ce qui se mesure est « la borne déclarée est celle qui
s'applique », jamais « elle vaut 3 ».

**Asymétrie CLI/HTTP, voulue.** On **dépose** par la route, jamais en CLI : l'aire existe parce que HTTP n'a pas de
système de fichiers, et `--wheel <chemin>` suffit déjà à qui en a un. Ce que la CLI gagne est une **vue en lecture**,
`forgemaster update wheels` (`_cli_wheels`, `src/forgemaster/update.py:1103`) — parce qu'une politique de rétention qui
n'existe que dans le code n'est pas une politique déclarée.

## make_update_router() — la surface HTTP, et pourquoi elle est plus étroite que la CLI
`src/forgemaster/daemon/routes/update.py:69` · monté par `daemon.app.build_app` · préfixe `/api/update`
`GET /plan` (préflight + `describe`, **idempotent** — aucun dossier de run créé) · `POST /apply` · `POST /rollback`
(**202** `{run, unit, mode, state}`) · `GET /runs` · `GET /runs/{id}` · `POST /wheels` (multipart, **201**) ·
`GET /wheels` · `GET /aptitude`.

**`GET /aptitude` rend 200 même quand tout refuse, et c'est sa propriété entière.** Un refus d'aptitude est un **état** :
la requête est bien formée, l'instance répond, et sa réponse est « non ». Le 409 reste celui de `/plan`, qui
prévisualise une **action** — là, refuser veut dire « ce que tu demandes n'aura pas lieu ». Rendre 409 ici obligerait
chaque lecteur à traiter un état normal du produit comme une panne, et la surface qui l'affiche **au repos** ne pourrait
plus distinguer « je ne sais pas revenir » de « je n'ai pas pu te répondre » — précisément l'écart que
`web/src/lib/updateLiaison` existe pour tenir.

**Le dépôt se lit en FLUX, et le handler est synchrone pour ça.** `POST /wheels` ne fait pas `await file.read()` :
le patron d'origine (`projects.upload_to_project`) matérialise toute la part avant de la borner, ce qui est tenable
sous un cap de 10 Mo et ne l'est pas sur le daemon qui s'apprête à se remplacer lui-même. Un handler `def` (joué dans
le pool de fils) lit `file.file` — le fichier spoolé, rembobiné par le parseur — morceau par morceau, sans générateur
asynchrone à recoller à un cœur déterministe. **Ce que le flux n'achète pas, et il faut le dire** : le parseur
multipart a déjà déversé la part sur le disque avant que ce handler soit appelé. C'est un autre défaut, fiché côté
vault, pas maquillé.

**Le POST exécute, il ne prévisualise pas.** La prévisualisation d'un geste mutant est un `GET` idempotent — même
doctrine que `GET .../git/sync` avant `POST .../git/sync/reconcile` (`docs/schema-contract.md` §3). Un `dry_run` dans
le corps d'un `POST` obligerait à faire confiance à un drapeau pour ne rien casser.

**Ni `unit`, ni `systemctl`, ni `service` dans le corps** — c'est tout ce que disent `ApplyRequest` (`wheel`, `scope`)
et `RollbackRequest` (`snapshot`, `scope`), et c'est délibéré : ce sont des points d'injection pour un test ou pour un
opérateur devant un terminal, pas des choses qu'on accepte du réseau. Les deux corps sont `extra="forbid"` — déviation
assumée de la permissivité par défaut de pydantic, et le motif leur est propre : sur la route la plus puissante du
produit, un champ **ignoré en silence** se lit « honoré » par qui l'a écrit. Un **422** dit la vérité. L'unité est celle de la portée ; `scope` vaut
`user` par défaut — mesuré, un daemon non privilégié ne peut pas piloter une unité système (polkit refuse), c'est donc
le seul chemin par lequel une MAJ depuis le produit existe.

**Sept codes, sept sens.** **201** : l'artefact est rangé, avec ce qu'on a mesuré en le recevant. **400** / **415** /
**413** : les trois refus du dépôt — nom invalide, type hors `.whl`, au-delà de la borne — trois corrections
différentes pour l'utilisateur, pas trois façons de dire non. **409** : l'instance refuse dans son état — les six refus voyagent avec leur texte
intégral, jamais un « impossible » nu ; et la **collision d'horodatage**, conflit transitoire où rien n'a été touché.
**422** : un champ inconnu dans le corps (cf. `extra="forbid"`). **503** : `systemd-run` n'a pas enregistré l'unité — ce n'est *pas* un refus
(l'instance était d'accord), c'est une machinerie indisponible, et l'identifiant du run voyage quand même pour que la
trace soit trouvable. **404** : run inconnu ou identifiant hors forme.

**Posture d'authentification, énoncée et non rencontrée.** Ce daemon n'authentifie **aucun** appelant (CORS seul, bind
`127.0.0.1`) et cette route remplace le binaire de l'instance : c'est la plus puissante du produit. Elle n'ajoute pas
ce pouvoir — la CLI pose déjà un wheel arbitraire — elle l'expose par un second chemin sur la même boucle locale. Ce
qu'elle ajoute est d'être atteignable depuis un navigateur, donc la seule barrière est le CORS : un corps JSON force
un préflight, que l'allow-list refuse pour toute origine tierce (**mesuré**, `tests/test_update_route.py`, avec son
contre-témoin sur l'origine du dev). Le jour où l'instance écoutera autre chose que la boucle locale, cette route est
la première à devoir une authentification.

## follow() — détaché ne veut pas dire aveugle
`src/forgemaster/update.py:615` · appelé par `launch` · retourne le rc lu dans `result.json`
Suit `journal.log` en flux jusqu'à ce que `result.json` apparaisse. Un suivi interrompu (délai `FOLLOW_TIMEOUT`,
`update.py:51`, 15 min) ne conclut **pas** à l'échec : il dit où regarder, et le script continue. La supervision se
fait par **fichier de verdict**, pas par le tuyau ssh — c'est ce qui la rend valable depuis un banc distant.

## survey_authority() / survey_in_flight() — dégrader honnêtement plutôt que bloquer sur ce qu'on ignore
`src/forgemaster/update.py:1001` · appelés par `cli_dispatch` et par `routes/update._plan` · retournent les verdicts, ou `[]`
N'ouvrent la base **que si elle existe déjà** : un préflight qui refuse ne doit pas avoir créé la base de son refus.
Une base d'un schéma qu'on ne lit pas rend « je ne sais pas » — ce module ne bloque que sur ce qu'il **sait**. Un refus
qui se déclencherait sur une base illisible interdirait la MAJ des instances qu'il faut justement mettre à jour.

Remontées hors de l'en-tête « CLI » le 2026-08-06, et renommées en public à cette occasion : le daemon les appelle
aussi, et une fonction que deux vues partagent n'est pas un détail d'implémentation de l'une d'elles.

## cli_dispatch() — `apply` et `rollback` suivent la MÊME séquence
`src/forgemaster/update.py:1044` · appelé par `cli._h_update` (routé par `_HANDLERS`) · retourne le code de sortie
Préflight qui refuse avant tout effet → description → (`--dry-run` : on s'arrête là) → lancement détaché. La symétrie
est structurelle et pas cosmétique : c'est elle qui garantit qu'on ne découvre pas le chemin du retour le jour où il
compte.

`wheels` et `aptitude` sortent avant cette séquence : ce sont des **lectures**, pas des gestes, et toutes deux rendent
**toujours 0**. Un rc non nul dit « le geste demandé n'a pas eu lieu » ; il n'y a pas de geste dans une question, et
`_cli_aptitude` est la parité exacte du 200 de la route. Il marque la réversibilité non mesurée d'un `?` et non d'un
`✗` — une croix ferait lire un second refus là où il n'y en a qu'un.

---

## build_blue() — le venv neuf, à côté, jamais en place
`src/forgemaster/apply_update.py:82` · appelé par `apply` · retourne le chemin du `forgemaster` neuf · lève `UpdateFailed`
Étapes [1/6] et [2/6]. À côté parce qu'un processus ne remplace pas le wheel qu'il exécute, et surtout parce que
l'ancien venv doit rester **intact** pour pouvoir y revenir. Un wheel qui ne pose pas de commande `forgemaster` est
refusé ici : ce n'est pas un wheel de forgemaster.

## probe_isolated() — ce qu'elle prouve, et ce qu'elle ne prouve pas
`src/forgemaster/apply_update.py:97` · appelé par `apply` (étape 3/6) et `rollback` (étape 1/5) · retourne l'identité de build
Fait servir la version sur un port libre et un `FORGEMASTER_HOME` **jetable**, puis lit `/api/version` **et charge la
page dans un vrai navigateur** (cf. section suivante). Elle prouve que *le wheel démarre, sert et s'affiche* ; elle ne
prouve **pas** que ta configuration et ta base tiennent — son home est vierge. C'est la vérification en vivant qui couvre ça, et son échec est précisément ce qui déclenche le retour. Le
wheel est **sa propre référence** : l'identité relevée ici devient l'attendu du vivant, sans manifeste ni signature.
Côté `rollback` elle a un **double effet** : elle produit l'attendu final *et* prouve que l'ancien binaire sert encore
— s'il ne servait plus, revenir vers lui n'aurait aucun sens, et rien n'a bougé.

## check_ui() / render_ok() / verificateur() — le détecteur de panne : la page rend-elle ?
`src/forgemaster/apply_update.py:317` · appelé par `probe_isolated` et `_verify_live` · retourne `(état, phrase)`
`/health` 200 + le bon SHA sont de la **plomberie** : un wheel dont la SPA est cassée les satisfait tous les deux et
sert une page blanche. Depuis le 2026-08-07, les deux vérifications chargent la page dans Chromium et exigent que le
wheel y rende les marqueurs **qu'il déclare lui-même**.

**Le contrat voyage avec le wheel** (`src/forgemaster/_ui_contract.json`, lu par `ui_contract` dans le paquet que
`package_dir` fait dire au python du venv sondé — on ne compose jamais `lib/python3.X/site-packages` à la main), jamais
en dur dans le script : `spawn` copie l'`apply_update.py` de la version **installée**, donc c'est le **vieux**
applicateur qui juge le **nouveau** wheel — un libellé épinglé dans le script ferait échouer toute MAJ qui le renomme.
Le lockstep entre ce contrat et le shell (`web/src/App.tsx`, `web/src/components/ProjectRail.tsx`) est tenu par des
tests, pas par la vigilance — et ils sont **nécessaires sans être suffisants** : deux faux-verts sont passés au travers
le 2026-08-07 et n'ont été vus que sur VM. **Un marqueur doit être ce que le navigateur REND** : le `<title>` est servi
par l'`index.html` statique (il survit à une SPA qui n'a jamais monté), et `innerText` rend le texte **transformé par
CSS** (un libellé sous `uppercase` n'arrive jamais tel qu'il est écrit).

**Ce que `describe` annonce** vient d'`annonce_verification` (posé au plan par `_preflight_service`) : le plan dit
avant le geste si l'interface pourra être regardée, et **ce qui se passera sinon**. C'est ce qui met la phrase sous les
yeux de qui clique dans le panneau `/settings`, sans une ligne de front.

**Le juge vient de l'hôte, jamais du wheel qu'il juge.** `verificateur` reprend **exactement** la cascade de
`gate.verify.runner_path` (`$FORGEMASTER_VERIFY_RUNNER` → `<home>/runners/render_check.js`) : un seul override pour les
deux usages. Le runner **embarqué dans le wheel** (`forgemaster/_verify_runner/`) a été écrit comme repli puis
**écarté sur mesure** : `deploy/runners/node_modules` est gitignoré, donc un wheel bâti depuis un checkout propre
n'emporte que le `.js` et son `package.json` — ce fichier ne s'exécute pas, `require('playwright-core')` lève. Le
retenir aurait transformé une **dégradation annoncée** en **MAJ refusée**. Même complété il resterait inutile sans le
Chromium de ~150 Mo, qui n'est dans aucun wheel : le runner embarqué est une **source**, que `provision-ct.sh` tire
pour semer `<home>/runners`, pas un exécutable. De là vient aussi l'exigence de `node_modules/playwright-core` **à
côté** du runner : un runner sans sa dépendance n'est pas un runner, et le compter présent ferait planter le juge.

**Trois états, et la frontière entre les deux premiers est toute la doctrine.** `vu` : la page rend. `non-mesuré` : pas
de Node, pas de runner, ou pas de contrat → on retombe sur `/health` + SHA **et on le dit** (au plan avant le geste,
au verdict après). `raté` : la page ne rend pas, **ou** le juge était là et n'a pas pu juger (plantage, délai, verdict
illisible) — jamais blanchi. Les six refus de ce module sont réservés à ce qui **casserait** ; un juge absent ne casse
rien, il rend moins sûr, et ce dépôt **déclare** ce qu'il n'a pas pu observer (`comparable=false`, `run_state: unknown`,
`impact: null`) au lieu de bloquer dessus. Exiger ~150 Mo de Chromium pour toute MAJ contredirait par ailleurs la
promesse turnkey « l'utilisateur final n'installe que Python ».

**Deux endroits, deux questions.** En isolation le home est vierge : ce qui est jugé est le **build**, et un refus y
coûte un venv jetable au lieu d'un arrêt de service, d'un instantané et d'un retour arrière. En vivant c'est la
**rencontre** du binaire et des données réelles — une interface qui ne tombe que sur la vraie base ne se voit que là,
et ce `False` déclenche le retour arrière automatique sans une ligne neuve. Côté `rollback`, l'absence de contrat sur
la cible **ne bloque jamais** : *on exige la preuve pour avancer, jamais pour revenir*.

Chaque passage archive sa capture (`ui-isolation.png`, `ui-live.png`) dans le dossier du run — une preuve durable,
relisable après coup.

## take_snapshot() — pris à FROID, par le forgemaster ANCIEN
`src/forgemaster/apply_update.py:140` · appelé par `apply` (4/6) et `rollback` (2/5) · retourne le dossier · lève `UpdateFailed`
Service arrêté, juste avant la bascule. Par l'**ancien** binaire (`<venv courant>/bin/forgemaster snapshot create`) et
jamais réimplémenté ici : deux implémentations, c'est une seule testée. Il protège de la **migration avant** — la
nouvelle version migre la base à sa première ouverture, et la base monte en forward-only. Un ancien forgemaster qui ne
connaît pas le verbe `snapshot` fait échouer la MAJ **ici, avant la bascule** : on ne bascule jamais sur une version
dont on ne saurait pas revenir. `--home` est porté par la sous-commande et non par la racine — constaté par
l'acceptance, pas par relecture.

## swap() — le remplacement atomique du lien
`src/forgemaster/apply_update.py:164` · appelé par `apply`, `rollback` · aucun retour
`symlink` sur un temporaire puis `os.replace`. Jamais `unlink` puis `symlink` : entre les deux, l'unité systemd
pointerait le vide. Le lien qu'il remplace est celui que `service.stable_link` nomme et que `service.pose_stable_link`
pose à l'installation (§ ci-dessous).

## matches() — un build non tamponné se DIT, il ne se conclut pas au vert
`src/forgemaster/apply_update.py:174` · appelé par `_verify_live` · **fonction pure** · retourne `(verdict, motif)`
Compare version puis SHA de build. Quand l'un des deux SHA est absent (checkout éditable, wheel sans tampon), la
comparaison est **impossible** — elle le dit et ne vérifie que la version, au lieu de faire passer une absence pour une
égalité.

## apply() — le geste complet, trois issues toutes explicites
`src/forgemaster/apply_update.py:359` · appelé par `main` · retourne `(rc, verdict, détails)`
Six étapes : venv neuf → sonde en isolation → **arrêt + instantané à froid** → bascule du lien + redémarrage →
vérification en vivant → retour arrière automatique si elle échoue. Trois issues : **posée** (rc 0), **refusée avant
bascule** (rc 1, rien n'a bougé), **revenue en arrière** (rc 1, l'instance est telle qu'avant). Le retour automatique
rebascule le lien puis restaure ; si la restauration échoue, il **re-bascule EN AVANT** (rc 2) — le binaire neuf sait
lire ces données-là, et un binaire ancien sur des données neuves est le seul état interdit.

## rollback() — le symétrique de l'aller, pas un second mécanisme
`src/forgemaster/apply_update.py:425` · appelé par `main` · retourne `(rc, verdict, détails)`
Cinq étapes, chacune réutilisant une fonction que `apply` exerce déjà. **L'ordre est contraint et non négociable : le
lien D'ABORD, la restauration ENSUITE** — `restore` interroge `<home>/current` pour savoir quel schéma le binaire en
place sait lire ; inversé, il verrait le binaire neuf et refuserait une restauration pourtant légitime.
`tests/test_rollback.py` lit cet ordre **dans le code** (AST) : le risque n'est pas l'appel d'aujourd'hui, c'est la
simplification de demain. Chaque moitié qui échoue est compensée par son appelant — bascule ratée → **aucune**
restauration tentée ; restauration ratée → re-bascule sur le courant ; retour du retour → rien à compenser (le lien
est déjà sur le binaire le plus haut), mais l'échec se **dit**. Un retour arrière qui ne partage pas le code de
l'aller est un chemin qu'on ne joue qu'en catastrophe, donc jamais joué pour de vrai avant le jour où il compte.

## _refuse_if_target_would_be_purged() — la prise de sûreté peut détruire sa propre cible
`src/forgemaster/apply_update.py:531` · appelé par `rollback` (étape 1/5) · lève `UpdateFailed`
Prendre l'instantané de sûreté consomme un cran de rétention et déclenche la purge. On refuse **avant le premier
geste** plutôt que de le découvrir entre les deux moitiés : un refus coûte une relance, une cible détruite ne se
rattrape pas. La cible survit si, et seulement si, moins de `KEEP_SNAPSHOTS - 1` instantanés complets lui sont
postérieurs. L'étape [3/5] **re-vérifie** ensuite l'existence de la cible, la prise ayant eu lieu entre-temps.

## _restore() — rendre `False` plutôt qu'avaler l'échec
`src/forgemaster/apply_update.py:589` · appelé par `apply` et `rollback` · retourne un booléen
Restaure par le script **figé dans l'instantané** — celui écrit en même temps que son manifeste, donc celui qui le
comprend ; c'est aussi le chemin de secours manuel, donc on exerce ici ce qu'on documente. Elle loggait un `⚠` et
rendait `None` : ses trois appelants concluaient « restauré » sur un échec et laissaient l'unique moitié que
l'invariant interdit. Le correctif n'est pas un message de plus, c'est un **type de retour** — la seule forme qu'un
appelant ne peut pas ignorer. Le drapeau `FORCE_FLAG` (`apply_update.py:66`) n'est passé que si `_supports_flag`
(`apply_update.py:422`) le trouve dans le texte du script figé : un instantané pris avant ce garde embarque un
`restore.py` dont l'argparse sortirait en usage, et ferait échouer le retour au pire moment. Ce n'est pas un
affaiblissement du garde de compatibilité — le lien vient d'être rebasculé sur le venv qui a **pris** cet instantané,
donc le seul état que le garde pourrait rendre est *indéterminable* ; le refus d'une incompatibilité **constatée**
reste absolu.

## _verify_live() / _wait_health() — trois issues, pas deux
`src/forgemaster/apply_update.py:628` · `src/forgemaster/apply_update.py:650` · appelés par `apply`, `rollback`, `probe_isolated`
`200` l'instance sert · **`503` portant `ready:false`** échec **immédiat, avec son motif** · rien d'autre → on attend.
Attendre la fin du délai pour conclure « ne répond pas » transformerait une réponse claire en silence, et c'est ce
silence qui remonterait à l'utilisateur comme diagnostic. `_unservable_detail` (`apply_update.py:681`) exige la forme
complète : un 503 de proxy ou d'un autre service sur ce port ne doit pas se faire passer pour un verdict de
l'instance. Si le processus sondé meurt, on n'attend pas non plus — un échec rapide vaut mieux qu'un long silence.

## ROLLBACK_DEPTH — la politique de rétention, déclarée UNE fois
`src/forgemaster/apply_update.py:58` · lu par `_purge_venvs` et par `snapshot.KEEP`
`KEEP_VENVS = DEPTH + 1` (le courant + les crans joignables) et `KEEP_SNAPSHOTS = DEPTH + 2` (la marge « échoué,
restauré, retenté, re-échoué ») en **dérivent**. Déclarée ici parce que ce module est stdlib-pur par contrat : c'est
`snapshot.py` qui lit la constante, l'inverse serait impossible. Ce qui change n'est pas le disque consommé — les
valeurs effectives sont inchangées — mais que « jusqu'où on sait revenir » devienne un **nombre nommé**, sur lequel les
deux rétentions s'accordent au lieu de coïncider.

## _purge_venvs() — une garde qui ne sait pas répondre se tait
`src/forgemaster/apply_update.py:722` · appelé par `apply` (seulement après un vivant vert) · aucun retour
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

- **`main()`** (`apply_update.py:555`) — point d'entrée du script figé : ouvre `journal.log`, route vers `apply` ou
  `rollback` selon `--mode`, et écrit `result.json` **quoi qu'il arrive** (une exception inattendue devient un verdict
  rc 2, jamais une trace nue). C'est ce fichier que `follow` attend.
- **`_parse`, `_write_json`, `_run`, `_free_port`, `_systemctl`, `_get_json`, `_identity`** (`apply_update.py:546`,
  `540`, `499`, `507`, `386`, `489`, `495`) — utilitaires stdlib sans décision propre. `_write_json` écrit par
  temporaire + `os.replace` (un verdict à moitié écrit ne doit pas se lire).
- **Le parser CLI** (`cli.py`, sous-commandes `update apply` / `update rollback`) : déclaration argposée, décrite dans
  `cli.md`. C'est `cli_dispatch` qui porte la logique.
- **La surface web/API du retour arrière** : elle n'existe pas encore. La découvrabilité du geste est portée par les
  messages de refus, jamais par de la prose — ce runbook est une carte pour qui développe, pas un manuel utilisateur.
