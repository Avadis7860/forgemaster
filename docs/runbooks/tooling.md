# tooling — runbook (modules opérationnels top-level : rail d'outils, adoption d'outil, build front, unit systemd, co-install du serveur MCP, self-check, auth claude, onboarding creds)

Les huit modules opérationnels de la forge (hors spine cœur dispatch/spawn) : ils provisionnent et tiennent
l'hôte plutôt que d'orchestrer une mission. Provisionnement de l'outillage (`tools`), re-sync d'un outil
adopté (`toolsync`), build de la SPA (`webbuild`), unité systemd du daemon (`service`), **co-install du
serveur MCP de corpus** (`mcp.local`), sonde de présence (`doctor`), détection d'auth Claude (`auth`),
liaison des credentials par projet (`onboarding`). Tous suivent la
même convention forge : seams **purs** testables sans subprocess + exécution injectée, fail-loud, zéro secret
en argv.

## tools.preflight_tools() / install_tools() — gate de présence + provisionnement hôte-niveau
`src/forgemaster/tools.py:177` (`preflight_tools`) · `src/forgemaster/tools.py:486` (`install_tools`) · appelés par le
gate de dispatch (preflight avant spawn) et `forgemaster toolchain install` (cli_dispatch).
`preflight_tools` vérifie que tout binaire déclaré par la facette active (`<worktree>/.claude/settings.local.json`)
résout sur le PATH worker (`tools_env`) et lève `ToolPreflightError` (`:55`) AVANT le spawn — ne gate QUE
`declared & HOST_TOOLS` (outils hôte-provisionnés). `install_tools` est idempotent/fail-loud : crée le venv
d'outils, installe les **3** cartes (`task-map` est vendoré au wheel, pas une carte hôte) + qualité py + Node
via nodeenv (`install_plan`), symlinke chaque exécutable dans `tools/bin` ; une étape rouge abandonne (jamais
un demi-provisioning).
**Les cartes viennent de l'ÉDITION** (2026-08-08) : `deploy/build-wheel.sh` les bâtit au SHA du sibling et les
embarque dans le wheel (`forgemaster/_maps` — 3 wheels + `maps.json`), et `install_plan` les pose par
`pip install --no-index --force-reinstall <chemins>`. Avant, elles venaient de `git+<url>@main`, une réf
**mobile** : deux installs à une semaine d'écart posaient deux produits sous le même numéro de version.
`--no-index` met la garantie hors-ligne dans l'argv ; `--force-reinstall` reste parce que le no-op de pip a
survécu au changement de source (les cartes sont figées à `0.1.0`, donc la version ne discrimine jamais,
fichier ou pas — constaté en vrai sur la VM 9311 le 2026-08-03) ; `--no-deps` n'est **pas** repris, pour
qu'une carte qui gagnerait une dépendance échoue bruyamment au lieu d'être posée amputée.
**Édition non posable ⇒ refus fail-loud** (`EditionMapsError`), jamais un repli git : dossier absent,
manifeste illisible, carte non déclarée ou wheel manquant sont **quatre** refus distincts, parce qu'ils
n'ont pas le même remède.
**Aucun credential** : il n'y a plus d'URL dans le plan, donc plus de clone, donc plus rien à authentifier —
la propriété n'est plus tenue par un env de précaution mais par l'absence du chemin. (`anonymous_env` reste,
pour son seul appelant réel : `mcp.local`, qui clone.)

## tools.missing_bins() — quels binaires ne résolvent pas
`src/forgemaster/tools.py:171` · appelé par `preflight_tools`, `doctor.scan`.
Seam **pur** : sous-ensemble trié de `bins` que `shutil.which` ne trouve pas via `env["PATH"]`. C'est la vérité
unique partagée entre le gate de dispatch et la sonde `doctor` — aucune duplication de logique de présence.

