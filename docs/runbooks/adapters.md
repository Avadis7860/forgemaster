# adapters — runbook (adaptateurs vers les pairs : codemap embarqué (index/flow), résolveur blueprint MCP injecté dans taskmap, pont PTY web)

Trois ponts du cockpit vers ses outils-pairs, chacun consommé en **boîte-noire** derrière un seam injectable. `codemap` (index matérialisé + requêtes de flot) alimente la route codemap du daemon ; `mcp.client.blueprint_resolver` est injecté dans `taskmap.context` pour la résolution blueprint-mode, avec **dégradation honnête** si le MCP est absent ; `terminal.pty` relaie un PTY local vers le terminal web (xterm.js). Aucun n'invente : contrat de sortie ou `None`/erreur explicite.

## codemap.ensure_index() — garantit un index frais (SHA, schema)-bound
`src/cockpit/codemap/index.py:78` · appelé par `flow.list_operations`/`flow.flow` (donc la route codemap du daemon).
Résout le SHA de la réf (`InternalGit.feature_sha`) + la `schema_version` du contrat code-map → clé de cache `home/codemap/<projet>/<sha>/<schema>`. Cache-hit si le marqueur **cockpit** `.cockpit-index-built` (ligne 34) est présent ; sinon `git archive <sha>` matérialise l'arbre puis `codemap build --root`, marqueur écrit **après** rc 0. Toute panne (réf absente, archive/build KO, version illisible) → `CodemapError`. Le `runner` (seam `core.run`) est injectable.

## codemap.IndexHandle — poignée d'un index matérialisé
`src/cockpit/codemap/index.py:48` · rendu par `ensure_index`, consommé par `flow`/`list_operations`.
`dataclass(frozen=True)` — `(project, ref, sha, root)`. `root` est la racine de l'arbre extrait (contient `.codemap/`), passée en `--root` aux requêtes `codemap flow`.

## codemap.codemap_schema_version() — négocie le contrat AVANT tout build
`src/cockpit/codemap/index.py:59` · appelé par `ensure_index` (moitié de la clé de cache).
Lit `codemap --schema-version` **sans index** : câble la clé de cache et négocie la compat avant de bâtir. **Non mémoïsé** (un `id(runner)` peut être réutilisé après GC → valeur périmée ; le CLI imprime une constante, coût négligeable hors boucle chaude). Sortie vide / rc non-0 → `CodemapError`.

## codemap.index_dir_for() — chemin de cache dérivé (sha × schema)
`src/cockpit/codemap/index.py:71` · appelé par `ensure_index`.
Retourne `home/codemap/<projet>/<sha>/<schema>`. Invalidation double : un nouveau SHA (nouveau code) **OU** un nouveau `schema` (upgrade de code-map) ouvre un dossier neuf — l'ancien index n'est jamais servi périmé (ferme le trou « vider le cache à la main après déploiement »). PUR (composition de chemin).

## codemap.flow.flow() — sous-graphe de flot d'une opération
`src/cockpit/codemap/flow.py:33` · appelé par la route codemap du daemon.
`ensure_index` (cache SHA) puis `codemap flow <op> --format json --depth --root`. Renvoie `{ok, operation, entry, nodes[], edges[], stats}` **relayé tel quel** — `ok:false` (opération introuvable/ambiguë) compris : l'appelant décide du rendu. Stdout illisible → `CodemapError` (jamais un demi-résultat). `depth` défaut 6.

## codemap.flow.list_operations() — entry points découverts
`src/cockpit/codemap/flow.py:22` · appelé par la route codemap du daemon.
`ensure_index` puis `codemap flow --list --root`. Renvoie `{operations:[{operation,entry,kind}], engine}` (routes API + verbes CLI). Build ou sortie illisible → `CodemapError`.

