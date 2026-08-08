# Installer le forgemaster (self-hosted)

Guide turnkey pour héberger **ta propre** instance de forgemaster. Chacun lance la sienne — pas de compte
serveur, pas de secret en clair. Deux chemins d'installation : un **wheel packagé** (le plus simple, aucun
Node requis) ou **depuis les sources** (clone + build du front).

## Prérequis

- **Python ≥ 3.11** (toujours).
- **Node ≥ 18** — **uniquement** pour installer *depuis les sources* (le wheel packagé embarque déjà l'UI).
- Git (le forgemaster orchestre des dépôts).
- **Pour `forgemaster deploy` sur un CT LXC** — le service tourne en `--system` (root), donc `podman compose`
  s'exécute en *rootful-in-container*. Un CT LXC **non-privilégié** doit être créé avec les features Proxmox
  **`nesting=1,keyctl=1`** (`pct create … --features nesting=1,keyctl=1`, ou `pct set <id> --features …`) :
  sans elles, le namespace/overlay imbriqué de podman échoue au `build`/`up`. `provision-ct.sh` installe podman +
  le provider compose + fuse-overlayfs, mais **ne peut pas** poser les features (elles se règlent à la création
  du CT, hors du conteneur). CT privilégié = fallback si le non-privilégié ne passe pas.
- **Device `/dev/net/tun` (device-passthrough, PAS une feature)** — le `build` d'image de `forgemaster deploy`
  (`podman build`, ex. `RUN npm ci`) a besoin du réseau ; sans `/dev/net/tun` dans le CT, **slirp4netns ne peut
  pas créer son tap** et le build meurt (`failed to read from slirp4netns sync pipe: EOF`). TUN n'est pas
  réglable par `--features` : ajoute-le à la conf du CT (`/etc/pve/lxc/<id>.conf`) **avant le 1ᵉʳ boot**, puis
  stop/start :
  ```
  lxc.cgroup2.devices.allow: c 10:200 rwm
  lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
  ```
  Comme les features, c'est un réglage de **création** (hors conteneur) que `provision-ct.sh` ne pose pas ;
  `forgemaster doctor` le sonde (`🔴 device TUN` s'il manque).

## Installer

### A. Depuis un wheel packagé — recommandé (aucun Node)

L'interface web **et l'outil code-map** voyagent **dans le wheel** : `pip install` suffit, rien à builder ni
à installer en plus — l'onglet **Flow** (cartes d'exécution) fonctionne dès le 1ᵉʳ démarrage.

```bash
python3 -m venv ~/.venvs/forgemaster && . ~/.venvs/forgemaster/bin/activate
pip install forgemaster-0.1.0-py3-none-any.whl     # le wheel fourni
forgemaster --version
```

Pour le coffre Bitwarden (optionnel) : `pip install 'forgemaster[bws]'` (voir *Coffre de secrets*).

### B. Depuis les sources (clone) — Node requis

```bash
git clone https://github.com/Avadis7860/forgemaster && cd forgemaster
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # (ajoute [dev] pour l'outillage qualité)
forgemaster setup               # build l'UI (npm) — nécessite Node ; fail-loud sinon
```

`forgemaster setup` construit `web/dist` **et** rend `python -m codemap` disponible (il installe code-map depuis
un clone **sibling** `../code-map` s'il existe — sinon il te le dit ; l'onglet Flow en a besoin). Sans build
front (ou sans Node), le daemon tourne en **API-only** et sert une page d'aide à `/` expliquant quoi faire —
jamais un écran blanc silencieux.

> Pour le chemin sources, clone **code-map à côté du forgemaster** (`git clone …/code-map` en sibling) avant
> `forgemaster setup`. Le wheel packagé (chemin A) n'en a pas besoin : code-map y est déjà inclus.

## Premier démarrage

```bash
forgemaster serve                       # http://127.0.0.1:8700
forgemaster serve --host 0.0.0.0        # exposé au réseau local (LAN)
```

Ouvre l'URL : sur une **instance neuve**, un **wizard `/setup`** te guide —

1. **Coffre de secrets** — prêt par défaut (fichier chiffré local, zéro-config).
2. **Ton premier projet** — crée-le (le miroir GitHub est optionnel).
3. **Miroir + token** — si le projet pousse vers GitHub, lie un token de push (jamais stocké en clair).
4. **Prêt** — ouvre le projet et lance la forge.

Le wizard est **non bloquant** et ré-ouvrable depuis **Réglages**. En ligne de commande : `forgemaster onboard
status` (code de sortie `0` si complet, `1` sinon — utilisable comme sonde) et `forgemaster onboard link`.

## Servir en production (systemd)

Le plus simple — un **service utilisateur** (sans root), qui redémarre tout seul et survit au reboot :

```bash
forgemaster install-service --host 0.0.0.0        # écrit ~/.config/systemd/user/forgemaster.service
loginctl enable-linger "$USER"                    # AVANT d'activer : sinon le service meurt à la déconnexion
systemctl --user daemon-reload && systemctl --user enable --now forgemaster
```

L'ordre n'est pas cosmétique : sans `linger`, le gestionnaire systemd de l'utilisateur s'arrête avec sa
dernière session, et le service avec lui — sur un serveur sans écran, « ça marche tant que je suis connecté
en ssh ». `install-service` imprime désormais les trois gestes dans cet ordre ; cette page les répète.

Ou un **service système** (root) : `forgemaster install-service --system --host 0.0.0.0` puis
`sudo systemctl daemon-reload && sudo systemctl enable --now forgemaster`.

La commande écrit aussi un `forgemaster.env` (EnvironmentFile) sous `FORGEMASTER_HOME` — c'est là que se règlent le
`FORGEMASTER_HOME`, le backend de coffre et le bind (jamais un secret). Gabarit manuel :
[`deploy/forgemaster.service`](../deploy/forgemaster.service).

**Réseau / TLS.** Le forgemaster n'a **pas d'authentification HTTP** (outil mono-utilisateur, frontière de
confiance = LAN/localhost). Ne l'expose pas nu sur Internet : mets-le derrière un reverse-proxy (TLS + auth)
ou un VPN.

**WebSockets (terminal / transcript).** Exposer hors loopback (`--host 0.0.0.0`) n'est sûr que **grâce à la
garde côté serveur** des handshakes WS : contrôle d'**Origin** (same-origin automatique + allowlist) **et**
**token par-instance** (`home/ws_token`, injecté de façon transparente par le front). Sans elle, une page web
tierce dans ton navigateur pourrait détourner un terminal (CSWSH) — le réseau ne filtre pas ce vecteur, et le
**CORS ne couvre pas les WS**. Derrière un reverse-proxy à nom public différent, déclare-le dans
`FORGEMASTER_WS_ALLOWED_ORIGINS`. Détail : spec [`ws-origin-token-boundary`](specs/ws-origin-token-boundary.md).

## Édition maintainer — recette CT reproductible (batteries incluses)

Pour monter un hôte vierge (CT/VM) servant **la dernière version** du forgemaster avec la **boîte à outils du
framework déjà rangée** dans le rail « Outils » — en une commande, reproductiblement. Deux temps, séparés
nettement : **build** (chez le mainteneur, Node présent) puis **provision** (sur l'hôte cible, Python seul).

### 1. Build du wheel depuis HEAD (mainteneur — Node requis)

```bash
deploy/build-wheel.sh        # → dist/forgemaster-<version>-py3-none-any.whl (UI embarquée, vérifiée)
```

Le wheel est buildé **depuis le code courant** (jamais un snapshot en retard) ; la SPA y est empaquetée sous
`forgemaster/_web_dist`. C'est le seul point où Node intervient.

### 2. Provision de l'hôte cible (Python seul, aucun Node)

Copie sur l'hôte cible le wheel + `deploy/{provision-ct.sh,bootstrap.yaml}`, puis, **en tant que l'utilisateur
du service** (jamais root pour un service `--user` : la base écrite par le bootstrap doit lui appartenir) :

```bash
./provision-ct.sh --wheel forgemaster-<version>-py3-none-any.whl \
                  --manifest bootstrap.yaml
```

**Aucun credential n'est requis pour l'outillage**, et depuis le 2026-08-08 ce n'est plus une politique mais
une propriété structurelle : les 3 cartes (`code-map`, `docs-map`, `front-map`) voyagent **dans le wheel**
(`forgemaster/_maps`, bâties au SHA du sibling par `deploy/build-wheel.sh`), et l'étape `[4]` les pose
**hors-ligne** depuis ces fichiers. Il n'y a plus d'URL sur ce chemin, donc plus de clone, donc plus rien à
authentifier — et deux installs de la même édition posent le **même** code, ce qu'une réf `@main` ne
garantissait pas. `--token-file` ne subsiste que pour l'amorçage `[7]`, et seulement si le manifeste déclare
un dépôt encore **privé** — cf. § « Le manifeste ».

Le script (idempotent, fail-loud, imprime chaque étape `[n/9]`) : `[1]` pose les **prérequis de base**
(`python3-venv`, `git`, `curl` — absents d'une image cloud minimale) + crée un venv → `[2]` installe le wheel →
`[3]` *(Claude, opt-in)* → `[4]` **`forgemaster toolchain install`** — l'**outillage hôte-niveau** que les bundles
DÉCLARENT (maps CLI + qualité py + **Node via nodeenv rootless**) rangé sous `$FORGEMASTER_HOME/tools/bin`, puis
**`forgemaster doctor`** qui **aborte fail-loud** si un outil déclaré manque (cf. § ci-dessous) → `[5]` écrit l'unité
systemd → `[6]` dépose le manifeste sous `FORGEMASTER_HOME` → `[7]` **`forgemaster bootstrap`** (adopte les 5 outils via
leur **vrai clone git**) → `[8]` *(serveur MCP co-installé, opt-in — cf. § ci-dessous)* → `[9]` active le
service. Résultat : `http://<hôte>:8700`, rail « Outils » peuplé au 1ᵉʳ chargement. Ré-exécuter la commande
est sûr (venv réutilisé, outils déjà là *skippés*).

### Claude Code dans le terminal web (`--with-claude`)

L'onglet **Terminal** de chaque projet ouvre un login shell dans le dépôt — l'endroit naturel pour lancer
`claude` et travailler. Ajoute `--with-claude` à la recette pour installer le CLI **au provisioning** :

```bash
./provision-ct.sh --wheel forgemaster-<version>-py3-none-any.whl --manifest bootstrap.yaml --with-claude
```

L'étape pose l'**installeur natif officiel** (`claude.ai/install.sh`, binaire autonome — **aucun Node, aucune
clé API**) dans le `~/.local/bin` de **l'utilisateur du service** (le propriétaire du PTY), avec le PATH câblé
pour les login shells. Idempotente (skip si déjà présent), retry x2, vérif dure `claude --version`. La
connexion se fait **au 1ᵉʳ lancement** : ouvre l'onglet Terminal et tape `claude` (login OAuth interactif) —
aucun secret ne transite par la recette. Sans le flag, l'install reste inchangée (le terminal marche, mais
`claude` n'y est pas).

### Le manifeste `deploy/bootstrap.yaml`

De la **donnée versionnée**, jamais un secret : `slug` + `source_url` + `kind: tool` par outil. Les 5 dépôts
du framework (`forgemaster`, `code-map`, `front-map`, `docs-map`, `forgemaster-catalogs`) y sont déclarés. Un `credential_ref`
optionnel par entrée épingle un token dédié ; sinon le token partagé de `--token-file` (un PAT fine-grained
`Contents:Read`) sert de repli ; sinon le clone est **anonyme**. Le token vit dans le coffre — **jamais** dans
ce fichier ni un repo.

**État au 2026-08-03** : `code-map`, `docs-map`, `front-map` sont **publics** → adoptés sans credential.
`forgemaster` et `forgemaster-catalogs` restent **privés** → ce manifeste-ci est l'**édition maintainer**, et son amorçage
complet demande encore un `--token-file`.

### Un amorçage incomplet n'annule pas l'install

Si un dépôt du manifeste est injoignable (privé sans token, renommé, hors ligne), `[7]` le rapporte en 🔴 par
outil, l'install **continue**, et `[8]` active le service : une adoption ratée est une **donnée** manquante,
pas une infrastructure cassée. Le rail « Outils » montre l'outil absent, et `forgemaster bootstrap` est
**idempotent** — relance-le quand l'accès est rétabli, il ne re-clone pas ce qui est déjà adopté.

### Outillage hôte-niveau, preflight & câblage MCP

Deux mécanismes **distincts** peuplent un hôte forgemaster — ne pas les confondre :

- **`forgemaster toolchain install`** (étape `[4/9]`, ci-dessus) — le **toolchain hôte-niveau** que les bundles
  *déclarent* (`allowedTools`) : maps CLI (`codemap`/`docsmap`/`frontmap`) **depuis les wheels de l'édition**
  (`--no-index` : la seule étape hors-ligne, et la première, pour qu'un réseau absent ne fasse pas tomber ce
  qui n'en a pas besoin), qualité py (ruff/mypy/pytest) et **Node via nodeenv rootless** — ces deux-là
  exigent toujours le réseau. Le tout dans un venv d'outils dédié sous `$FORGEMASTER_HOME/tools/` (symlinks en
  `tools/bin`). Idempotent, fail-loud. C'est ce qui rend le contrat d'outillage **réellement présent** sur
  l'hôte. `forgemaster toolchain check` dit ensuite, **sans réseau**, si les cartes servies sont bien celles
  de l'édition installée — la question qui se pose après une mise à jour, puisque `update apply` ne touche
  pas `tools/`.
- **`forgemaster bootstrap`** (étape `[7/9]`) — l'**adoption des 5 dépôts-outils** du framework dans le rail
  « Outils » via leur clone git (donnée du manifeste). Peuple la *surface*, pas le PATH du worker.

**Le runtime HONORE le contrat, pas seulement l'install.** Au **dispatch**, le PATH d'outils (`tools/bin` +
Node) est **injecté explicitement** dans l'env du worker (fini l'héritage passif), et un **preflight fail-loud**
refuse un dispatch dont un binaire déclaré manque — **avant** le spawn, avec un remède nommé (`forgemaster
toolchain install`) — plutôt que laisser le worker mourir à mi-course. La sonde jumelle **hors-ligne** est `forgemaster
doctor` (rc 0/1), rejouée à l'étape `[4/9]` et disponible à tout moment.

### Le corpus MCP : deux topologies, et l'instance dit laquelle elle est

Un forgemaster n'a **pas** d'instance `forgemaster-catalogs` par défaut. Deux façons d'en avoir une, et
`GET /api/version` (clé `mcp`) répond toujours laquelle est en place :

- **`co-installed`** — le serveur tourne **sur cet hôte**, servi en **loopback**. Posé par
  `provision-ct.sh --with-mcp <racine-corpus>` (étape `[8/9]`) ou après coup par
  **`forgemaster mcp install --data-root <racine-corpus>`**. La commande pose un venv dédié
  (`$FORGEMASTER_HOME/mcp/venv`) au **SHA épinglé** de l'édition, génère le secret HS256, écrit un
  `EnvironmentFile` en `600` + une unité systemd, et câble le forgemaster dessus — aucune valeur à saisir.
- **`remote`** — l'instance consomme un serveur d'ailleurs. Câblé au **wizard `/setup`** (1ᵉʳ démarrage) ou
  via **`forgemaster mcp wire --endpoint <url>`** (`--secret-file <f>` si on possède la valeur,
  `--secret-ref <uuid>` en BYO). Pose dans `forgemaster.env` une **référence opaque** au secret + l'endpoint,
  jamais le secret en clair.

**La racine de corpus est TA donnée.** Le co-install pose un **lecteur**, pas un corpus : `--data-root` est
obligatoire et doit exister, et le forgemaster ne clone aucun corpus — ni le sien, ni le tien. Sans racine, la
commande **refuse** plutôt que de démarrer un serveur qui répondrait `200` sur un corpus vide.

Dans les deux cas le prochain dispatch injecte un `.mcp.json` valide ; sans câblage l'injection est un
**no-op honnête** (`topology: "none"` — le forgemaster tourne, la doc tierce n'est juste pas atteignable). Un
`systemctl restart forgemaster` recharge l'`EnvironmentFile`. Détail : `docs/runbooks/provision.md`.

## Coffre de secrets

| Backend | Quand | Config |
|---|---|---|
| `file` (défaut) | poste perso / lightweight | **aucune** — clé chiffrée créée à la 1ʳᵉ écriture sous `FORGEMASTER_HOME/secrets/` |
| `bws` | Bitwarden Secrets Manager | `pip install 'forgemaster[bws]'` + `FORGEMASTER_SECRET_STORE=bws` + `BWS_ACCESS_TOKEN` (ou `BWS_ACCESS_TOKEN_FILE`) |

La base ne stocke **jamais** un token — seulement une **référence** ; la valeur vit dans le coffre. Le choix
du backend se fait à l'installation (env / `forgemaster.env`), pas à chaud.

## Emplacements

- `FORGEMASTER_HOME` (défaut `~/.forgemaster`) — base SQLite, logs, coffre fichier, `forgemaster.env`.
- `FORGEMASTER_PROJECTS_ROOT` (défaut `~/projects`) — racine des dépôts orchestrés.

## Mettre à jour

- Wheel : `pip install --upgrade <nouveau wheel>` puis redémarre le service. *(Si tu ré-installes la **même
  version** — ex. un rebuild local sans bump — `--upgrade` est un no-op ; ajoute `--force-reinstall
  --no-deps` pour forcer le remplacement des fichiers.)*
- Sources : `git pull && pip install -e . && forgemaster setup` puis redémarre.

Le schéma SQLite **migre en place** au démarrage (idempotent) — aucune action manuelle.