## toolsync.sync_tool() — re-sync pull-only d'un outil adopté
`src/forgemaster/toolsync.py:38` · appelé par `forgemaster tool sync <slug>` (cli_dispatch).
Re-synchronise un `kind=tool` avec son amont, **pull-only ff seulement** (frontière read-only stricte). Refuse un
`kind=project` par `NotAToolError` (`:33`, → 409) : un projet se réconcilie via la voie gatée `reconcile`, jamais
ici. Fetch+ff via `InternalGit.sync_tracking` sur `TRACKED_BRANCHES=("dev","main")` sous auth transitoire
(`credential_ref` résolu à l'usage). Si `dev` a bougé, pré-chauffe best-effort l'index Flow (`ensure_index`,
jamais bloquant → `index_refreshed=False` honnête).

## webbuild.build_front() / ensure_codemap() — build SPA + garantie code-map
`src/forgemaster/webbuild.py:35` (`build_front`) · `src/forgemaster/webbuild.py:114` (`ensure_codemap`) · appelés par
`forgemaster setup` (chemin from-clone) et le hook de packaging (`hatch_build.py`).
`build_front` build la SPA Vite dans `web_dir` (→ `web_dir/dist`), `npm ci` si lockfile sinon `npm install`, et
lève `FrontBuildError` (`:19`, message actionnable) si Node/npm absent ou npm échoue. `ensure_codemap` garantit
`python -m codemap` dans le venv courant (requis par l'onglet Flow) : no-op en install wheel, install **éditable**
depuis un sibling `../code-map` en from-clone — **jamais fatal** (Flow est une surface, pas le cœur CLI). Module
stdlib-pur, s'importe sans le serveur.

## webbuild.served_from() / ensure_map() — une carte installée n'est pas une carte à jour
`src/forgemaster/webbuild.py:72` (`served_from`) · `:88` (`_install_from_sibling`) · `:160` (`ensure_map`) ·
`:174` (`ensure_maps`, les 4 cartes).

**L'ordre est load-bearing** : on cherche le sibling **avant** de se satisfaire d'un module importable. L'inverse
(le court-circuit historique `find_spec(...) is not None`) figeait la carte à sa première install. Défaut mesuré
le 2026-08-01 : le venv de ce repo servait un `codemap` **sans le verbe `check`**, livré chez code-map le jour
même — et les deux annonçaient **la même version** (`0.1.0`, schéma `1.6.0`), donc rien ne les distinguait. Une
session qui obéissait à `CLAUDE.md` et tapait `codemap check` recevait `invalid choice`.

`served_from` est le discriminant : l'origine du module importé tombe-t-elle **dans les sources du sibling** ?
Une copie de `site-packages` répond non même si le module s'importe parfaitement. L'install est donc **éditable**
(`pip install -e`) : la carte suit le `git pull` de son repo, sans entretien ni fenêtre de dérive. Écarté — une
copie ré-installée avec `--upgrade` : elle re-fige au commit du jour, on paie le même défaut plus tard.
Idempotent **sans relancer pip** (`pip install -e` reconstruit un wheel à chaque appel, `forgemaster setup` le
paierait × 4 pour rien). Le chemin **wheel** (code-map vendoré, aucun sibling) est inchangé.

Frontière : ceci couvre le venv d'un **checkout de dev**. Sur une instance provisionnée, les cartes viennent de
`tools.install_plan()` — depuis le 2026-08-08, des **wheels de l'édition** (`pip install --no-index` dans
`tools/venv`) et non plus de `git+…@main` : elles sont épinglées à l'artefact, plus figées au jour de
l'install. Le preflight n'y vérifie toujours qu'une **présence**, jamais une identité. Ce que cette instance
sert, et si c'est bien ce que son édition déclare, se lit par `maps_provenance` / `check_tools` (ci-dessous).

## tools.maps_provenance() — quelles cartes cette instance sert-elle
`src/forgemaster/tools.py:325` (`maps_provenance`) · `src/forgemaster/tools.py:268` (`dist_provenance`) ·
`src/forgemaster/tools.py:218` (`venv_site_packages`) · consommé par `build_provenance.provenance` →
`GET /api/version`. `dist_provenance` s'appelait `map_provenance` : elle ne lit pourtant rien de spécifique
aux cartes (juste PEP 610 dans un `.dist-info`), et le serveur MCP co-installé (`mcp.local.server_provenance`)
l'appelle **telle quelle** plutôt que d'entretenir une seconde lecture du même format.

**Deux sources, dans un ordre qui compte.** ① Le tampon `_vendored_from.txt`, posé **dans le paquet** par
`deploy/build-wheel.sh` et localisé par le **`RECORD`** de la distribution — jamais par un nom de paquet
deviné depuis le nom de distribution (`code-map` → `codemap` est une convention, pas une règle : PEP 503
normalise le nom de *distribution* et ne dit rien du nom d'*import*). C'est le mode canonique depuis le
2026-08-08, et il est lu **en premier** parce qu'une carte posée depuis un wheel n'a pas de `vcs_info` : s'en
tenir à PEP 610 rendrait `sha=None` sur exactement le mode qu'on vient de rendre canonique. ② `direct_url.json`
(PEP 610), que `pip install git+<url>@<ref>` pose avec le `commit_id` **résolu** — le mode historique, encore
vivant sur toute instance provisionnée avant cette date, et c'est précisément ce qui **distingue les deux
modes**. Aucun registre parallèle n'est écrit : on lit ce que l'install a laissé.

Lecture **locale, zéro réseau, qui ne lève jamais** — d'où son usage sûr depuis une sonde HTTP. Contrat de
dégradation identique à `read_stamp` : un `sha=None` s'accompagne **toujours** d'un `reason`, et `source` dit
d'où vient la réponse (`edition` · `vcs` · `local-dir` · `unknown`). Un SHA qui n'a pas la **forme** d'un SHA
— tampon corrompu comme `commit_id` douteux — est **refusé** plutôt que servi comme identité : un SHA faux
coûte plus cher qu'un SHA manquant, il retire le doute qui aurait déclenché la vérification.

Mesure du 2026-08-03 (VM 9311), qui a motivé l'épinglage : instance provisionnée à 00:34, les 3 cartes déjà
différentes de leur amont à 04:19. La dérive n'attendait pas des semaines — elle commençait à la première
heure. C'est cette dérive-là que l'édition ferme.

## tools.check_tools() — les cartes servies sont-elles celles de l'ÉDITION
`src/forgemaster/tools.py:567` (`check_tools`) · `src/forgemaster/tools.py:313` (`compare`, PUR) ·
`src/forgemaster/tools.py:344` (`read_edition`) · `src/forgemaster/tools.py:337` (`overall_state`, PUR) ·
`src/forgemaster/tools.py:623` (`_cli_check`) · appelé par `forgemaster toolchain check`.

**La question a changé avec l'épinglage (2026-08-08).** La sonde comparait le commit servi au `main` amont
(`git ls-remote` par carte). Ce n'est plus la bonne question : les cartes ne suivent plus une réf mobile, donc
« suis-je en retard sur upstream ? » est devenue la question du **wheel** — portée par `build_provenance`
contre le miroir SoT local, puis par le canal servi de la phase 5. Celle qui reste, et qui n'avait **aucune**
réponse, est *mes cartes sont-elles celles de mon édition ?* Elle se pose exactement quand une instance a
monté d'édition sans reposer son outillage — `update apply` ne touche pas `tools/` — et elle se répond
**sans réseau** : le tampon servi (`_vendored_from.txt`, dans le paquet) contre le SHA déclaré
(`forgemaster/_maps/maps.json`). Zéro subprocess, zéro timeout, et le mode d'échec le plus fréquent de
l'ancienne sonde (réseau coupé) a disparu avec elle.

Trois issues **distinctes**, et c'est le cœur du contrat — exit **0** conforme · **1** au moins une diffère ·
**2** rien ne diffère mais au moins une n'a pas pu être comparée (carte non installée, ou édition qui ne
déclare rien : checkout dev, wheel dégradé). « Je n'ai pas pu vérifier » n'est ni « conforme » ni « périmé » ;
le confondre avec l'un des deux refait le faux-vert (ou le faux-rouge) que cette sonde répare. **On ne dit
jamais « en retard de N commits »** : deux SHA ne se soustraient pas sans l'historique — la sonde dit
*lesquelles* diffèrent, jamais *de combien*.

`check` **rapporte, ne mute rien**. La remise à niveau reste le geste explicite `forgemaster toolchain install`
(idempotent, hors-ligne) : une re-sync automatique remplacerait un binaire sous un worker en vol.

**Le même objet est servi par `GET /api/version` depuis le 2026-08-08** (volet `edition`), et c'est
délibérément le **même** — `build_provenance.provenance` appelle `check_tools` verbatim, il n'y a pas deux
lectures qui pourraient diverger. Motif du déplacement : un verdict qui ne vit que dans la CLI est hors de
portée de qui n'a pas de terminal, c'est-à-dire de l'utilisateur distribué pour qui tout le cycle de MAJ
existe. La sonde HTTP passe `served=` (les cartes qu'elle vient de lire pour son volet `maps`) plutôt que de
laisser `check_tools` re-parcourir les mêmes `.dist-info` : deux marches, une liste.

`overall_state` rend `unknown` sur une liste **vide** — zéro carte comparée satisfait « aucune ne diffère »
par vacuité, et le lire comme un vert dirait « conforme » à une instance dont on n'a rien pu lire. Le cas
était inatteignable tant que `maps_provenance` était le seul fournisseur ; il le devient dès qu'un appelant
injecte sa liste.

## build_provenance.install_mode() — de quel MODE d'install cette instance vient
`src/forgemaster/build_provenance.py:89` (`install_mode`, **PUR**) · composé par `provenance` · servi par
`GET /api/version` (clé `install`).

Deux faits lisibles localement, et leur **conjonction** est le mode : le wheel porte-t-il son tampon
`_build.json` ? l'édition `forgemaster/_maps/maps.json` est-elle lisible ? Même patron — et même raison — que
`mcp.local.topology` : **déduit du disque, jamais déclaré**. Une clé d'env `…_MODE` serait un champ qui peut
mentir, puisque rien ne le re-vérifie après une réinstall.

| tampon | édition | `mode` | ce que ça dit |
|---|---|---|---|
| ✓ | ✓ | `edition` | wheel de release portant les cartes qu'il épingle — le mode canonique |
| ✓ | ✗ | `wheel` | wheel bâti **sans** son édition (d'avant le 2026-08-08, ou build dégradé) |
| ✗ | ✗ | `checkout` | sibling éditable — mode de **développement**, normal, pas une panne |
| ✗ | ✓ | `unknown` | paire qu'un même build ne peut pas produire → on avoue, on ne tranche pas |

**Pourquoi ce champ existe alors que `sha is None` « suffisait »** : il ne suffisait que tant qu'un seul
candidat le satisfaisait. Un wheel bâti sans tampon aurait été annoncé « checkout » — un mode qu'il n'a pas,
avec une réparation qui n'est pas la sienne. C'est la leçon de la phase 3a·5a : *une dérivation exacte dans
un cas devient un choix arbitraire dans l'autre*. Le front consommait exactement cette dérivation (« installée
depuis un checkout, pas un wheel ») ; il lit désormais le champ mesuré.

## service.install_service() / render_unit() — unité systemd du daemon
`src/forgemaster/service.py:158` (`install_service`) · `src/forgemaster/service.py:102` (`render_unit`) · appelés par
`forgemaster service install` (cli_dispatch).
`render_unit` est **pur** : rend l'unité systemd pour `forgemaster serve`, deux portées `user` (défaut, sans root) /
`system` (root, épingle `User=`/`Group=`). `Environment=HOME` est **obligatoire** (sans lui git ne lit pas le
helper de credentials → fetch/push non-auth en silence). `install_service` écrit l'unité + un `forgemaster.env`
gabarit (jamais écrasé s'il existe) et retourne `(unit_path, env_path, hint)` — l'appelant imprime le
hint, on n'exécute PAS systemctl (pas de footgun privilège).
Le hint lui-même vient de `src/forgemaster/service.py:183` (`systemctl_hint`), **pur**, partagé avec
`mcp.local.install` : une seule formulation pour toutes les unités que ce produit pose. En portée `user` il
ouvre le `linger` **en premier** — sans lui le gestionnaire systemd de l'utilisateur meurt avec sa dernière
session, et le service avec (mesuré sur vrai systemd le 2026-08-06) ; en portée `system` il ne l'écrit pas.

## mcp.local.install() — co-installer le serveur de corpus SUR cet hôte
`src/forgemaster/mcp/local.py:250` (`install`) · `src/forgemaster/mcp/local.py:120` (`install_plan`, pur) · appelés par
`forgemaster mcp install` et l'étape `[8/9]` de `deploy/provision-ct.sh` (`--with-mcp`).
Pose un venv **dédié** (`$FORGEMASTER_HOME/mcp/venv` — ni celui du forgemaster, ni celui des outils : trois cycles de
vie distincts), installe `forgemaster-catalogs` au **SHA épinglé** (`SERVER_REF`, pas une réf mobile — §3 de la
décision d'édition : une pièce de classe « nous » monte AVEC l'édition), **génère** le secret HS256 s'il n'y en
a pas, écrit un `EnvironmentFile` en `600` + une unité systemd, puis câble le forgemaster sur son **loopback**
(`wire(live_env=True)`). Retourne `{ok, steps, unit, env_file, endpoint, sha, hint}` — l'appelant imprime le
hint, **on n'exécute PAS systemctl** (même règle que `service.install_service`).
Deux refus load-bearing : **`data_root` obligatoire et existant** (un serveur démarré sur une racine absente
répond `200` sur un corpus vide — cette réussite apparente est pire qu'un échec), et **abandon avant d'écrire
l'unité** si une étape pip est rouge (jamais de demi-provisioning qu'un `systemctl enable` viendrait démarrer).
Le secret déjà câblé est **réutilisé** à la ré-exécution : le régénérer invaliderait les jetons du serveur qui
tourne, à chaque appel d'une commande annoncée idempotente. Clone **anonyme** par défaut (`anonymous_env`) ;
`--token-file` est une voie explicite, utile seulement tant que le dépôt du serveur est privé.
`install_plan` refait les **deux passes** de `tools.install_plan` — même piège pip-git-SHA : la version est
figée à `0.1.0`, donc `--upgrade` seul saute l'install en rendant rc 0.

## mcp.local.topology() — laquelle des deux topologies cette instance est-elle
`src/forgemaster/mcp/local.py:203` · consommé par `build_provenance.provenance` → `GET /api/version` (clé `mcp`).
Répond à l'exigence du §4 de la décision d'édition : deux topologies déclarées, et l'instance **dit** laquelle.
Retourne `{topology, sha, endpoint, reason}`, lecture **locale, zéro réseau, qui ne lève jamais**.
**Déduit du disque, jamais déclaré** — une clé d'env `…_TOPOLOGY` serait un champ qui peut mentir, que rien ne
re-vérifie après un re-câblage. Deux faits suffisent : le serveur est-il installé sous `mcp/venv` (via
`server_provenance` → `tools.dist_provenance`, PEP 610) ? l'endpoint consommé est-il en loopback (`is_loopback`,
pur, **aucun DNS résolu** — `0.0.0.0` exclu : c'est une adresse de bind, jamais de destination) ?
Quatre états : `none` (aucun endpoint — **normal**, une install sans corpus n'a rien à interroger) ·
`co-installed` (installé ici + loopback — **seul cas qui porte un `sha`**) · `remote` (un endpoint d'ailleurs ;
`sha: null` **avec son motif**, car le build d'une autre machine ne se lit pas localement — il se demande par
`GET /version` sous JWT) · `unknown` (sonde illisible). Le cas tordu est nommé : serveur installé mais endpoint
pointant ailleurs → `remote`, et le `reason` signale le serveur local inutilisé.

## doctor.scan() — sonde de présence par type/facette
`src/forgemaster/doctor.py:23` · appelé par `forgemaster doctor` (cli_dispatch).
Pour chaque `settings.local.json` de facette des bundles vendorés, calcule `required_bins & HOST_TOOLS` et
`missing_bins` sous `tools_env`. Retourne `[{type, facet, required, missing}]`. **Même vérité** que
`preflight_tools`, mais en lecture globale — une sonde d'install shell (`forgemaster doctor; echo $?`, rc 0/1).
Déterministe : glob local + `shutil.which`, zéro réseau/LLM.

## auth.claude_auth_status() / trust_workspace() — auth Claude de l'hôte
`src/forgemaster/auth.py:30` (`claude_auth_status`) · `src/forgemaster/auth.py:46` (`trust_workspace`) · appelés par
l'onboarding (`status`) et le gate de dispatch.
`claude_auth_status` répond « cette machine est-elle authentifiée ? » **sans jamais lire le secret** : présence de
`~/.claude/.credentials.json` ou d'une clé d'env → `{authenticated, source}` (source ∈ credentials-file /
env-api-key / env-oauth / None). Auth **par machine**, pas par projet. `trust_workspace` upsert
`projects[<workspace>].hasTrustDialogAccepted=true` dans `~/.claude.json` (écriture atomique, idempotent) — sans
ça `claude -p` headless IGNORE les `allowedTools` d'un workspace non-trusted (worker inerte).

## onboarding.status() / link_credential() / unlink_credential() — liaison des credentials par projet
`src/forgemaster/onboarding.py:30` (`status`) · `:94` (`link_credential`) · `:123` (`unlink_credential`) · appelés par
`forgemaster onboard <action>` (cli_dispatch).
`status` compose **cinq axes**, sans jamais révéler un secret : le **store** (backend actif + racine de confiance
joignable) ; les **requirements** par projet (un projet à miroir a besoin d'un token pour pousser → satisfait ssi
il porte un `credential_ref`) ; `claude_auth` (axe **orthogonal** à `complete` — le gate « peut dispatcher » :
l'install ne travaille qu'après un `claude login` explicite, jamais en héritant en silence l'auth d'un autre) ;
`mcp` (le corpus privé est-il câblé ? `{wired, endpoint}` via `provision.mcp.wire_state`, **optionnel** — une
install publique sans corpus reste valide et n'entre donc pas dans `complete`) ; et `build` (provenance +
fraîcheur du wheel installé — `{version, sha, committed_at, comparable, stale, behind_by, missing_types,
reference, head}`, cf.
`build_provenance` : un forgemaster en retard sur son SoT local se **déclare**, jamais faux-vert ; `reference`
+ `head` disent **contre quoi** — le miroir bare local et son SHA — parce que ce miroir vieillit avec
l'instance et qu'un verdict qu'on ne peut pas situer n'est pas jugeable). Plus deux
verdicts : `complete` (store prêt ET toutes les exigences satisfaites) et `first_run` (aucun projet créé — le
wizard doit guider, pas annoncer « complet »), qui distinguent *rien-à-faire-car-réglé* de *rien-encore-réglé*.
Les axes `claude_auth`/`mcp`/`build` sont **injectables** pour les tests ; à défaut ils sont détectés live.
`link_credential` lie **exactement l'un** de `token` (voie fichier → `store.put` → ref opaque) ou
`ref` (voie BWS bring-your-own UUID, validé par `store.get`) ; la DB ne reçoit que la **référence**, jamais la
valeur. `unlink_credential` remet `credential_ref` à NULL (le secret reste dans le store).

## Zones non détaillées
- Les `cli_dispatch` de chaque module (routage CLI + impression `✅/🔴`, codes de sortie fail-loud).
- `onboarding.wire_mcp` : le câblage MCP vu du wizard (délègue à `provision.mcp.wire`, `live_env=True` → le daemon voit la ref sans restart).
- `tools.cli_env` : l'env d'outillage rendu à un appelant externe (même résolution que `tools_env`, exposée).
- `webbuild` : `find_map_src`, `ensure_map`, `ensure_maps` — la mise à disposition des index de maps pour le build du front (dérivés, régénérés si absents).
- `tools` : helpers de chemin purs `tools_root`/`tools_venv`/`nodeenv_prefix`/`tools_bin`/`tools_env`,
  `required_bins`, `_symlink_sources`, `install_plan`, `run_step` (public — partagé avec
  `mcp.local.install`), `_default_runner`, constantes
  `MAP_REPOS`/`PY_QUALITY`/`HOST_TOOLS`/`_VENV_BINS`/`_NODE_BINS` ; côté provenance/sonde :
  `parse_ls_remote`, `_looks_like_sha`, `_read_text`, `_dist_info`, `_CHECK_MARKS`/`_CHECK_EXITS`,
  `_SHA_LENGTHS`/`_LS_REMOTE_TIMEOUT_S`.
- `service` : `set_env_keys`/`load_env_file` (parité env CLI↔systemd), helpers `_forgemaster_bin`/`_env_template`/
  `_unit_dir`.
- `webbuild` : `find_web_dir`/`find_codemap_src` (localisation du checkout).
- `doctor` : `_report_mcp`/`_report_runtime` (état token MCP P4 + runtime conteneur P2).
- `toolsync` : table `_ACTION_GLYPH`, constante `TRACKED_BRANCHES`.
- `mcp.local` : helpers de chemin purs `mcp_root`/`mcp_venv`/`env_file`/`unit_path`/`endpoint_url`,
  rendus `render_env`/`render_unit`, `cli_install` (routage CLI), `_existing_secret` (réutilisation
  du secret câblé), `McpInstallError`, constantes `SERVER_REPO`/`SERVER_REF`/`SERVER_DIST`/
  `SERVER_UNIT`/`DEFAULT_PORT`/`LOOPBACK_HOST`/`JWT_ISSUER`/`JWT_AUDIENCE`.