## mcp.client.blueprint_resolver() — résolveur blueprint injecté dans taskmap
`src/cockpit/mcp/client.py:55` · injecté au seam `taskmap.context.build_context`/`doctor` (`BlueprintResolver = id -> dict|None`) ; le board (P3) s'en sert pour `resolved:true` sur un `features.blueprint`.
Ferme sur `settings` et rend `resolve(bp_id)`. **Invariant de dégradation honnête** : secret non câblé (`COCKPIT_MCP_JWT_SECRET_REF` absent) → `None` ; secret `< 32` octets → `None` ; toute exception (mint/réseau/MCP pendu) capturée → `None` ; réponse non-dict ou vide → `None`. **Jamais inventé, jamais propagé** — exactement le contrat qu'attend `taskmap.context._blueprint_verdict` (`None`/`{}` = liaison morte signalée). `secret_ref`/`endpoint`/`resolver`/`caller`/`timeout` sont des seams (défaut réseau réel = `_read_blueprint`, timeout 5 s).

## mcp.client._read_blueprint() — coquille réseau réelle (fastmcp)
`src/cockpit/mcp/client.py:38` · seam `caller` par défaut de `blueprint_resolver`.
`read(type=blueprint, ref=<id>)` via `fastmcp.Client` (Streamable HTTP + Bearer). Import fastmcp **paresseux** (le socle cockpit ne le tire pas au chargement). Le daemon appelle depuis un thread sync (routes `def`) → `asyncio.run` est sûr (aucun event-loop courant). Retourne `.data` ou `None`.

## terminal.pty.pty_bridge() — pont PTY ↔ WebSocket (async)
`src/cockpit/terminal/pty.py:87` · appelé par le router terminal (après `accept()` + gate de session).
Ouvre un PTY local pilotant `argv` dans `cwd`, relaie octets PTY↔WS via deux tasks (`pty_to_ws`/`ws_to_pty`) jusqu'à la fin de l'une, puis nettoie (`remove_reader`, close master, `_terminate` le groupe de process, `websocket.close()`). Agnostique au transport (legacy = argv ssh ; ici argv `bash -l` local — même corps). Frames BINAIRES = frappes → écrites telles quelles ; frames TEXTE = contrôle JSON (`parse_control` → resize). La gate de session + l'audit open/close sont à la charge de l'appelant, **avant** ce pont.

## terminal.pty.parse_control() — décode un message de contrôle resize
`src/cockpit/terminal/pty.py:56` · appelé par `pty_bridge` (branche frame TEXTE).
PUR. JSON → `(rows, cols)` si `{"type":"resize", cols, rows}` valide, sinon `None`. Bornes défensives : `cols` clampé `[1,500]`, `rows` `[1,300]` ; texte non-JSON ou champ manquant → `None`.

## terminal.pty.resolve_workdir() — workdir borné anti-traversal
`src/cockpit/terminal/pty.py:48` · appelé par le router terminal pour poser le `cwd` du PTY.
Résout `<projects_root>/<project>[/<subpath>]` via `fs.safe_path` (#4) : `subpath` relatif résolu sous la racine, tout `..` qui sort → `ValueError`. Défaut = racine du projet (contient `sot.git` + `worktrees/`).

## Zones non détaillées
- `codemap_argv` (`index.py:37`) / `CodemapError` (`index.py:44`) — argv `sys.executable -m codemap` (pas de dépendance PATH systemd) et type d'erreur signal ; triviaux.
- `_parse_json` (`flow.py:49`) — parse défensif du stdout CLI (rc 0 même sur `ok:false`) ; illisible → `CodemapError`.
- helpers PTY `_set_winsize` (`pty.py:72`) / `_terminate` (`pty.py:76`) — ioctl `TIOCSWINSZ` et kill de groupe SIGTERM→SIGKILL anti-zombie.
- `shell_env` (`pty.py:39`) / `local_shell_argv` (`pty.py:33`) — env couleur forcé (`TERM=xterm-256color`, `COLORTERM=truecolor`) et argv `bash -l` ; PURs.
