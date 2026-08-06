# adapters — runbook (adaptateurs vers les pairs : codemap embarqué (index/flow), résolveur blueprint MCP injecté dans taskmap, sessions PTY web détachables)

Trois ponts du forgemaster vers ses outils-pairs, chacun consommé en **boîte-noire** derrière un seam injectable. `codemap` (index matérialisé + requêtes de flot) alimente la route codemap du daemon ; `mcp.client.blueprint_resolver` est injecté dans `taskmap.context` pour la résolution blueprint-mode, avec **dégradation honnête** si le MCP est absent ; `terminal.pty` + `terminal.registry` servent un PTY local au terminal web (xterm.js) en **sessions détachables** — le shell survit à la déconnexion du WebSocket et se ré-attache. Aucun n'invente : contrat de sortie ou `None`/erreur explicite.

## codemap.ensure_index() — garantit un index frais (SHA, schema)-bound
`src/forgemaster/codemap/index.py:81` · appelé par `flow.list_operations`/`flow.flow` (donc la route codemap du daemon).
Résout le SHA de la réf (`InternalGit.feature_sha`) + la `schema_version` du contrat code-map → clé de cache `home/codemap/<projet>/<sha>/<schema>`. Cache-hit si le marqueur **forgemaster** `.forgemaster-index-built` (ligne 34) est présent ; sinon `git archive <sha>` matérialise l'arbre puis `codemap build --root`, marqueur écrit **après** rc 0. Toute panne (réf absente, archive/build KO, version illisible) → `CodemapError`. Le `runner` (seam `core.run`) est injectable.

## codemap.IndexHandle — poignée d'un index matérialisé
`src/forgemaster/codemap/index.py:52` · rendu par `ensure_index`, consommé par `flow`/`list_operations`.
`dataclass(frozen=True)` — `(project, ref, sha, root)`. `root` est la racine de l'arbre extrait (contient `.codemap/`), passée en `--root` aux requêtes `codemap flow`.

## codemap.codemap_schema_version() — négocie le contrat AVANT tout build
`src/forgemaster/codemap/index.py:62` · appelé par `ensure_index` (moitié de la clé de cache).
Lit `codemap --schema-version` **sans index** : câble la clé de cache et négocie la compat avant de bâtir. **Non mémoïsé** (un `id(runner)` peut être réutilisé après GC → valeur périmée ; le CLI imprime une constante, coût négligeable hors boucle chaude). Sortie vide / rc non-0 → `CodemapError`.

## codemap.index_dir_for() — chemin de cache dérivé (sha × schema)
`src/forgemaster/codemap/index.py:74` · appelé par `ensure_index`.
Retourne `home/codemap/<projet>/<sha>/<schema>`. Invalidation double : un nouveau SHA (nouveau code) **OU** un nouveau `schema` (upgrade de code-map) ouvre un dossier neuf — l'ancien index n'est jamais servi périmé (ferme le trou « vider le cache à la main après déploiement »). PUR (composition de chemin).

## codemap.flow.flow() — sous-graphe de flot d'une opération
`src/forgemaster/codemap/flow.py:33` · appelé par la route codemap du daemon.
`ensure_index` (cache SHA) puis `codemap flow <op> --format json --depth --root`. Renvoie `{ok, operation, entry, nodes[], edges[], stats}` **relayé tel quel** — `ok:false` (opération introuvable/ambiguë) compris : l'appelant décide du rendu. Stdout illisible → `CodemapError` (jamais un demi-résultat). `depth` défaut 6.

## codemap.flow.list_operations() — entry points découverts
`src/forgemaster/codemap/flow.py:22` · appelé par la route codemap du daemon.
`ensure_index` puis `codemap flow --list --root`. Renvoie `{operations:[{operation,entry,kind}], engine}` (routes API + verbes CLI). Build ou sortie illisible → `CodemapError`.

