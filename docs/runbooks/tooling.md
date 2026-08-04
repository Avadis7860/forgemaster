# tooling — runbook (modules opérationnels top-level : rail d'outils, adoption d'outil, build front, unit systemd, co-install du serveur MCP, self-check, auth claude, onboarding creds)

Les huit modules opérationnels de la forge (hors spine cœur dispatch/spawn) : ils provisionnent et tiennent
l'hôte plutôt que d'orchestrer une mission. Provisionnement de l'outillage (`tools`), re-sync d'un outil
adopté (`toolsync`), build de la SPA (`webbuild`), unité systemd du daemon (`service`), **co-install du
serveur MCP de corpus** (`mcp.local`), sonde de présence (`doctor`), détection d'auth Claude (`auth`),
liaison des credentials par projet (`onboarding`). Tous suivent la
même convention forge : seams **purs** testables sans subprocess + exécution injectée, fail-loud, zéro secret
en argv.

## tools.preflight_tools() / install_tools() — gate de présence + provisionnement hôte-niveau
`src/forgemaster/tools.py:151` (`preflight_tools`) · `src/forgemaster/tools.py:401` (`install_tools`) · appelés par le
gate de dispatch (preflight avant spawn) et `forgemaster toolchain install` (cli_dispatch).
`preflight_tools` vérifie que tout binaire déclaré par la facette active (`<worktree>/.claude/settings.local.json`)
résout sur le PATH worker (`tools_env`) et lève `ToolPreflightError` (`:43`) AVANT le spawn — ne gate QUE
`declared & HOST_TOOLS` (outils hôte-provisionnés). `install_tools` est idempotent/fail-loud : crée le venv
d'outils, installe les **3** cartes (`task-map` est vendoré au wheel, pas une carte hôte) + qualité py + Node
via nodeenv (`install_plan`), symlinke chaque exécutable dans `tools/bin` ; une étape rouge abandonne (jamais
un demi-provisioning). **Les cartes se posent en DEUX passes** — `--upgrade` (qui résout les dépendances) puis
`--force-reinstall --no-deps` (qui force leur code à la réf demandée) : sans la seconde, `pip` clone, résout
`main` au bon commit, **puis saute l'install** à version égale, et l'outillage ne bouge pas alors que la
commande rend rc 0. Les cartes sont figées à `0.1.0`, donc la version ne discrimine jamais. Constaté en vrai
sur la VM 9311 le 2026-08-03, attrapé par `check_tools` restée rouge après le prétendu remède.
**Aucun credential** : les 3 dépôts sont publics, le clone est anonyme — `anonymous_env` n'ajoute que
`GIT_TERMINAL_PROMPT=0`, sans quoi un dépôt injoignable ferait *pendre* pip 900 s sur un prompt.

## tools.missing_bins() — quels binaires ne résolvent pas
`src/forgemaster/tools.py:145` · appelé par `preflight_tools`, `doctor.scan`.
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
`src/forgemaster/webbuild.py:35` (`build_front`) · `src/forgemaster/webbuild.py:113` (`ensure_codemap`) · appelés par
`forgemaster setup` (chemin from-clone) et le hook de packaging (`hatch_build.py`).
`build_front` build la SPA Vite dans `web_dir` (→ `web_dir/dist`), `npm ci` si lockfile sinon `npm install`, et
lève `FrontBuildError` (`:19`, message actionnable) si Node/npm absent ou npm échoue. `ensure_codemap` garantit
`python -m codemap` dans le venv courant (requis par l'onglet Flow) : no-op en install wheel, install **éditable**
depuis un sibling `../code-map` en from-clone — **jamais fatal** (Flow est une surface, pas le cœur CLI). Module
stdlib-pur, s'importe sans le serveur.

## webbuild.served_from() / ensure_map() — une carte installée n'est pas une carte à jour
`src/forgemaster/webbuild.py:71` (`served_from`) · `:87` (`_install_from_sibling`) · `:154` (`ensure_map`) ·
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
`tools.install_plan()` (`pip install --upgrade git+…@main` dans `tools/venv`), figées à l'install — le preflight
n'y vérifie qu'une **présence**, jamais une version. Ce que cette instance sert, et si ça a dérivé, se lit
désormais par `maps_provenance` / `check_tools` (section suivante).

## tools.maps_provenance() — quelles cartes cette instance sert-elle
`src/forgemaster/tools.py:270` (`maps_provenance`) · `src/forgemaster/tools.py:223` (`dist_provenance`) ·
`src/forgemaster/tools.py:191` (`venv_site_packages`) · consommé par `build_provenance.provenance` →
`GET /api/version`. `dist_provenance` s'appelait `map_provenance` : elle ne lit pourtant rien de spécifique
aux cartes (juste PEP 610 dans un `.dist-info`), et le serveur MCP co-installé (`mcp.local.server_provenance`)
l'appelle **telle quelle** plutôt que d'entretenir une seconde lecture du même format.

**Il n'y a aucun tampon à écrire.** `pip install git+<url>@<ref>` pose déjà `direct_url.json` (PEP 610) dans le
`dist-info`, avec le `commit_id` **résolu** — écrit par la machine, à l'install. On le **lit**. C'est le même
mécanisme que la provenance de `forgemaster-catalogs` : un mécanisme, deux consommateurs. (Le forgemaster, lui, doit
tamponner son `_build.json` parce qu'il **construit un wheel** — cf. `build_provenance` ; ce n'est pas le cas ici.)

Lecture **locale, zéro réseau, qui ne lève jamais** — d'où son usage sûr depuis une sonde HTTP. Contrat de
dégradation identique à `read_stamp` : un `sha=None` s'accompagne **toujours** d'un `reason`, et `source` dit
d'où vient la réponse (`vcs` — le seul cas porteur d'un SHA · `local-dir` · `unknown`). Un `commit_id` qui n'a
pas la forme d'un SHA est **refusé** plutôt que servi comme identité : un SHA faux coûte plus cher qu'un SHA
manquant, il retire le doute qui aurait déclenché la vérification.

