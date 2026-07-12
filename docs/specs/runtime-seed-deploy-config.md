# spec — Runtime : semer la config de run par type (P3)

> Cible : overlays `provision/bundles/types/{service-api,front-ts}/` (fichiers `compose.yaml`, `Dockerfile`,
> stub d'app, `.dockerignore`) + `runtime/engine.py` (pré-vol « pas de compose → refus honnête ») +
> `provision/__init__.py` (`_walk_files` robuste aux artefacts de compilation). Consomme le système de
> bundles typés (`load_bundle` = `base ⊕ overlay`) ; complète le contrat compose de P2.

## Problème tranché

P2 a livré le moteur de run mais **défère le `compose.yaml` à P3** : un projet frais n'avait ni compose, ni
Dockerfile, ni code applicatif (`service-api` naît en « amorçage »). Le smoke P2 devait **injecter** un compose
à la main, et il tirait une `image:` plutôt que de **builder**. P3 rend un projet frais **déployable sans
aucune édition** : `create --type <t>` sème la config de run complète dans le SoT (dev+main), et `deploy up`
**build depuis le Dockerfile semé** → 200 out-of-the-box.

## Règles verrouillées

1. **Seuls les types-SERVICE portent une config de run.** `service-api` + `front-ts` sèment
   `compose.yaml` + `Dockerfile` + un **stub d'app runnable** + `.dockerignore`. `cli-tool` et `generic` n'en
   sèment **pas** : un CLI / un projet générique n'expose aucun service long-running → non-déployable **par
   nature** (propriété assumée, pas un oubli).
2. **Fichiers overlay-only, ajout pur.** La composition whole-file (`base | overlay`) **ajoute** ces fichiers
   (la base ne les a pas → zéro collision). Le seed reste déterministe (lecture triée, `|` déterministe).
3. **Le compose BUILD depuis le repo, jamais une image figée.** `build: .` (contexte = racine de l'arbre
   archivé, `Dockerfile` canonique). Pas d'`image:` en dur → l'app du projet est ce qui tourne.
4. **Port publié = port injecté, fail-loud.** `ports: ["${COCKPIT_PORT:?…}:8000"]` — interpolation
   `${VAR:?err}` (échec bruyant si le cockpit n'injecte pas le port). Jamais un port en dur, jamais de
   faux-vert. Le stub écoute `0.0.0.0:8000` (contrat interne uniforme).
5. **Namespace non figé dans le fichier.** Le nom de compose-project (frontière d'isolation P2) vient de
   l'orchestrateur (`-p` / `COMPOSE_PROJECT_NAME`), **jamais** du `compose.yaml` semé → l'isolation reste
   structurelle et le payload reste **générique** (aucun slug en dur).
6. **Stub zéro-dépendance.** `service-api/app.py` = `http.server` (stdlib), `front-ts/server.mjs` = `node:http`
   natif — aucun `pip install`/`npm install` au build → déterministe, rapide, sans flakiness réseau. C'est un
   **amorçage** : l'humain le remplace par le vrai service en gardant l'écoute sur 8000.
7. **Pré-vol honnête côté engine.** `deploy` sonde la présence d'un compose à la racine de l'arbre archivé ;
   absent → `ValueError` clair (« n'expose pas de service déployable ») posée en `unhealthy`, **avant** tout
   appel backend (→ 400 route / `erreur` CLI). Remplace une erreur `compose` opaque par une dégradation lisible.
8. **Payload = sources, jamais du byte-code.** `_walk_files` **exclut** `__pycache__/` et `*.pyc` : introduire
   un `.py` dans un payload ne doit ni casser la lecture UTF-8 du loader, ni semer un binaire dans le SoT d'un
   projet.

## Invariants de test (`tests/test_provision.py`, `tests/test_runtime.py`)

- `load_bundle("service-api"|"front-ts")` contient `compose.yaml`, `Dockerfile`, le stub (`app.py`/`server.mjs`),
  `.dockerignore` — non vides ; `load_bundle("generic"|"cli-tool")` **n'a ni** compose **ni** Dockerfile.
- Le `compose.yaml` semé parse en YAML, porte `build: "."` (pas d'`image`), interpole `${COCKPIT_PORT:?` et
  publie `:8000`.
- Config **générique** : ni `cockpit-…` (namespace) ni slug d'exemple en dur dans compose/Dockerfile/stub.
- `engine.deploy` sur un arbre **sans** compose → `ValueError` + `unhealthy`, backend **jamais** appelé.
- Route `POST …/dev/up` sur un projet `service-api` (SoT réellement semé, `git.archive` réel) → `running`.

## Prouvé live (smoke réel, podman-rootless)

`create --type service-api` puis `create --type front-ts` (aucune édition) → le SoT porte `compose.yaml`,
`Dockerfile`, le stub à la racine. `deploy up` **build depuis le Dockerfile semé** → `running` sur ports
distincts du pool deploy (5250/5251), **HTTP 200/200** (service-api rend son JSON d'amorçage, front-ts son
HTML), namespaces `cockpit-demo-svc-dev_*` ≠ `cockpit-demo-front-dev_*`. Cas **négatif** : `create --type
cli-tool` → `deploy up` **refusé proprement** (`erreur : … n'expose pas de service déployable`, rc=1, aucun
conteneur créé).
