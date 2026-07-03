# Installer le cockpit (self-hosted)

Guide turnkey pour héberger **ta propre** instance de cockpit. Chacun lance la sienne — pas de compte
serveur, pas de secret en clair. Deux chemins d'installation : un **wheel packagé** (le plus simple, aucun
Node requis) ou **depuis les sources** (clone + build du front).

## Prérequis

- **Python ≥ 3.11** (toujours).
- **Node ≥ 18** — **uniquement** pour installer *depuis les sources* (le wheel packagé embarque déjà l'UI).
- Git (le cockpit orchestre des dépôts).

## Installer

### A. Depuis un wheel packagé — recommandé (aucun Node)

L'interface web voyage **dans le wheel** : `pip install` suffit, rien à builder.

```bash
python3 -m venv ~/.venvs/cockpit && . ~/.venvs/cockpit/bin/activate
pip install cockpit-0.1.0-py3-none-any.whl     # le wheel fourni
cockpit --version
```

Pour le coffre Bitwarden (optionnel) : `pip install 'cockpit[bws]'` (voir *Coffre de secrets*).

### B. Depuis les sources (clone) — Node requis

```bash
git clone https://github.com/Avadis7860/cockpit && cd cockpit
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # (ajoute [dev] pour l'outillage qualité)
cockpit setup               # build l'UI (npm) — nécessite Node ; fail-loud sinon
```

`cockpit setup` construit `web/dist`. Sans lui (ou sans Node), le daemon tourne en **API-only** et sert une
page d'aide à `/` expliquant quoi faire — jamais un écran blanc silencieux.

## Premier démarrage

```bash
cockpit serve                       # http://127.0.0.1:8700
cockpit serve --host 0.0.0.0        # exposé au réseau local (LAN)
```

Ouvre l'URL : sur une **instance neuve**, un **wizard `/setup`** te guide —

1. **Coffre de secrets** — prêt par défaut (fichier chiffré local, zéro-config).
2. **Ton premier projet** — crée-le (le miroir GitHub est optionnel).
3. **Miroir + token** — si le projet pousse vers GitHub, lie un token de push (jamais stocké en clair).
4. **Prêt** — ouvre le projet et lance la forge.

Le wizard est **non bloquant** et ré-ouvrable depuis **Réglages**. En ligne de commande : `cockpit onboard
status` (code de sortie `0` si complet, `1` sinon — utilisable comme sonde) et `cockpit onboard link`.

## Servir en production (systemd)

Le plus simple — un **service utilisateur** (sans root), qui redémarre tout seul et survit au reboot :

```bash
cockpit install-service --host 0.0.0.0        # écrit ~/.config/systemd/user/cockpit.service
systemctl --user daemon-reload && systemctl --user enable --now cockpit
loginctl enable-linger "$USER"                 # démarrer sans session ouverte (serveur headless)
```

Ou un **service système** (root) : `cockpit install-service --system --host 0.0.0.0` puis
`sudo systemctl daemon-reload && sudo systemctl enable --now cockpit`.

La commande écrit aussi un `cockpit.env` (EnvironmentFile) sous `COCKPIT_HOME` — c'est là que se règlent le
`COCKPIT_HOME`, le backend de coffre et le bind (jamais un secret). Gabarit manuel :
[`deploy/cockpit.service`](../deploy/cockpit.service).

**Réseau / TLS.** Le cockpit n'a **pas d'authentification** (outil mono-utilisateur, frontière de confiance =
LAN/localhost). Ne l'expose pas nu sur Internet : mets-le derrière un reverse-proxy (TLS + auth) ou un VPN.

## Édition maintainer — recette CT reproductible (batteries incluses)

Pour monter un hôte vierge (CT/VM) servant **la dernière version** du cockpit avec la **boîte à outils du
framework déjà rangée** dans le rail « Outils » — en une commande, reproductiblement. Deux temps, séparés
nettement : **build** (chez le mainteneur, Node présent) puis **provision** (sur l'hôte cible, Python seul).

### 1. Build du wheel depuis HEAD (mainteneur — Node requis)

```bash
deploy/build-wheel.sh        # → dist/cockpit-<version>-py3-none-any.whl (UI embarquée, vérifiée)
```

Le wheel est buildé **depuis le code courant** (jamais un snapshot en retard) ; la SPA y est empaquetée sous
`cockpit/_web_dist`. C'est le seul point où Node intervient.

### 2. Provision de l'hôte cible (Python seul, aucun Node)

Copie sur l'hôte cible le wheel + `deploy/{provision-ct.sh,bootstrap.yaml}` (+ un token-file si les dépôts
des outils sont privés), puis, **en tant que l'utilisateur du service** (jamais root pour un service `--user` :
la base écrite par le bootstrap doit lui appartenir) :

```bash
./provision-ct.sh --wheel cockpit-<version>-py3-none-any.whl \
                  --manifest bootstrap.yaml \
                  --token-file read-token.txt      # omets-le quand les dépôts sont publics
```

Le script (idempotent, fail-loud, imprime chaque étape) : crée un venv → installe le wheel → écrit l'unité
systemd → dépose le manifeste sous `COCKPIT_HOME` → **`cockpit bootstrap`** (adopte les 5 outils via leur
**vrai clone git**) → active le service. Résultat : `http://<hôte>:8700`, rail « Outils » peuplé au 1ᵉʳ
chargement. Ré-exécuter la commande est sûr (venv réutilisé, outils déjà là *skippés*).

### Le manifeste `deploy/bootstrap.yaml`

De la **donnée versionnée**, jamais un secret : `slug` + `source_url` + `kind: tool` par outil. Les 5 dépôts
du framework (`cockpit`, `code-map`, `front-map`, `docs-map`, `mcp-catalogs`) y sont déclarés. Un `credential_ref`
optionnel par entrée épingle un token dédié ; sinon le token partagé de `--token-file` (un PAT fine-grained
`Contents:Read` sur les 5 dépôts) sert à tous. Le token vit dans le coffre — **jamais** dans ce fichier ni un repo.

### Publier les outils plus tard (zéro changement)

Les dépôts sont privés aujourd'hui. Le jour où tu les publies (repos publics), le clone devient **anonyme** :
retire simplement `--token-file` de la commande. Aucun changement du manifeste ni du code — l'auth au clone est
optionnelle par conception.

## Coffre de secrets

| Backend | Quand | Config |
|---|---|---|
| `file` (défaut) | poste perso / lightweight | **aucune** — clé chiffrée créée à la 1ʳᵉ écriture sous `COCKPIT_HOME/secrets/` |
| `bws` | Bitwarden Secrets Manager | `pip install 'cockpit[bws]'` + `COCKPIT_SECRET_STORE=bws` + `BWS_ACCESS_TOKEN` (ou `BWS_ACCESS_TOKEN_FILE`) |

La base ne stocke **jamais** un token — seulement une **référence** ; la valeur vit dans le coffre. Le choix
du backend se fait à l'installation (env / `cockpit.env`), pas à chaud.

## Emplacements

- `COCKPIT_HOME` (défaut `~/.cockpit`) — base SQLite, logs, coffre fichier, `cockpit.env`.
- `COCKPIT_PROJECTS_ROOT` (défaut `~/projects`) — racine des dépôts orchestrés.

## Mettre à jour

- Wheel : `pip install --upgrade <nouveau wheel>` puis redémarre le service.
- Sources : `git pull && pip install -e . && cockpit setup` puis redémarre.

Le schéma SQLite **migre en place** au démarrage (idempotent) — aucune action manuelle.
