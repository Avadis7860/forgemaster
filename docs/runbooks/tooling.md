# tooling — runbook (modules opérationnels top-level : rail d'outils, adoption d'outil, build front, unit systemd, self-check, auth claude, onboarding creds)

Les sept modules opérationnels top-level de la forge (hors spine cœur dispatch/spawn) : ils provisionnent et
tiennent l'hôte plutôt que d'orchestrer une mission. Provisionnement de l'outillage (`tools`), re-sync d'un
outil adopté (`toolsync`), build de la SPA (`webbuild`), unité systemd du daemon (`service`), sonde de présence
(`doctor`), détection d'auth Claude (`auth`), liaison des credentials par projet (`onboarding`). Tous suivent la
même convention forge : seams **purs** testables sans subprocess + exécution injectée, fail-loud, zéro secret
en argv.

## tools.preflight_tools() / install_tools() — gate de présence + provisionnement hôte-niveau
`src/cockpit/tools.py:151` (`preflight_tools`) · `src/cockpit/tools.py:204` (`install_tools`) · appelés par le
gate de dispatch (preflight avant spawn) et `cockpit tools install` (cli_dispatch).
`preflight_tools` vérifie que tout binaire déclaré par la facette active (`<worktree>/.claude/settings.local.json`)
résout sur le PATH worker (`tools_env`) et lève `ToolPreflightError` (`:43`) AVANT le spawn — ne gate QUE
`declared & HOST_TOOLS` (outils hôte-provisionnés). `install_tools` est idempotent/fail-loud : crée le venv
d'outils, installe les **3** cartes (`task-map` est vendoré au wheel, pas une carte hôte) + qualité py + Node
via nodeenv (`install_plan`), symlinke chaque exécutable dans `tools/bin` ; une étape rouge abandonne (jamais
un demi-provisioning). **Aucun credential** : les 3 dépôts sont publics, le clone est anonyme — `anonymous_env`
n'ajoute que `GIT_TERMINAL_PROMPT=0`, sans quoi un dépôt injoignable ferait *pendre* pip 900 s sur un prompt.

## tools.missing_bins() — quels binaires ne résolvent pas
`src/cockpit/tools.py:145` · appelé par `preflight_tools`, `doctor.scan`.
Seam **pur** : sous-ensemble trié de `bins` que `shutil.which` ne trouve pas via `env["PATH"]`. C'est la vérité
unique partagée entre le gate de dispatch et la sonde `doctor` — aucune duplication de logique de présence.

