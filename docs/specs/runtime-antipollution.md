# spec — Runtime : durcir l'anti-pollution inter-projets (P4)

> Cible : `runtime/backend.py` (`_compose` : allowlist d'env, `_COMPOSE_ENV_ALLOW`) + `secrets/__init__.py`
> (`scoped_cred_resolver` : ACL par projet, additif). Verrouille par des tests deux invariants déjà
> structurels (frontière FS/réseau du conteneur ; pools de ports disjoints). Consomme l'unité d'isolation de
> P2 (le compose-project) ; ne change ni signature ni contrat existant.

## Problème tranché

Hors-Proxmox, le CT ne donne plus « gratuitement » la frontière d'isolation ; il faut la re-gagner
**côté control-plane**. DoD binaire (tranché bosse) : *le service de A ne lit ni les secrets ni les fichiers
de B*, un test le prouve. L'étude grounded a montré **une seule** surface réellement atteignable — l'env du
daemon hérité en bloc par la CLI compose — plus des frontières déjà structurelles à **verrouiller** (FS/réseau,
ports) et un durcissement control-plane défensif (ACL secrets). Fork tranché : **fermer la fuite + ACL**, SANS
construire d'injection secret→conteneur (aucun service consommateur aujourd'hui → pas de feature en avant).

## Règles verrouillées

1. **L'env passé à la CLI compose est une allowlist stricte.** `_compose` ne fait plus `{**os.environ}` : il
   part de `_COMPOSE_ENV_ALLOW` (le strict nécessaire pour que podman/docker compose tourne — `PATH`, `HOME`,
   `XDG_RUNTIME_DIR`, locale, chemins de config containers/docker s'ils sont posés) ⊕ l'overlay explicite de
   l'engine (`FORGEMASTER_PORT`, `COMPOSE_PROJECT_NAME`). **Aucun secret du daemon** (`BWS_ACCESS_TOKEN`,
   `FORGEMASTER_*`, `GITHUB_TOKEN`…) n'atteint le build/run d'un service, même présent dans l'environnement du
   daemon. L'env de run est scopé **par construction**, jamais hérité en bloc.
2. **Résolution de secrets scopée au projet (ACL, opt-in).** `scoped_cred_resolver(settings, conn, slug)` ne
   résout QUE le `credential_ref` **lié à `slug`** (`projects.credential_ref`) ; un ref d'un autre projet (ou
   d'aucun) dégrade en `''`. **Additif** : `cred_resolver` global reste inchangé (bootstrap/adoption
   multi-refs). La *policy* d'appartenance vit dans la couche `secrets`, jamais dans `git/internal`
   (invariant conservé). **Lazy/Total** : store construit à la demande, hors-scope/absent/illisible → `''`,
   jamais d'exception au point de résolution.
3. **Frontière FS/réseau structurelle (verrouillée, pas ajoutée).** Le contexte de build/run vit sous
   `<projects_root>/<slug>/deploy/<branch>` : l'image ne reçoit que le `git archive` de **son** SoT via
   `COPY . .`. Le compose semé ne déclare **ni `volumes:` ni `networks:`** → aucun bind vers le FS hôte,
   réseau podman **par compose-project** (jamais un réseau partagé où A joindrait B). L'isolation est une
   propriété du payload + du namespace, pas une config à ajouter par projet.
4. **Pools de ports disjoints.** Le pool **deploy** (`engine.DEPLOY_RANGE = 5250-5329`) et le pool **worktree**
   (`ports.DEFAULT_RANGE = 5170-5249`) ne se recouvrent jamais ; `UNIQUE(port)` global empêche toute collision
   au sein d'un pool. Un port de service ne peut coïncider ni avec un autre service ni avec un worktree.
5. **Ce que P4 ne fait PAS (frontière assumée).** Pas d'injection secret→conteneur (env-file par projet) :
   aucun service n'a besoin d'un secret aujourd'hui (le stub semé n'en consomme aucun). Le mécanisme arrivera
   quand un vrai service en aura besoin — ancré alors sur l'ACL (règle 2) déjà en place.

## Invariants de test (`tests/test_runtime.py`, `tests/test_provision.py`, `tests/test_secrets_acl.py`)

- **Env scellé** : avec `BWS_ACCESS_TOKEN`/`FORGEMASTER_ADMIN_TOKEN`/`GITHUB_TOKEN` posés dans l'env du daemon,
  l'env passé au runner compose n'en contient **aucun** (ni clé, ni valeur) ; `set(env) ⊆ allowlist ∪ overlay`.
- **ACL** : `scoped_cred_resolver(slug="alpha")` résout le ref d'alpha, refuse (`''`) le ref de beta — alors
  que `cred_resolver` global, lui, le résout (contraste). Projet inconnu / ref vide / projet sans ref lié → `''`.
- **FS** : `deploy_dir_for` par (projet, branche) → chemins distincts, bornés sous `projects_root`, aucun niché
  dans un autre ; le compose semé n'a ni `volumes:` ni `networks:` (top-level et service).
- **Ports** : `DEPLOY_RANGE` et `DEFAULT_RANGE` sont des intervalles **disjoints**.

## Prouvé live (smoke réel, podman-rootless)

Deux projets `service-api` (A, B) déployés simultanément → 200/200, namespaces distincts (P2/P3). Avec
`BWS_ACCESS_TOKEN=<sentinelle>` dans l'env du daemon : `podman exec <conteneur-A> env` **ne montre pas** la
sentinelle (le secret du daemon n'a pas fui dans le build/run) ; `podman exec <conteneur-A> ls /app` ne montre
que l'arbre de A (aucun fichier de B). ACL (unitaire) : `scoped_cred_resolver(slug="a")` sur le ref de B → `''`.
