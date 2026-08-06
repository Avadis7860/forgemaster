# cli — runbook (spine CLI : surface de commandes + dispatch vers les `cli_dispatch` de chaque sous-système)

`src/forgemaster/cli.py` est la porte unifiée `forgemaster` (entry-point `console_scripts`). Elle assemble tout
l'arbre argparse **sans tirer aucune couche lourde** (fastapi/uvicorn/couches stub), puis route la
sous-commande parsée vers un handler mince qui, lui, importe la couche **paresseusement** et l'appelle.
Deux responsabilités : (1) déclarer la surface (`build_parser`), (2) parser + configurer l'env + dispatcher
(`main` via la table `_HANDLERS`). Toute la logique métier vit dans les `cli_dispatch` des sous-systèmes.

## build_parser() — assemble l'arbre argparse complet (pur, import léger)
`src/forgemaster/cli.py:24` · appelé par main()
Entrées : aucune. Comportement : construit le parser racine `forgemaster` (+ `--version`), un parent `common`
(`--home`, `--projects-root`) hérité par chaque sous-parser, puis déclare les **27** sous-commandes de la spine
et leurs actions. Les groupes à sous-actions : `project` (create/list/get), `tool` (sync), `tools` (install),
`bundle` (list/validate/show/version/derive), `scaffold` (reseed), `roadmap` (add-feature/set-deps/show/check),
`task` (add/set-deps/next), `reliability` (show/mark), `gate` (review/review-dispatch/verify/toolchain/
woaw-dispatch), `onboard` (status/link/unlink), `mcp` (wire). Les verbes plats : `dispatch`, `cost`, `run`,
`abort`, `refix`, `redrain`, `interview`, `inspire`, `upload`, `deploy`, `merge`, `bootstrap`, `serve`,
`setup`, `install-service`, `doctor`. **Cet inventaire est daté, pas un contrat** — la vérité vit dans le code,
`grep 'add_parser' src/forgemaster/cli.py` la rend en une commande ; ce runbook dit la FORME (un parent commun, des
groupes, `set_defaults(func=…)`), pas la liste. Sortie :
l'`ArgumentParser`. Invariant : **pur et sans dépendance lourde** — le seul import est
`provision.list_valid_types` (stdlib-only, fail-closed : un overlay cassé n'est pas offert à `--type`), de
sorte que `--help` marche et que le parser se construit même quand les couches sont des stubs. Appelants :
`main()` (et les tests de câblage argparse).

## main() — parse, configure l'env, résout Settings, dispatche
`src/forgemaster/cli.py:412` · point d'entrée console_scripts
Séquence : `build_parser().parse_args(argv)` → `_autoload_env(args)` (parité CLI ↔ service) →
`settings = _settings(args)` → lookup `handler = _HANDLERS[args.command]` → `return handler(settings, args)`, sous un `try` qui attrape `SchemaTooNew` (rc 1 + message) : c'est le seul endroit qui voit tous les verbes, et une base trop neuve est un état de l'instance, pas la panne d'un verbe.
Le cœur du dispatch est la **table `_HANDLERS`** (`cli.py:666`), un dict `command → _h_*` : chaque `_h_*`
reçoit `(settings, args)`, importe SA couche en **import paresseux** (jamais au niveau module) et délègue à
son `cli_dispatch(settings, args)` en retournant le code de sortie. Ce pattern « handler mince =
délégateur » est ce qui garde `build_parser` léger : aucune couche n'est tirée tant que la sous-commande
correspondante n'est pas effectivement invoquée. Quelques handlers routent en interne selon `args.action`
(`_h_roadmap` → `check` vs `model` ; `_h_gate` → `review`/`review-dispatch`/`verify`/`toolchain`) ou appellent
une fonction dédiée plutôt qu'un `cli_dispatch` (`_h_serve` → `app.serve`, `_h_setup` → `webbuild`,
`_h_install_service` → `service.install_service`). Sortie : le code de retour du handler.

## _settings() / _autoload_env() — résolution de config avant dispatch
`src/forgemaster/cli.py:387` (`_settings`) · `src/forgemaster/cli.py:397` (`_autoload_env`) · appelés par main()
`_settings(args)` retourne `Settings.resolve(home=…, projects_root=…)` en lisant les flags `--home` /
`--projects-root` (via `getattr`, tolérant à leur absence), puis pose `allow_unknown_schema` par `replace()` si
`--allow-unknown-schema` a été demandé — jamais depuis l'env, pour que la porte du garde de schéma reste un
laissez-passer d'UNE invocation. `_autoload_env(args)` charge
`$FORGEMASTER_HOME/forgemaster.env` dans `os.environ` **avant** de résoudre les Settings, pour garantir la **parité
CLI ↔ service** : le service systemd lit son `EnvironmentFile`, la CLI doit voir la même config (dont le
câblage MCP) — sinon un `forgemaster dispatch` lancé en shell perdait le MCP en silence. Home résolu selon la
même priorité que `_settings` (flag `--home` > `$FORGEMASTER_HOME` > `DEFAULT_HOME`) ; invariant : le fichier
**ne surcharge jamais** une clé déjà présente dans l'environnement réel.

## Zones non détaillées
- **Les 27 handlers `_h_*`** (`cli.py:339`–`543`) : délégateurs **minces** vers le `cli_dispatch` (ou la
  fonction dédiée) de chaque sous-système, tous sur le contrat `(settings, args) -> int` avec import
  paresseux de la couche. Ne pas les lire un par un — leur logique vit dans la couche cible. Liste et
  cible : `_h_project`→`projects.registry` · `_h_tool`→`toolsync` · `_h_tools`→`tools` ·
  `_h_bundle`→`provision.manage` · `_h_roadmap`→`roadmap.check`/`roadmap.model` ·
  `_h_task`→`roadmap.resolver` · `_h_dispatch`→`dispatch.worker` · `_h_run`→`dispatch.orchestrator` ·
  `_h_deploy`→`runtime.engine` · `_h_gate`→`gate.review`/`dispatch.reviewer`/`gate.verify`/`gate.toolchain` ·
  `_h_merge`→`gate.merge` · `_h_onboard`→`onboarding` · `_h_bootstrap`→`bootstrap` · `_h_serve`→`daemon.app`
  (`serve`) · `_h_setup`→`webbuild` · `_h_install_service`→`service` · `_h_doctor`→`doctor` ·
  `_h_mcp`→`provision.mcp`.
- **La table `_HANDLERS`** (`cli.py:666`) : le mapping `command → _h_*`, décrit dans la section `main()`.
- **Le détail des sous-parsers/arguments** de chaque sous-commande (flags, `choices`, `nargs`) : lire
  directement `build_parser` (`cli.py:24`–384) — c'est de la déclaration argposée, pas de la logique.