## toolsync.sync_tool() — re-sync pull-only d'un outil adopté
`src/cockpit/toolsync.py:38` · appelé par `cockpit tool sync <slug>` (cli_dispatch).
Re-synchronise un `kind=tool` avec son amont, **pull-only ff seulement** (frontière read-only stricte). Refuse un
`kind=project` par `NotAToolError` (`:33`, → 409) : un projet se réconcilie via la voie gatée `reconcile`, jamais
ici. Fetch+ff via `InternalGit.sync_tracking` sur `TRACKED_BRANCHES=("dev","main")` sous auth transitoire
(`credential_ref` résolu à l'usage). Si `dev` a bougé, pré-chauffe best-effort l'index Flow (`ensure_index`,
jamais bloquant → `index_refreshed=False` honnête).

## webbuild.build_front() / ensure_codemap() — build SPA + garantie code-map
`src/cockpit/webbuild.py:35` (`build_front`) · `src/cockpit/webbuild.py:113` (`ensure_codemap`) · appelés par
`cockpit setup` (chemin from-clone) et le hook de packaging (`hatch_build.py`).
`build_front` build la SPA Vite dans `web_dir` (→ `web_dir/dist`), `npm ci` si lockfile sinon `npm install`, et
lève `FrontBuildError` (`:19`, message actionnable) si Node/npm absent ou npm échoue. `ensure_codemap` garantit
`python -m codemap` dans le venv courant (requis par l'onglet Flow) : no-op en install wheel, install **éditable**
depuis un sibling `../code-map` en from-clone — **jamais fatal** (Flow est une surface, pas le cœur CLI). Module
stdlib-pur, s'importe sans le serveur.

## webbuild.served_from() / ensure_map() — une carte installée n'est pas une carte à jour
`src/cockpit/webbuild.py:71` (`served_from`) · `:87` (`_install_from_sibling`) · `:154` (`ensure_map`) ·
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
Idempotent **sans relancer pip** (`pip install -e` reconstruit un wheel à chaque appel, `cockpit setup` le
paierait × 4 pour rien). Le chemin **wheel** (code-map vendoré, aucun sibling) est inchangé.

Frontière : ceci couvre le venv d'un **checkout de dev**. Sur une instance provisionnée, les cartes viennent de
`tools.install_plan()` (`pip install --upgrade git+…@main` dans `tools/venv`), figées à l'install — le preflight
n'y vérifie qu'une **présence**, jamais une version.

## service.install_service() / render_unit() — unité systemd du daemon
`src/cockpit/service.py:154` (`install_service`) · `src/cockpit/service.py:98` (`render_unit`) · appelés par
`cockpit service install` (cli_dispatch).
`render_unit` est **pur** : rend l'unité systemd pour `cockpit serve`, deux portées `user` (défaut, sans root) /
`system` (root, épingle `User=`/`Group=`). `Environment=HOME` est **obligatoire** (sans lui git ne lit pas le
helper de credentials → fetch/push non-auth en silence). `install_service` écrit l'unité + un `cockpit.env`
gabarit (jamais écrasé s'il existe) et retourne `(unit_path, env_path, systemctl_hint)` — l'appelant imprime le
hint, on n'exécute PAS systemctl (pas de footgun privilège).

## doctor.scan() — sonde de présence par type/facette
`src/cockpit/doctor.py:23` · appelé par `cockpit doctor` (cli_dispatch).
Pour chaque `settings.local.json` de facette des bundles vendorés, calcule `required_bins & HOST_TOOLS` et
`missing_bins` sous `tools_env`. Retourne `[{type, facet, required, missing}]`. **Même vérité** que
`preflight_tools`, mais en lecture globale — une sonde d'install shell (`cockpit doctor; echo $?`, rc 0/1).
Déterministe : glob local + `shutil.which`, zéro réseau/LLM.

## auth.claude_auth_status() / trust_workspace() — auth Claude de l'hôte
`src/cockpit/auth.py:30` (`claude_auth_status`) · `src/cockpit/auth.py:46` (`trust_workspace`) · appelés par
l'onboarding (`status`) et le gate de dispatch.
`claude_auth_status` répond « cette machine est-elle authentifiée ? » **sans jamais lire le secret** : présence de
`~/.claude/.credentials.json` ou d'une clé d'env → `{authenticated, source}` (source ∈ credentials-file /
env-api-key / env-oauth / None). Auth **par machine**, pas par projet. `trust_workspace` upsert
`projects[<workspace>].hasTrustDialogAccepted=true` dans `~/.claude.json` (écriture atomique, idempotent) — sans
ça `claude -p` headless IGNORE les `allowedTools` d'un workspace non-trusted (worker inerte).

## onboarding.status() / link_credential() / unlink_credential() — liaison des credentials par projet
`src/cockpit/onboarding.py:30` (`status`) · `:89` (`link_credential`) · `:118` (`unlink_credential`) · appelés par
`cockpit onboard <action>` (cli_dispatch).
`status` compose **cinq axes**, sans jamais révéler un secret : le **store** (backend actif + racine de confiance
joignable) ; les **requirements** par projet (un projet à miroir a besoin d'un token pour pousser → satisfait ssi
il porte un `credential_ref`) ; `claude_auth` (axe **orthogonal** à `complete` — le gate « peut dispatcher » :
l'install ne travaille qu'après un `claude login` explicite, jamais en héritant en silence l'auth d'un autre) ;
`mcp` (le corpus privé est-il câblé ? `{wired, endpoint}` via `provision.mcp.wire_state`, **optionnel** — une
install publique sans corpus reste valide et n'entre donc pas dans `complete`) ; et `build` (provenance +
fraîcheur du wheel installé — `{version, sha, committed_at, comparable, stale, behind_by, missing_types}`, cf.
`build_provenance` : un cockpit en retard sur son SoT local se **déclare**, jamais faux-vert). Plus deux
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
  `required_bins`, `_symlink_sources`, `install_plan`, `_run_step`, `_default_runner`, constantes
  `MAP_REPOS`/`PY_QUALITY`/`HOST_TOOLS`/`_VENV_BINS`/`_NODE_BINS`.
- `service` : `set_env_keys`/`load_env_file` (parité env CLI↔systemd), helpers `_cockpit_bin`/`_env_template`/
  `_unit_dir`.
- `webbuild` : `find_web_dir`/`find_codemap_src` (localisation du checkout).
- `doctor` : `_report_mcp`/`_report_runtime` (état token MCP P4 + runtime conteneur P2).
- `toolsync` : table `_ACTION_GLYPH`, constante `TRACKED_BRANCHES`.
