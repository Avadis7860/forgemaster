# spec — Runtime : vérification e2e (P6, clôture de l'épic)

> Cible : **prouver** le critère binaire de l'épic runtime-hosting en RÉEL, pas seulement le plomber. Un
> harnais d'acceptance **rejouable** (`scripts/e2e_runtime.py`) pilote la vraie surface HTTP du daemon +
> podman/docker-rootless — aucun seed DB, aucun FakeBackend. P6 n'ajoute pas de feature : il livre l'artefact
> de preuve + cette spec, et **clôt** `ROADMAP-cockpit-runtime-hosting`.

## Problème tranché

P1-P5 ont livré et testé (unitaire + smokes ciblés) chaque brique — modèle de déploiement, backend compose,
seed de config, anti-pollution, observabilité. Manquait la preuve **bout-en-bout** que le tout tient ensemble :
deux projets qui tournent **simultanément**, chacun visible (santé + logs), **sans pollution croisée**, liens
qui ouvrent les services vivants. Un smoke par phase ne le montre pas ; il faut un run **unique** qui enchaîne
déploiement réel → co-résidence → isolation → rendu UI, ancré au SHA.

## Règles verrouillées

1. **Preuve RÉELLE, jamais un seed.** Le harnais crée de vrais projets (`project_type=service-api` → compose +
   Dockerfile + stub semés), déploie via `POST .../up` (build + `compose up -d` réel), lit l'état via `GET
   .../status` (reconcile **live**, `ps` réel) et `GET .../logs`. Aucune écriture DB directe, aucun backend
   simulé — c'est la surface que l'UI appelle. Distinct de `ui_shot.py:_seed_deploy_state` (itération visuelle,
   état DB figé) : ici l'état `running` provient d'un conteneur qui tourne.
2. **Les 4 étages couvrent le critère binaire.** (1) un projet se déploie sur `main` **ET** `dev` → 2
   conteneurs, 2 ports distincts, status live `running`, logs réels, `url` → 200 ; (2) un 2ᵉ projet tourne en
   même temps → **4 compose-projects** (`cockpit-<slug>-<branch>`), **4 ports disjoints**, aucun conflit, les 4
   répondent 200 ; (3) **non-pollution** ; (4) **feature-verified**.
3. **Non-pollution rejouée e2e (rejeu de P4, au niveau conteneur).** Le fichier privé de B est commité sur SON
   `dev` **avant son 1er build** → présent dans SON conteneur, **absent** de celui de A (`<engine> exec <A> ls
   /app`). Le **secret sentinelle** posé dans l'env du daemon est **absent** de l'env du conteneur de A
   (`<engine> exec <A> env` → allowlist P4). L'ACL control-plane est vérifiée in-process : le résolveur de
   secrets de A **refuse** le `credential_ref` de B (`scoped_cred_resolver`). Le fichier est semé **avant le
   1er deploy** (pas par un re-`up`) : podman-compose 1.0.6 **ne recrée pas** un conteneur au re-`up` sur simple
   changement de contexte — on prouve l'**isolation**, pas le rebuild-on-change (hors périmètre de l'épic).
4. **Feature-verified ancré au SHA de HEAD (cockpit).** Le runner Playwright **goto-only** (`render_check.js`)
   charge `/{project}/runtime` et asserte les **marqueurs FR** rendus dans le DOM (`Déploiements`, `en marche`,
   `ouvrir main ↗`, `ouvrir dev ↗`) + screenshot non vide ; le verdict porte `reviewed_sha == HEAD` (frais ssi
   le HEAD n'a pas bougé). **Limite honnête assumée** : le runner ne clique pas → la santé **live** derrière le
   RefreshButton (`GET .../status`) n'est pas déclenchée par ce screenshot ; il capture l'état **DB persisté**,
   qui provient d'un **VRAI deploy** (donc genuine, pas un faux-vert). Le reconcile live est couvert **à part**
   par l'étage 1 (l'API `status` renvoie `running` depuis un `ps` réel). Les deux moitiés se couvrent.
5. **Rejouable, teardown garanti.** `COCKPIT_HOME` jetable ; en `finally`, `down` des déploiements + arrêt du
   daemon + rm du home → **aucun conteneur ni home résiduel**. Idempotent : un 2ᵉ run repart d'un état propre.
6. **Hors gate natif.** Le harnais exige podman/docker + est lent (builds) → il n'entre pas dans le Tier-0
   déterministe (comme les smokes P2-P5). Le gate natif reste vert et inchangé (pytest 348). On le joue **à la
   main** : `.venv/bin/python scripts/e2e_runtime.py` (avec `node`/nvm 22 au PATH pour l'étage 4).

## Prouvé live (podman-rootless, 2026-07-13, cockpit `19f0be2`)

`scripts/e2e_runtime.py` → **25 asserts verts** : demo-a déployé sur main (:5250) + dev (:5251), status live
`running`, logs réels, 200/200 ; demo-b co-résident (:5252/:5253), **4 ports disjoints**, 4×200 ; le conteneur
de A sans le fichier privé ni le secret de B, ACL refusée ; onglet Runtime rendu (marqueurs FR + screenshot),
verdict ancré au HEAD `19f0be2`. Teardown propre (0 conteneur, 0 home). **Critère binaire de l'épic : atteint.**
