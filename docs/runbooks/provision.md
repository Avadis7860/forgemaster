# provision — runbook (provisioning : bundles d'archétype (types), facettes, câblage MCP dans un worktree (token scopé))

Le paquet `provision` compose le « Toolkit auto-travaillable » semé dans le SoT de chaque projet créé par le forgemaster. Trois briques : les **bundles** (`base ⊕ overlay(type)`, registre = filesystem, whole-file, déterministe) que `create_project` valide puis sème ; les **facettes** qui alignent le worker sur le type de travail de sa feature (persona+méthode dans le prompt, hooks+permissions via `settings.local.json` gitignoré) ; et le **câblage MCP** qui injecte dans un worktree un `.mcp.json` porteur d'un JWT **scopé au projet** (`sub=forgemaster:<slug>`), minté à la demande, jamais baké dans le bundle. Invariant load-bearing : le token est re-minté à chaque dispatch, gitignoré, et le câblage absent dégrade en **no-op honnête** (worker sans MCP, zéro crash).

## load_bundle() — le bundle d'un type = base ⊕ overlay(type)
`src/forgemaster/provision/__init__.py:80` · appelé par `projects.registry.create_project` (via la policy), `read_bundle_manifest`, `validate_bundle`, `list_valid_types`, `load_launch_roadmap`, `load_payload`, `manage._show`.
Retourne le mapping `chemin-relatif-POSIX → contenu` composé par `base | overlay(type)` (whole-file : l'overlay écrase les clés communes, ajoute les siennes). `generic` = base seule. Déterministe (lecture triée des fichiers vendorés). Lève `BundleError` si `project_type` ∉ `discover_types()`.

## validate_bundle() — porte fail-closed avant toute copie
`src/forgemaster/provision/__init__.py:124` · appelé par `projects.registry.create_project`, `list_valid_types`, `manage._list/_validate/_show`.
Valide un bundle **avant** de le semer et lève `BundleError` sur : type hors registre ; manifeste absent/illisible ; `version` manquante ; `project_type` du manifeste ≠ nom du dossier ; `facets` vide ; `default_facet` ∉ `facets` ; facette déclarée sans dossier `.claude/facets/<f>/` de support. Plus trois blocs **optionnels** dont la présence impose la forme (absents ⇒ valides) : `[bundle.mcp]` doit être une table et son `corpus` un booléen — déclaration **sèche** du besoin de corpus, jamais un secret ni un endpoint (les silos pertinents vivent dans la prose du `CLAUDE.md §4`, seul SoT réellement lu par le worker) ; `reseed_owned` doit être une liste de chemins **présents dans le bundle composé** (on ne peut pas posséder un fichier qu'on ne sème pas) ; `[bundle.facet_models]` doit être une table dont chaque clé est une facette **déclarée** et chaque valeur un alias de modèle non vide. Aucune valeur de retour — soit ça passe, soit ça lève. Cette énumération EST le contrat : ce qui n'y figure pas n'est pas refusé.

## discover_types() — le registre, dérivé du filesystem
`src/forgemaster/provision/__init__.py:39` · appelé par `load_bundle`, `list_valid_types`, `manage._list/_validate/_version`.
Le registre des types = `("generic", *sous-dossiers triés de bundles/types/)`. `generic` toujours en tête. Ajouter un type = déposer `bundles/types/<type>/` — zéro enum en dur, zéro migration DB.

## list_valid_types() — la source unique des types offerts
`src/forgemaster/provision/__init__.py:178` · appelé par le dropdown de création (UI) et le durcissement des `choices` CLI.
Filtre `discover_types()` par `validate_bundle` (fail-closed) : un overlay cassé est **silencieusement écarté** (on n'offre jamais un type qu'on ne saurait pas semer). Chaque entrée : `{type, version, project_type, facets, default_facet, mcp}` — `mcp` étant la déclaration sèche du manifeste (`{}` si absente), ce qui permet à l'UI de savoir si le type demande un corpus sans lire un secret. Ordre de `discover_types` (generic en tête).

## read_bundle_manifest() — la table [bundle] du manifeste composé
`src/forgemaster/provision/__init__.py:106` · appelé par `list_valid_types`, `manage._list/_show/_version`.
Point d'accès amont : parse `.forgemaster/bundle.toml` du bundle composé (`load_bundle` + `_parse_manifest`) et rend la table `[bundle]` (version, project_type, facets, default_facet…). Sert la sélection, la provenance, la gestion.

## load_launch_roadmap() — graine de roadmap de lancement (fail-soft)
`src/forgemaster/provision/__init__.py:204` · appelé au seed d'un projet (create).
Parse `.forgemaster/launch-roadmap.yaml` du bundle composé (schéma = contrat `roadmap.yaml` SANS la clé `project:`, fournie au seed). Retourne `{}` **fail-soft** si le type ne porte aucune graine ; parse **strict** (YAML vendoré cassé = bug dev attrapé par les tests). Lève `BundleError` si type hors registre (via `load_bundle`).

## load_payload() — compat : le bundle générique (base seule)
`src/forgemaster/provision/__init__.py:254` · appelé par les appelants historiques non-typés.
Alias de compatibilité : `load_bundle("generic")`. Conservé pour le code qui ne type pas encore le projet.

## BundleError — l'erreur d'entrée du provisioning
`src/forgemaster/provision/__init__.py:33` · levée par `discover_types`-gated helpers, `load_bundle`, `_parse_manifest`, `validate_bundle`, `load_launch_roadmap`.
Sous-classe de `ValueError` → routée en 400 (API) / message CLI comme les autres erreurs d'entrée, sans handler dédié. Signale un type hors registre ou un manifeste invalide.

## resolve_facet() — la facette effective d'une feature
`src/forgemaster/provision/facet.py:26` · appelé au dispatch (via `roadmap/prompt.py` et l'activation worktree).
Résout dans l'ordre : `feature_facet` s'il est posé → sinon `default_facet` du `.forgemaster/bundle.toml` de la worktree → sinon `doc` (`_FALLBACK_FACET`). Fail-soft : manifeste absent/illisible → fallback, jamais de crash.

## activate_facet() — pose le settings.local.json gitignoré
`src/forgemaster/provision/facet.py:65` · appelé au dispatch (canal hooks+permissions).
Copie la source committée `.claude/facets/<facet>/settings.local.json` → `.claude/settings.local.json` (**gitignoré**, lu par `claude -p`). Idempotent (overwrite). Retourne le chemin écrit, ou `None` (fail-soft) si la facette n'a pas de `settings.local.json`. C'est la seule voie de différenciation par-feature, les worktrees partageant l'arbre committé.

## facet_dir() — le dossier source committé d'une facette
`src/forgemaster/provision/facet.py:21` · appelé par `activate_facet` (et tout lecteur de persona/méthode).
Pur : `<root>/.claude/facets/<facet>/`. La persona + méthode sont lues depuis ce dossier committé (injectées dans le prompt, zéro fichier posé).

## wire() — câble l'instance forgemaster-catalogs sur cette install (cœur partagé CLI + wizard)
`src/forgemaster/provision/mcp.py:207` · appelé par `cli_dispatch` (`forgemaster mcp wire`) **et** par la route onboarding du wizard.
Pose dans `forgemaster.env` une **référence opaque** au secret HMAC partagé (jamais le secret en clair) + l'endpoint, de sorte que le prochain dispatch injecte un `.mcp.json` valide (`inject_mcp_config`). Rend la ref posée. **Exactement une** voie parmi trois : `secret` (la valeur brute, que le wizard POSTe → `store.put` → ref), `secret_file` (même voie depuis un fichier — la CLI), ou `secret_ref` (bring-your-own UUID, **validée** via `store.get`). `live_env=True` reflète en plus la ref dans l'`os.environ` du process courant : le daemon la voit **sans restart** — c'est ce que fait le wizard ; la voie CLI, elle, demande un `systemctl restart forgemaster` pour recharger l'EnvironmentFile. Lève `MCPWireError` sur mauvais usage (zéro ou plusieurs voies), backend incompatible, secret `<32c` ou ref introuvable. Sans câblage, l'injection reste un no-op honnête.

## render_mcp_config() — la forme du .mcp.json (pure)
`src/forgemaster/provision/mcp.py:64` · appelé par `inject_mcp_config`.
Pur (hors lecture d'`os.environ`) : un `mcpServers` avec le label `vault-catalogs` (verbatim du contrat serveur CT 9118), `type: http`, l'`url` et un header `Authorization: Bearer <token>`. L'endpoint par défaut (`endpoint=None`) est résolu **LIVE** par `current_endpoint()`, jamais gelé à l'import — un re-wire prend effet au prochain rendu sans recharger le module.

## inject_mcp_config() — mint du token scopé + écriture chmod 600
`src/forgemaster/provision/mcp.py:87` · appelé au dispatch d'un worker (injection POST-création).
**Cœur de l'invariant token-scopé** : résout le secret HMAC via le coffre (`cred_resolver`, total), mint un JWT `mint_hs256(f"forgemaster:{slug}", …, aud=vault-catalogs, iss=vault-mcp, ttl=86400s)` — donc `sub=forgemaster:<slug>` **scopé au projet** — puis écrit `<worktree>/.mcp.json` (`chmod 600`, porte le Bearer → lecture propriétaire seule). **No-op honnête** (`None`, aucun fichier) si le ref n'est pas configuré ou si le secret est absent/`<32c`. Re-minté à chaque dispatch (just-in-time, jamais expiré au lancement).

## worktree_token() — lit le Bearer réellement servi (pur)
`src/forgemaster/provision/mcp.py:133` · appelé par `check_lifecycle`.
Lit et rend le Bearer du `.mcp.json` d'un worktree (le token effectivement servi au worker), ou `None` si fichier absent / illisible / forme inattendue. Pur — **ne mint pas** (le mint est dans `inject_mcp_config`).

## token_exp() — l'exp d'un JWT sans le vérifier (pur)
`src/forgemaster/provision/mcp.py:119` · appelé par `check_lifecycle`.
Décode le payload base64url et rend l'`exp` (epoch) **sans authentifier** — le doctor signale, il ne vérifie pas. `None` si token malformé ou sans `exp`. Pur.

## check_lifecycle() — diagnostic déterministe pour forgemaster doctor
`src/forgemaster/provision/mcp.py:147` · appelé par `forgemaster doctor`.
Retourne `{configured, healthy, reason, exp, stale}`, zéro réseau. Non câblé (pas de `FORGEMASTER_MCP_JWT_SECRET_REF`) → `configured=False, healthy=True` (install sans corpus privé, dégradation prévue) ; ref posée mais secret illisible/`<32c` → `configured=True, healthy=False` (câblage cassé → re-wire) ; sinon mint un token témoin (`forgemaster:doctor`) et scanne les `.mcp.json` des worktrees — un token expiré ou expirant dans la fenêtre d'un run (`_RUN_WINDOW_S=1800s`) = `stale` (faux-négatif void-runner : worktree non re-dispatché). `healthy` ⇔ mint OK et aucun stale.

## MCPWireError — l'erreur de câblage du wire
`src/forgemaster/provision/mcp.py:202` (`ValueError`) · levée par `wire`, interceptée par `cli_dispatch` et la route onboarding.
Signale un câblage refusé **avant tout effet** : zéro ou plusieurs voies de secret fournies, backend sans secret direct (bring-your-own attendu), secret `<32c` (HS256 exige au moins 32 caractères), ref introuvable dans le store. Sous-classe de `ValueError` — un appelant qui n'en sait rien la traite comme telle. À ne pas confondre avec l'**injection** (`inject_mcp_config`), qui ne lève pas : elle dégrade en no-op honnête quand le câblage est absent.

## wire_state() — l'état de câblage, sans révéler le secret
`src/forgemaster/provision/mcp.py:191` · lu par `onboarding.status` (axe `mcp` du wizard).
Rend `{wired, endpoint}` : la **présence** de la ref et l'endpoint résolu, jamais la valeur. C'est ce qui permet au wizard de dire « corpus privé câblé / pas câblé » sans jamais lire un secret.

## Zones non détaillées
- **Helpers privés de `__init__.py`** : `_walk_files` (parcours récursif trié, exclut `_SKIP_DIRS` + `*.pyc` — sources only, pas de binaire dans le SoT), `_read_tree` (arbre → mapping `chemin POSIX → texte`, fail-soft dossier absent → `{}`), `_parse_manifest` (table `[bundle]` d'un bundle composé, lève `BundleError` si absent/illisible). Plomberie déterministe sans surface publique.
- **`__init__.py`** — `read_reseed_owned` (les chemins que le scaffold POSSÈDE, relus au reseed) et `check_launch_roadmap_drift` (la graine de roadmap a-t-elle divergé de ce que le bundle sème aujourd'hui).
- **`facet.py`** — `resolve_facet_model` (le modèle de la facette, lu par `worker.dispatch_next` : le mécanique tolère un modèle plus léger, le gate reste le garde-fou).
- **`manage.py`** (`forgemaster bundle …`) : façade MINCE de présentation sur l'API `provision`, ne ré-implémente aucune logique. `_list` (une ligne/type, valides ✓ + cassés ✗, retour 0), `_validate` (diagnostic actionnable, **retour 1** dès un bundle invalide → gate/CI), `_show` (détail d'un bundle), `_version` (version nue scriptable), `cli_dispatch` (route `args.action` via `_ACTIONS`, `settings` inutilisé — registre = filesystem vendoré).
- **Nommage divergent** : le label serveur / `aud` (`vault-catalogs`) et l'`iss` (`vault-mcp`) reproduisent verbatim le contrat validé par le serveur forgemaster-catalogs (ex-CT 9113) ; le renommage `vault-catalogs → forgemaster-catalogs` est coordonné serveur-d'abord (backlog `forgemaster-catalogs-naming-coherence`), pas une demi-migration côté client.