Mesure du 2026-08-03 (VM 9311) : instance provisionnée à 00:34, les 3 cartes déjà différentes de leur amont à
04:19. Le figeage n'attend pas des semaines — il commence à la première heure.

## tools.check_tools() — les cartes servies ont-elles dérivé de leur amont
`src/forgemaster/tools.py:470` (`check_tools`) · `src/forgemaster/tools.py:313` (`compare`, PUR) ·
`src/forgemaster/tools.py:295` (`check_plan`, PUR) · `src/forgemaster/tools.py:337` (`overall_state`, PUR) ·
`src/forgemaster/tools.py:522` (`_cli_check`) · appelé par `forgemaster toolchain check`.

Un `git ls-remote <url> <MAP_REF>` par carte (aucun objet transféré), sous `anonymous_env` — la sonde tape les
mêmes dépôts publics que l'install et n'a donc **pas le droit** d'y ajouter un credential (un test l'asserte).
Une carte qu'on ne sert pas n'est **pas** interrogée : sa raison est déjà locale, et l'interroger ferait attendre
le réseau pour une réponse connue.

**Pourquoi pas dans `preflight_tools`** — et **pas** parce que « l'instance peut être hors réseau » : les 3
appelants du preflight (`dispatch/{worker,reviewer,woaw}.py`) spawnent tous `claude`, qui exige l'API
Anthropic. Un dispatch hors ligne n'existe pas ici.

La vraie raison est : **que ferait le dispatch de la réponse ?** `MAP_REF` est une réf **mobile** et la dérive
commence en quelques heures (mesuré : 4 h). Un preflight qui **refuse** bloquerait presque tous les spawns
passé la demi-journée — un check qui s'allume sur ce qui est normal **par construction**. Un preflight qui
**avertit** donne au worker un fait sur lequel il ne peut rien : il ne peut pas réinstaller son outillage en
vol, et muter les outils sous un worker qui tourne est précisément ce qu'on a écarté. Accessoirement, GitHub
est un **second** fournisseur, indépendant d'Anthropic : une panne GitHub deviendrait un motif neuf de ne pas
pouvoir dispatcher. La comparaison va donc là où quelqu'un peut **agir** dessus — une commande d'opérateur —
et le produit se contente de dire **localement** ce qu'il sert.

Trois issues **distinctes**, et c'est le cœur du contrat — exit **0** à jour · **1** au moins une diffère ·
**2** rien ne diffère mais au moins une n'a pas pu être comparée. « Je n'ai pas pu vérifier » n'est ni « à
jour » ni « périmé » ; le confondre avec l'un des deux refait le faux-vert (ou le faux-rouge) que cette sonde
répare. **On ne dit jamais « en retard de N commits »** : `ls-remote` ne rend que des réfs, compter exigerait de
rapatrier l'historique — la sonde dit *lesquelles* ont bougé, jamais *de combien*.

`check` **rapporte, ne mute rien**. La remise à niveau reste le geste explicite `forgemaster toolchain install`
(idempotent, `--upgrade @main`) : une re-sync automatique remplacerait un binaire sous un worker en vol.

## service.install_service() / render_unit() — unité systemd du daemon
`src/forgemaster/service.py:154` (`install_service`) · `src/forgemaster/service.py:98` (`render_unit`) · appelés par
`forgemaster service install` (cli_dispatch).
`render_unit` est **pur** : rend l'unité systemd pour `forgemaster serve`, deux portées `user` (défaut, sans root) /
`system` (root, épingle `User=`/`Group=`). `Environment=HOME` est **obligatoire** (sans lui git ne lit pas le
helper de credentials → fetch/push non-auth en silence). `install_service` écrit l'unité + un `forgemaster.env`
gabarit (jamais écrasé s'il existe) et retourne `(unit_path, env_path, systemctl_hint)` — l'appelant imprime le
hint, on n'exécute PAS systemctl (pas de footgun privilège).

## mcp.local.install() — co-installer le serveur de corpus SUR cet hôte
`src/forgemaster/mcp/local.py:245` (`install`) · `src/forgemaster/mcp/local.py:120` (`install_plan`, pur) · appelés par
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
`src/forgemaster/mcp/local.py:199` · consommé par `build_provenance.provenance` → `GET /api/version` (clé `mcp`).
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
`src/forgemaster/onboarding.py:30` (`status`) · `:89` (`link_credential`) · `:118` (`unlink_credential`) · appelés par
`forgemaster onboard <action>` (cli_dispatch).
`status` compose **cinq axes**, sans jamais révéler un secret : le **store** (backend actif + racine de confiance
joignable) ; les **requirements** par projet (un projet à miroir a besoin d'un token pour pousser → satisfait ssi
il porte un `credential_ref`) ; `claude_auth` (axe **orthogonal** à `complete` — le gate « peut dispatcher » :
l'install ne travaille qu'après un `claude login` explicite, jamais en héritant en silence l'auth d'un autre) ;
`mcp` (le corpus privé est-il câblé ? `{wired, endpoint}` via `provision.mcp.wire_state`, **optionnel** — une
install publique sans corpus reste valide et n'entre donc pas dans `complete`) ; et `build` (provenance +
fraîcheur du wheel installé — `{version, sha, committed_at, comparable, stale, behind_by, missing_types}`, cf.
`build_provenance` : un forgemaster en retard sur son SoT local se **déclare**, jamais faux-vert). Plus deux
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
