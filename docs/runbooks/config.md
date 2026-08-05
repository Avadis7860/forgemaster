# config — runbook (résolution config (injection explicite) + bootstrap idempotent d'un environnement)

`config` est le SOCLE : un résolveur générique de racines, sans aucune notion métier (vault, proxmox, CT, ssh — correctif du legacy `server.py` qui codait ces chemins en dur). Sa règle unique : **injection explicite, jamais de god-module**. `Settings` est gelé, dérivé une fois au démarrage, puis passé de couche en couche. `bootstrap` s'appuie dessus pour amorcer l'édition maintainer de façon **idempotente** (adoption d'outils déclarés, ré-exécution sûre).

## Settings — racines résolues du forgemaster (dataclass frozen)
`src/forgemaster/config.py:32` (classe) · résolveur `Settings.resolve` en `:64` · consommé par toutes les couches (bootstrap, registry, db, secrets) qui le reçoivent en argument
Deux racines indépendantes : `home` (état — base SQLite `db_path`, `logs_dir`, coffre `secrets_dir`) et `projects_root` (repos gérés). Plus **trois** réglages : `secret_store` (`"file"` | `"bws"`), `compose_cmd` (préfixe moteur compose, normalisé en tuple) et `ws_allowed_origins` (origines WebSocket autorisées **en plus** du same-origin et du dev Vite — le cas « daemon derrière un reverse-proxy à nom public différent » ; défaut **vide**, donc rien n'est ouvert par accident, cf. `daemon/wsguard`). Résolution **par racine** avec priorité `argument explicite > variable d'env > défaut` (`_pick`, `:88`) ; `~` développé et chemin rendu absolu (`_norm`, `:97`). Invariant : `@dataclass(frozen=True)` — immuable, jamais un module-global mutable ; c'est l'anti god-module câblé dans le type même.

## run_bootstrap() — adoption idempotente des outils du manifeste
`src/forgemaster/bootstrap.py:81` · appelé par `cli_dispatch` (`:151`), après `load_manifest` + `_resolve_shared_ref`
Boucle sur chaque entrée du manifeste et adopte l'outil via `registry.create_project(source_url=…)` — **provenance seule, aucune destination de push** : `source_url` devient l'`origin` du clone bare (ce que `toolsync` re-fetch), et l'outil adopté ne porte **pas** de `mirror_remote`, dont `onboarding.status()` déduirait qu'un token de push est requis (jusqu'au 2026-08-05, la copie `mirror_remote=source_url` réclamait à tout installateur un token de push vers les dépôts du mainteneur — cf. migration schéma v20). Qui veut pousser un outil pose son miroir explicitement (`set_mirror_remote`). **Idempotent** : un slug déjà présent → `skipped`, jamais de doublon (`:94`). Résolution du credential **par entrée** : `credential_ref` de l'entrée (un token par repo, D6), sinon le `shared_ref` du wizard/`--token-file`, sinon anonyme (repo public, D7). Une erreur opérationnelle (clone injoignable, course) → `failed` **isolé**, la boucle continue (`:105`). Retourne `{created, skipped, failed:[{slug, error}]}`.

## load_manifest() — lecture + validation fail-loud du manifeste
`src/forgemaster/bootstrap.py:51` · appelé par `preview` (`:117`) et `cli_dispatch` (`:151`)
Charge `<FORGEMASTER_HOME>/bootstrap.yaml` et le **VALIDE** strictement. **Absent → `None`** (no-op propre, l'install reste générique). **Présent mais invalide → `ValueError`** (abort loud, jamais un demi-amorçage) : YAML illisible, clé `tools` absente/non-liste, entrée non-mapping, `slug`/`source_url` manquant, `credential_ref` non-string. Retourne une liste normalisée `{slug, source_url, kind, credential_ref}` (`kind` défaut `"tool"`). C'est le point où la distinction structurel (fail-loud) vs opérationnel (best-effort dans `run_bootstrap`) se tranche.

## preview() — aperçu idempotent (GET) sans effet
`src/forgemaster/bootstrap.py:117` · surface lecture (wizard `/setup`), miroir sans effet de `run_bootstrap`
GET pur : décrit ce que l'amorçage FERAIT, **aucun effet, aucun secret**. `available` = manifeste présent & valide ; par outil, `adopted` = slug déjà présent en base. Manifeste absent → `available:false` (install générique). Manifeste invalide → propage `ValueError` (mappé 400). Retourne `{available, tools:[{slug, source_url, kind, adopted}], adopted, total}` — le compteur `adopted` alimente l'UI du wizard.

## write_template() — gabarit commenté no-overwrite
`src/forgemaster/bootstrap.py:131` · appelé par `cli_dispatch` sur `--init` (`:150`)
Écrit `_TEMPLATE` (manifeste gabarit commenté, **sans aucun secret** : `credential_ref` = réf opaque, jamais un token) sous `FORGEMASTER_HOME` (créé si besoin). **Garde no-overwrite** : un manifeste déjà présent → `ValueError` (jamais écraser, même invariant que `service.py`). Retourne le chemin écrit.

## Zones non détaillées
- `_pick`/`_norm` (config), `manifest_path`/`_resolve_shared_ref`/`cli_dispatch` (bootstrap) : helpers/adaptateurs triviaux (sélection premier-non-vide, normalisation de chemin, chemin déterministe, mise-en-store du token partagé, routage argparse).