## mcp.client.blueprint_resolver() — résolveur blueprint injecté dans taskmap
`src/forgemaster/mcp/client.py:94` · injecté au seam `taskmap.context.build_context`/`doctor` (`BlueprintResolver = id -> dict|None`) ; le board (P3) s'en sert pour `resolved:true` sur un `features.blueprint`.
Ferme sur `settings` et rend `resolve(bp_id)`. **Invariant de dégradation honnête** : secret non câblé (`FORGEMASTER_MCP_JWT_SECRET_REF` absent) → `None` ; secret `< 32` octets → `None` ; toute exception (mint/réseau/MCP pendu) capturée → `None` ; réponse non-dict ou vide → `None`. **Jamais inventé, jamais propagé** — exactement le contrat qu'attend `taskmap.blueprint_verdict` (`None`/`{}` = liaison morte signalée). `secret_ref`/`endpoint`/`resolver`/`caller`/`timeout` sont des seams (défaut réseau réel = `_read_blueprint`, timeout 5 s).

## mcp.client._read_blueprint() — coquille réseau réelle (fastmcp)
`src/forgemaster/mcp/client.py:87` · seam `caller` par défaut de `blueprint_resolver`.
Wrapper **mince** sur `_call_tool` — c'est là que vivent désormais `fastmcp.Client` (Streamable HTTP + Bearer), l'import paresseux (le socle forgemaster ne tire pas fastmcp au chargement) et l'`asyncio.run` (sûr : le daemon appelle depuis un thread sync, routes `def`, aucun event-loop courant). Cette fonction ne fait que fixer les arguments du contrat historique du seam `caller` : `read(type=blueprint, ref=<id>)`, et rend le `.data` s'il est un dict, sinon `None`.

## mcp.client.CapitalBrowser — parcours read-only du capital-token (routes `/api/capital/*`)
`src/forgemaster/mcp/client.py:135` · instancié par `capital_browser`, injecté au router `routes/capital`. Navigue `list_types → list_collections → list_sections → read` (chaque méthode mint un JWT frais + appelle l'outil MCP éponyme). **Dégradation honnête à 3 états** — c'est le point sensible : (a) MCP non câblé (`FORGEMASTER_MCP_JWT_SECRET_REF` absent) ou secret `< 32` → `None` ; (b) **transport** injoignable (réseau/timeout — fastmcp émet un `RuntimeError`) → `None` ; (c) le MCP **répond mais l'outil échoue** sur la ressource (ref cassée, silo en défaut — `fastmcp.ToolError`/`McpError`) → **`CapitalServerError`** (détail serveur réel), **jamais avalé en `None`**. La route mappe `None`→**503** générique, `CapitalServerError`→**502**+detail (le mislabel « non câblé » corrigé, cf. CHANGELOG API/capital). `_is_server_tool_error` (import fastmcp paresseux) est le discriminateur (b)↔(c). **Ne pas** aligner `blueprint_resolver` là-dessus : taskmap attend son `None` total.

## terminal.pty.serve_project_terminal() — sert une session PTY **détachable** (async)
`src/forgemaster/terminal/pty.py:112` · appelé par le router terminal (`daemon/routes/terminal.py`, flavors shell et interview) APRÈS la garde CSWSH + `accept()`.
Réutilise la session vivante du registre pour `session_key` (ré-attache + rejeu du scrollback) ou en spawn une neuve avec `argv` dans `cwd` ; une session morte résiduelle est fermée d'abord, on repart propre. Annonce une frame TEXTE `{"t":"session","fresh":…}` (`fresh` distingue une session NEUVE d'une ré-attache — le client n'en fait qu'une bannière), puis délègue le relais à `_relay`, dont le **motif de sortie décide du sort du process** : `replaced` (un nouveau client a pris la session) → sortir sans y toucher ; `disconnect` avec process VIVANT → **`detach()`**, le shell survit et le reaper le TTL-era — c'est la feature même, plus de kill à la déconnexion ; sinon (EOF réel, process mort) teardown final `close()` + retrait du registre, précédé — sur EOF seulement, là où un client regarde — d'une frame `{"t":"exit", code, reason}` dont la `reason` vient de `classify_exit` (`clean` / `failed_start` / `crash`, contrat WS lu par l'UI). Chaque flavor porte sa propre `session_key` (`<projet>` pour le shell, `interview:<projet>` pour l'interview via `interview_argv`) → sessions distinctes, persistance et fraîcheur indépendantes, aucune collision. Frames BINAIRES = frappes → écrites telles quelles dans le PTY ; frames TEXTE = contrôle JSON (`parse_control` → resize). La gate de session et l'audit open/close restent à la charge de l'appelant, **avant** ce point d'entrée.

