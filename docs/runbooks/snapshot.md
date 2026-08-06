# snapshot — runbook (le substrat du retour arrière : prendre un instantané, dire ce qu'il vaut, le remettre)

La base du forgemaster monte en **forward-only** : aucune down-migration n'est écrite et il n'en sera pas écrit. Le
retour arrière d'une MAJ n'est donc pas une migration inverse, c'est la **restauration** d'un instantané pris juste
avant. Sans cette capacité le produit n'a aucun retour arrière du tout — d'où ces deux modules.

Ils sont **la porte de secours**, et leur forme le dit : `restore.py` est **stdlib pure, zéro import
`forgemaster`**, et `db.md` note qu'il est l'un des rares chemins qui n'ouvrent pas la base. Il doit tourner avec le
`python3` du système sur une instance dont le venv est cassé et dont la base ne s'ouvre plus — précisément la
situation où on restaure. C'est aussi pourquoi il est **l'unique implémentation** : `forgemaster snapshot restore` le
*lance*, ne le refait pas. Deux implémentations, c'est une seule testée, et fatalement la mauvaise le jour où ça
compte.

Trois invariants portent le reste. **Le périmètre est déclaré, jamais inféré d'un `cp -r`** — et il voyage dans le
manifeste, pour que la restauration puisse dire ce qu'elle ne remettra pas. **`manifest.json` est écrit en dernier** :
un dossier sans manifeste est un instantané incomplet, donc invalide, et il doit se lire comme tel plutôt que se
restaurer à moitié. **Rien n'est détruit, tout est mis de côté** : restaurer le mauvais instantané reste une erreur
rattrapable, et une erreur rattrapable est ce qui rend le geste praticable par quelqu'un qui doute.

Le cycle qui pilote ce substrat (poser un wheel, basculer le lien, revenir) a son propre runbook : `update.md`.

