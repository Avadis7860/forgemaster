# spec — Runtime : observabilité des déploiements (P5)

> Cible : exposer l'état vivant des 2 déploiements (`main`/`dev`) d'un projet — **santé**, **logs**, **liens
> health-gated** — sans nouvelle machinerie. Réutilise `engine.status` (réconciliation live **déjà écrite** en
> P2) + ajoute une seule capacité backend (`logs()` borné) et un onglet Runtime. Ne change ni signature ni
> contrat existant.

## Problème tranché

P1-P4 ont livré le **run** (déployer, faire tourner, isoler) mais **aucune surface d'observation** : l'état de
run n'était lisible qu'au CLI (`forgemaster deploy status`), et le front deployments était greenfield. Sans
santé+logs visibles, la vérif e2e P6 (« deux projets tournent, chacun visible ») ne peut pas s'ancrer. DoD :
le forgemaster **montre** l'état de chaque déploiement (santé teintée, jamais un faux-vert), ses **logs** (bornés),
et un **lien qui n'ouvre le service que s'il répond**.

**Fork tranché (bosse) : logs = tail borné à la demande** (`compose logs --tail N`, GET read-only), PAS de
stream WS `compose logs -f` — un subprocess long-vécu à réaper serait de la complexité sans besoin prouvé. Le
live-follow arrivera si un vrai besoin le réclame, ancré sur la même route.

## Règles verrouillées

1. **La santé est une réconciliation live, séparée de la liste pure.** `GET .../deployments` reste **pur-DB**
   (goto-safe, deep-linkable) ; `GET .../deployments/{branch}/status` réconcilie la DB avec le réel
   (`engine.status` → `running`/`stopped`, ou `unhealthy` si la sonde échoue). Ce GET **à effet** est rattaché
   au RefreshButton, **jamais** au polling d'un runner *goto-only* — exactement le couple `GET /git` (pur) vs
   `GET /git/sync` (live) de la vue Git.
2. **`ps` et `logs` interrogent le MOTEUR directement, pas la CLI compose.** podman-compose 1.0.6 (le compose
   standalone par défaut) n'accepte ni `ps --format json`/`-a`, ni `logs --no-color/--no-log-prefix`,
   et **n'écrit pas fiablement** les logs sur stdout. Donc `ps` = `<engine> ps -a --format json --filter
   label=com.docker.compose.project=<name>` — le moteur est DÉRIVÉ (`compose_engine`, strippe `-compose`) car
   `cmd[0]` (`podman-compose`) N'EST PAS le moteur ; label posé par docker-compose ET podman-compose → état
   honnête par conteneur ; et `logs` = `<engine> logs --timestamps --tail <n> <cid>` par conteneur découvert
   (stdout **ET** stderr : un handler http logge souvent sur stderr). Cross-backend (moteur dérivé =
   podman|docker), **allowlist d'env P4
   préservée** (aucun secret ne fuit), borné (**jamais** `--follow`). `tail` clampé (`engine._LOGS_TAIL_MAX =
   1000`) + borné par la route (`Query(ge=1,le=1000)`).
3. **Toute sous-commande compose mutante reçoit `FORGEMASTER_PORT`.** Le compose est **re-parsé à chaque appel** et
   son `ports:` est fail-loud (`${FORGEMASTER_PORT:?}`, semé P3) → `down`/`restart` (qui passent par la CLI compose)
   injectent le port réservé (en DB), sinon podman/docker compose échoue au parse. `ps`/`logs` (direct moteur)
   n'en ont pas besoin. (Bug latent depuis P2, révélé par le premier smoke qui asserte le `status` live.)
4. **Vide honnête partout.** Un déploiement jamais monté (`no_deploy` / pas de workdir) rend `lines: []` et
   `status: no_deploy` — jamais une erreur, jamais un faux-vert. Le backend n'est même pas appelé (pas de
   sonde moteur sur un projet sans workdir).
5. **Le lien de service est health-gated.** Dans l'UI, le lien `main↗/dev↗` n'est **actif** (ancre `href`) que
   si le déploiement est `running` ; sinon il est **inerte** (jamais un lien mort vers un service arrêté). La
   teinte de statut vit dans `statusTone` (source unique) : seul `running` est vert, `unhealthy` en danger,
   `stopped`/`no_deploy` en neutre — **aucun faux-vert**.
6. **Ce que P5 ne fait PAS (frontière assumée).** Pas de stream WS live (`compose logs -f`) ; pas de métriques
   (CPU/mémoire, `compose stats`) ; pas d'auto-poll de santé. La santé se réconcilie à la demande, les logs se
   tirent à la demande. Ces extensions viendront ancrées sur les routes posées ici, si un besoin les réclame.

## Invariants de test (`tests/test_runtime.py`, `web/…RuntimeTab.test.tsx`)

- **`ps`/`logs` en direct-moteur** : `ps()` → `<engine> ps --format json --filter label=com.docker.compose…` ;
  `logs()` → `<engine> logs --timestamps --tail <n> <cid>` **sans** `--follow`/`-f`, lit stdout ET stderr ;
  l'env passe par l'allowlist P4.
- **Vide honnête** : `engine.logs` sur un `no_deploy` → `{"lines": []}` sans appeler le backend ; tail hors
  borne → clampé à `_LOGS_TAIL_MAX`.
- **Routes** : `GET .../status` reflète l'état live (`running` si un conteneur est up) ; `GET .../logs` rend les
  lignes du service, `[]` si jamais monté, `422` si `tail` hors borne, `404` si projet absent.
- **UI (aucun faux-vert)** : `running` → lien actif (`href` présent) ; `stopped` → lien inerte (aucun `<a>`) ;
  `deploymentTone` ne rend vert QUE `running`.

## Prouvé live (smoke réel, podman-rootless)

Un projet `service-api` déployé (dev) → `GET .../status` rend `running` ; `GET .../logs?tail=100` rend des
**lignes réelles** (le stub P3 journalise au boot) ; le lien `url` ouvre le service (HTTP 200) ; après `down`,
`GET .../status` rend `stopped` (honnête) et le lien devient inerte.