## terminal.registry.PtySessionRegistry — le registre qui rend les sessions détachables
`src/forgemaster/terminal/registry.py:176` · instancié au lifespan du daemon (`daemon/app.py`, porté par `app.state.terminals`) et passé à `serve_project_terminal` par le router.
C'est le module qui a **remplacé** le pont PTY d'origine : le shell ne meurt plus avec le WebSocket. Un `PtySession` détient le master fd + un **scrollback borné** (`SCROLLBACK_CAP` = 256 KiB, fenêtre glissante) ; le reader OS est installé **une fois** au spawn et **survit** détach/ré-attache — la sortie continue d'être bufferisée sans client attaché — puis retiré dès l'EOF (un fd en EOF reste « lisible » : sans ce retrait le loop spinnerait à 100 % CPU jusqu'au reap). Le consommateur lit par **curseur d'offset absolu** : un client trop lent est ramené au début de la fenêtre courante au lieu de faire gonfler une file (plus de `Queue` non bornée). L'`epoch` s'incrémente à chaque `attach` → l'ancien pump détecte qu'il a été remplacé et sort **sans** teardown. Le registre indexe une session par clé, et un **reaper** (`run_reaper`, tâche de fond du lifespan) ferme périodiquement les sessions **détachées** qui sont mortes ou détachées depuis plus de `DETACH_TTL_S` (30 min) — **jamais** une session attachée, son pump la possède. `clock` et `killer` sont injectables (invariant forge : I/O injectable → testable sans tuer de vrai process ni attendre l'horloge réelle).

## terminal.pty.parse_control() — décode un message de contrôle resize
`src/forgemaster/terminal/pty.py:71` · appelé par `_relay` (branche frame TEXTE du sens WS→PTY).
PUR. JSON → `(rows, cols)` si `{"type":"resize", cols, rows}` valide, sinon `None`. Bornes défensives : `cols` clampé `[1,500]`, `rows` `[1,300]` ; texte non-JSON ou champ manquant → `None`.

## terminal.pty.resolve_workdir() — workdir borné anti-traversal
`src/forgemaster/terminal/pty.py:63` · appelé par le router terminal pour poser le `cwd` du PTY.
Résout `<projects_root>/<project>[/<subpath>]` via `fs.safe_path` (#4) : `subpath` relatif résolu sous la racine, tout `..` qui sort → `ValueError`. Défaut = racine du projet (contient `sot.git` + `worktrees/`).

## Zones non détaillées
- `codemap_argv` (`index.py:39`) / `CodemapError` (`index.py:47`) — argv `sys.executable -m codemap` (pas de dépendance PATH systemd) et type d'erreur signal ; triviaux.
- `_parse_json` (`flow.py:49`) — parse défensif du stdout CLI (rc 0 même sur `ok:false`) ; illisible → `CodemapError`.
- `_relay` (`pty.py:157`) — le relais bidirectionnel lui-même : deux tasks (`pty_to_ws`, qui lit le scrollback par curseur ; `ws_to_pty`), la première terminée gagne et l'autre est annulée ; une exception de transport (client coupé net) est traitée comme une déconnexion — loggée, jamais avalée.
- `PtySession` (`registry.py:51`) / `run_reaper` (`registry.py:225`) — la session elle-même (spawn, attach/detach, scrollback, close) et la boucle de reap périodique ; sémantique détaillée dans la section registry ci-dessus.
- helpers PTY `_set_winsize` (`pty.py:87`) — ioctl `TIOCSWINSZ` ; et `_terminate` (`registry.py:35`, **plus dans `pty.py`** depuis l'extraction du registre) — kill de groupe SIGTERM → grâce → SIGKILL anti-zombie, appelé par `PtySession.close`.
- `shell_env` (`pty.py:51`) / `local_shell_argv` (`pty.py:32`) — env couleur forcé (`TERM=xterm-256color`, `COLORTERM=truecolor`) et argv `bash -l` ; PURs.