## ENTRIES / EXCLUDED_IN_HOME — le périmètre, déclaré et embarqué
`src/forgemaster/snapshot.py:56` · `src/forgemaster/snapshot.py:71` · lus par `create` et inscrits au manifeste
Trois entrées prises : la base (`forgemaster.db`, par `VACUUM INTO`), le réglage de l'instance
(`forgemaster.env`, mode 0600) et le blob chiffré des credentials (`secrets/store.enc` — sans lui, ils sont perdus).
Une exclusion tue se fait « compléter » par réflexe de symétrie à la session suivante : chacune voyage donc **dans le
manifeste**, avec son motif écrit dans le code — `master.key` (l'embarquer produirait un artefact qui, copié
ailleurs, déchiffre tout), `logs/` (historique append-only, et le copier avant chaque MAJ la rendrait assez lente
pour qu'on la saute), `snapshots/` (sinon N+1 embarque les N précédents : croissance quadratique), `restore.py`
(outillage re-posé à chaque prise, pas de l'état), et surtout **`venvs/`, `current`, `updates/`** : du **code**, pas
de l'état utilisateur. Un instantané couvre la donnée, jamais le binaire — c'est exactement pour ça que le retour
arrière demande **deux** gestes. Le dire ici rend la frontière lisible dans l'artefact lui-même, au lieu de la
laisser dans une doc que personne n'aura sous la main ce jour-là.

## create() — la prise, et la purge seulement APRÈS
`src/forgemaster/snapshot.py:82` · appelé par `cli_dispatch` · retourne le dossier
Le cycle de MAJ ne l'appelle **pas** en Python : `apply_update.take_snapshot` lance le verbe `snapshot create` de
l'**ancien** binaire dans un sous-processus, parce qu'il est stdlib-pur et ne peut rien importer d'ici — et parce que
c'est l'ancien forgemaster qui sait prendre l'état de l'ancien forgemaster.
Parcourt `ENTRIES`, prend chacune selon son `via`, pose `restore.py`, écrit le manifeste, **puis** purge. Un échec en
cours de prise ne doit jamais détruire un instantané antérieur encore bon. Trois sorts distincts et tous explicites :
une entrée **prise** (`entries`), **absente à la prise** (`absent` — l'instance ne l'avait pas), **délibérément hors
périmètre** (`excluded`). On ne découvre pas une exclusion en constatant un manque.

## _vacuum_into() — `VACUUM INTO`, jamais un `cp`
`src/forgemaster/snapshot.py:117` · appelé par `create`
`db/store.py` pose `PRAGMA journal_mode = WAL`. La doc SQLite est explicite : *« if a database file is separated from
its WAL file, then transactions that were previously committed to the database might be lost, or the database file
might become corrupted »*. `VACUUM INTO` rend au contraire *« a consistent snapshot of the original database »* en
**un seul fichier**, et *« VACUUM (but not VACUUM INTO) is a write operation »* — aucun verrou d'écriture pris sur le
vivant. La connexion n'ajuste **aucun** PRAGMA : on lit une base vivante, on ne la reconfigure pas. SQLite refuse une
cible existante, donc un instantané n'écrase jamais rien.

## _pose_restore_script() — le même fichier à deux endroits, pour deux raisons
`src/forgemaster/snapshot.py:136` · appelé par `create`
**Dans** l'instantané — un vieil instantané reste restaurable par le script écrit en même temps que son manifeste,
même si le produit a changé de format depuis. Et à **`<home>/restore.py`**, chemin **stable** : un chemin stable est
ce qu'on peut écrire dans un message d'erreur ou retrouver six mois plus tard ; un chemin daté, non. Re-posé à chaque
prise, donc sa présence ne dépend d'aucune étape d'installation et un `restore.py` effacé revient tout seul.

## _new_dir() / _aside_dir() — le `mkdir` EST le verrou
`src/forgemaster/snapshot.py:156` · `src/forgemaster/restore.py:303` · appelés par `create` et `restore`
Dossier daté UTC sans `:` (hostile en shell et hors ext4), créé **sans** `exist_ok` : deux prises dans la même seconde
ne se marchent pas dessus, elles prennent le suffixe suivant. Au-delà de 100 dans la même seconde, on refuse de
deviner un nom plutôt que d'en inventer un.

## _purge() — les incomplets d'abord, `spare` jamais
`src/forgemaster/snapshot.py:170` · appelé par `create` · retourne ce qui a été supprimé
Retire les dossiers **incomplets** (prise interrompue, aucun manifeste), puis ne garde que les `keep` complets les
plus récents. `KEEP` (`snapshot.py:49`) **dérive** de `apply_update.ROLLBACK_DEPTH` et n'est pas posé à côté :
les deux rétentions — venvs et instantanés — répondent à la même question, « jusqu'où sait-on revenir ? ». La marge de
2 tient le scénario « la MAJ a échoué, j'ai restauré, j'ai retenté, ça a re-échoué ». C'est cette arithmétique que
`apply_update._refuse_if_target_would_be_purged` doit savoir compter pour refuser une cible que sa propre prise de
sûreté détruirait.

## list_snapshots() — les trois états, DÉRIVÉS et non stockés
`src/forgemaster/snapshot.py:189` · appelé par `cli_dispatch` et par `update.preflight_rollback` · retourne du plus récent au plus ancien
Un dossier invalide est **listé** avec sa raison, pas masqué : c'est ce qui rend l'invariant « manifeste en dernier »
observable plutôt que déclaratif. Chaque instantané valide porte en plus son état — tous ne se valent pas, et les
présenter côte à côte sans le dire laisse choisir celui qui ne ramènera pas. La mesure coûte **une sonde par venv
retenu** (2 en régime), pas une par instantané.

## _restorability() — `restaurable`, `données seules`, `irrestaurable` — et le vide honnête
`src/forgemaster/snapshot.py:243` · appelé par `list_snapshots` · retourne `state` + `state_reason`
- **`restaurable`** — un venv porte **exactement** le schéma de l'instantané : binaire et données reviennent ensemble.
  Le seul état qui tient la promesse du retour arrière.
- **`données seules`** — aucun venv n'a ce schéma, mais au moins un le dépasse. La remise passera le garde, puis la
  base **migrera en avant** à la première ouverture : on récupère ses données, on ne revient pas, et on ne pourra
  plus. C'est le piège que cet étage existe pour rendre visible.
- **`irrestaurable`** — tous les binaires disponibles lisent moins loin : `restore` refusera, et il a raison.
- **`inconnu`** n'est pas un quatrième état, c'est le **vide honnête**. Une instance posée par `pip` n'a pas de
  `<home>/venvs` : rendre `irrestaurable` sur ce qui est NORMAL ferait un check défaillant, donc ignoré le jour où il
  dit vrai. Un manifeste tronqué y tombe aussi — la garde existe parce que `snapshot list`, le verbe qu'on lance
  précisément quand ça va mal, remontait un `KeyError` au lieu de lister.

Le vocabulaire est repris **mot pour mot** de `restore.check_compatibility` : deux formulations du même invariant
seraient deux façons de le comprendre.

## _venv_schemas() — un schéma indéterminable n'est pas un schéma bas
`src/forgemaster/snapshot.py:225` · appelé par `list_snapshots` · retourne `{nom de venv: schéma}`
Sonde chaque venv encore posé sous `<home>/venvs` — l'ensemble des binaires vers lesquels un retour arrière peut
basculer, donc la seule mesure qui décide de l'état d'un instantané. Un venv qu'on ne sait pas sonder est **absent**
du résultat plutôt que compté à zéro : un zéro passerait les comparaisons en silence.

## cli_dispatch() — les deux états qui trompent se disent, ligne à ligne
`src/forgemaster/snapshot.py:310` · appelé par `cli._h_snapshot` (routé par `_HANDLERS`) · retourne le code de sortie
Route `create` / `restore` / la liste. `restaurable` n'a rien à ajouter, et `inconnu` se dit **une fois** en pied de
liste : le répéter noierait ceux qui comptent. `données seules` et `irrestaurable` portent chacun leur motif complet
sous leur ligne. Les marqueurs viennent de `MARQUEURS` (`snapshot.py:52`).

## _launch_restore() — on LANCE le script figé, on ne le réimplémente pas
`src/forgemaster/snapshot.py:342` · appelé par `cli_dispatch` · retourne le rc du sous-processus
De préférence la copie **figée dans l'instantané** : elle a été écrite avec son manifeste, donc elle le comprend — et
le chemin de secours (lancer le script à la main) devient exactement celui qu'on exerce ici, au lieu d'un jumeau
jamais joué. `--allow-unverified-binary` n'est ajouté **que s'il est demandé** : une copie d'avant ce drapeau ferait
sortir argparse en usage, et casserait la restauration des instantanés anciens — exactement ceux qu'on restaure le
jour où ça compte.

## _describe() — dit à la prise, pas à la restauration
`src/forgemaster/snapshot.py:367` · appelé par `cli_dispatch` (`create`) · retourne les lignes à afficher
Ce qui a été pris, ce qui manquait, ce qui reste dehors. Le moment de dire ce qu'un instantané ne couvre pas est celui
où on le prend, pas celui où on découvre le manque.

---

## RestoreError — refus levé AVANT toute écriture
`src/forgemaster/restore.py:63` · levée par `load_manifest`, `verify`, `check_compatibility`, `_aside_dir` · rattrapée par `main`
L'instance est **intacte** quand elle sort. Tout est vérifié avant la première écriture — manifeste, présence,
empreintes, compatibilité : un instantané abîmé fait échouer la restauration sans avoir touché à l'instance, jamais à
moitié.

## load_manifest() — un dossier sans manifeste est une prise interrompue
`src/forgemaster/restore.py:69` · appelé par `restore` et `_list_snapshots` · lève `RestoreError`
Produire le manifeste **en dernier** est ce qui rend l'incomplétude détectable ici plutôt que découverte à
mi-restauration. Un `schema` inconnu (`SCHEMA`, `restore.py:48`) est refusé : ce script lit son schéma et refuse de
deviner.

## verify() — tout contrôler d'abord, rapporter ensemble
`src/forgemaster/restore.py:86` · appelé par `restore` · lève `RestoreError`
Chaque entrée est présente et son empreinte (`sha256`, `restore.py:101`) correspond au manifeste. Celui qui restaure
veut la **liste complète** des dégâts, pas le premier — d'où l'accumulation avant de lever.

## check_compatibility() — le garde vit ICI, et nulle part ailleurs
`src/forgemaster/restore.py:174` · appelé par `restore` · lève `RestoreError`
La base monte en forward-only : une base de schéma neuf sous un binaire ancien est illisible, et rien ne peut la
sauver. `db/store.migrate()` ne réagit que si la base est **en retard** — une base trop neuve passait en silence. La
comparaison porte sur le **schéma**, ni sur la version produit (deux versions peuvent partager un schéma : refuser
dessus produirait des refus faux) ni sur le SHA de build (qui n'ordonne rien). Heureuse conséquence : le schéma se lit
**dans le `.db` de l'instantané**, donc le format ne change pas et le garde protège aussi les instantanés **déjà
pris**. Trois issues : **compatible** (on passe), **incompatible** (refus sec — la panne est certaine),
**indéterminable** (refus, mais le message dit la porte). Un refus qui bloque le secours dans la situation même qu'il
sert serait un check défaillant ; un simple avertissement ne tiendrait plus l'invariant. `--allow-unverified-binary`
ne couvre que l'indéterminable — le refus d'une incompatibilité **constatée** reste absolu.

## snapshot_schema() / installed_schema() / python_schema() — les trois sondes de l'invariant
`src/forgemaster/restore.py:122` · `src/forgemaster/restore.py:131` · `src/forgemaster/restore.py:137`
`snapshot_schema` lit le schéma que **porte** l'instantané (`None` s'il ne porte pas de base : rien à rendre
illisible). `installed_schema` demande à `<home>/current` ce que le binaire **en place** sait lire. `python_schema`
pose la question à **un** venv quelconque — séparée de sa jumelle le 2026-08-06 parce que `list_snapshots` la pose à
chaque venv retenu, et une seconde sonde aurait divergé le jour où l'une des deux évolue. On demande la **constante**
`SCHEMA_VERSION` au python du venv, jamais un verbe CLI : un verbe neuf ne serait porté que par les binaires
**postérieurs** au garde, alors que le binaire dangereux est l'ancien.

## _probe_env() — une sonde qui hérite de l'environnement ne mesure pas ce qu'elle croit
`src/forgemaster/restore.py:200` · appelé par `python_schema` · retourne l'environnement expurgé
`PYTHONPATH` et `PYTHONHOME` sont **retirés**. Un venv trouve son `site-packages` par son `pyvenv.cfg` : il n'en a pas
besoin, et les hériter fait répondre la sonde sur le forgemaster de **l'appelant** au lieu de celui du venv sondé.
Mesuré le 2026-08-06 et non déduit : un `bin/python` isolé répondait `20` tant que l'appelant exportait un
`PYTHONPATH`, et `None` sans lui. Ce garde décide d'une restauration irréversible — répondre juste par accident
d'environnement n'est pas répondre.

## _user_version() — indéterminable, pas zéro
`src/forgemaster/restore.py:212` · appelé par `snapshot_schema` · retourne l'entier ou `None`
`PRAGMA user_version` en lecture seule (`mode=ro`). Toute erreur rend `None` : un fichier illisible est un schéma
**indéterminable**, pas un schéma 0 — un zéro passerait le garde en silence.

## restore() — le geste, et ce qu'il ANNONCE avant de le faire
`src/forgemaster/restore.py:232` · appelé par `main` · lève `RestoreError`
Le cycle de MAJ l'atteint indirectement : `apply_update._restore` lance le `restore.py` **figé dans l'instantané**
sous `sys.executable`, et c'est le `main` de cette copie-là qui arrive ici.
Manifeste → vérification → garde de compatibilité → annonce → écriture. L'annonce nomme ce qui est remis, ce qui est
**retiré** (les entrées absentes à la prise : les remettre à l'état d'alors, c'est aussi les retirer, sinon la
restauration est partielle en silence), ce qui est **écarté**, et ce qui reste intact. Puis, si `snap_schema <
installed`, un avertissement **DONNÉES SEULES** : cet écart-là *passe* le garde, et c'est justement celui qu'on ne
voit pas venir. L'annoncer comme deux nombres neutres à l'instant où le geste devient irréversible laissait
l'utilisateur le lire comme un détail — le message d'un garde doit être **au point où le geste devient
irréversible**, pas là où l'information est disponible.
L'écriture, enfin, ne détruit rien : l'état remplacé part dans `<home>/before-restore-<horodatage>/`, rendu par
`_aside_dir` (`os.replace`, même système de fichiers, donc déplacement atomique et zéro copie), et les entrées sont
remises par temporaire + `os.replace` — jamais de fichier à moitié écrit sous le nom final. Restaurer le mauvais instantané reste rattrapable,
et c'est ce qui rend le geste praticable par quelqu'un qui doute.

## _with_sidecars() — le `-wal` part avec l'ancienne base
`src/forgemaster/restore.py:296` · appelé par `restore` · retourne le fichier et ses journaux existants
Écraser le seul `forgemaster.db` en laissant son `-wal` **ne restaure pas** : SQLite rejoue le journal par-dessus le
fichier remis et **ressuscite ce qu'on voulait défaire** — vérifié le 2026-08-02, y compris après un processus tué, la
ligne écrite après l'instantané revient sans la moindre erreur. Un retour arrière qui ne retourne pas en arrière, en
silence, est pire qu'un échec bruyant. Appliqué à **toute** entrée : un `-wal` à côté d'un fichier plat n'existe pas,
et le vérifier coûte moins cher que de savoir lesquelles sont des bases.

## main() / _snapshot_beside_script() / _default_home() / _list_snapshots() — le chemin de secours manuel
`src/forgemaster/restore.py:355` · `318` · `324` · `333` · point d'entrée quand on lance le script à la main
La copie figée **dans** l'instantané se restaure elle-même sans argument (`python3 restore.py`) — c'est
`_snapshot_beside_script` qui le détecte. Sans instantané désigné, on **liste** et on s'arrête : choisir le plus
récent « pour rendre service » écraserait un état vivant sur une supposition, et ça se demande. `_default_home`
préfère le dossier du script (la copie à chemin stable vit dans le home) avant de retomber sur `FORGEMASTER_HOME` puis
`~/.forgemaster`.

## Zones non détaillées

- **`snapshots_dir()`** (`snapshot.py:75`) — `<home>/snapshots`, la racine. Elle vit **sous** `home`, donc elle
  s'exclut elle-même (`EXCLUDED_IN_HOME`) : sans quoi l'instantané N+1 embarquerait les N précédents.
- **`_copy_atomic`, `_write_json`, `_sha256`, `_build_identity`, `_read_manifest`** (`snapshot.py:128`, `302`, `294`,
  `147`, `209`) — utilitaires sans décision propre. Deux notes tout de même : `_build_identity` rend `sha=None` en
  checkout éditable, un état **honnête** et pas une erreur ; `_read_manifest` n'expose jamais le manifeste brut,
  seulement les noms d'entrées, le brut ne servant qu'à `_restorability`.
- **`SIDECARS`, `STABLE_LINK`, `ASIDE_PREFIX`, `SCHEMA_MODULES`, `PROBE_TIMEOUT`** (`restore.py:52`, `55`, `50`,
  `59`, `60`) — constantes du script figé. `SCHEMA_MODULES` porte les deux noms de paquet (`forgemaster.db.schema` et
  l'historique `cockpit.db.schema`) pour que la sonde réponde sur un venv d'avant le renommage.
- **Le parser CLI** (`cli.py`, sous-commandes `snapshot create` / `list` / `restore`) : déclaration argposée, décrite
  dans `cli.md`. La logique est dans `cli_dispatch`.
- **Le format du manifeste** (`schema: 1`) : lu dans `create` et `_read_manifest`, pas dupliqué ici. Il ne change
  **pas** — c'est ce qui permet au garde de compatibilité de protéger les instantanés déjà pris.
