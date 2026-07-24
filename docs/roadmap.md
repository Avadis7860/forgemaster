# Roadmap — cockpit (produit)

> Roadmap **produit durable** du cockpit : la vision, ce qui est **livré** en V1, les **décisions de
> conception verrouillées**, et les horizons ouverts. Miroir in-repo de l'épic de productisation
> (`cockpit-productization`) — vit avec le code, pas dans un chat.
>
> Voir aussi : [`architecture.md`](architecture.md) (les couches), [`install.md`](install.md)
> (self-hosted), [`specs/`](specs/) (les décisions distillées en contraintes de design).

## Étoile polaire

Faire du cockpit un **outil distribuable, self-hosted, professionnel** : chaque projet créé naît
**auto-travaillable** (3 index déterministes isolés — code / docs / front — + un `claude` dispatchable),
le cockpit distingue **projets** et **outils**, donne de la **visibilité git**, et **onboarde ses tokens**
au 1er démarrage (dont un token par repo) — **sans jamais** de secret en clair ni de pollution croisée
entre repos.

Modèle produit assumé : **cœur léger + extensions**. Tout ce qui est natif cockpit et auto-intégré à
chaque projet (code-map, docs-map, front-map, mcp-catalogs) est un **outil** (`kind=tool`) ; le cockpit
lui-même reste le **cœur**.

## Livré — V1 (productisation)

Six chantiers, gate vert, feature-verified, visibles sur les instances de vue LAN.

| # | Chantier | État | Preuve |
|---|---|---|---|
| **P0** | Durcissement : terminal net (un seul libellé, pane rempli) + `.gitignore` semé par le seam (`.docsmap/`/`.codemap/`/`.frontmap/`/`.venv/`/`dist/`…) | ✅ | vérifié visuel + `git ls-tree` |
| **P1** | Complétude env : `claude` installé au provisioning (login interactif = per-env, manuel) + seam généralisé aux **3 index** (docs/code/front), chacun scopé à sa racine + gitignoré | ✅ | `claude --version` sur le CT |
| **P2** | Visibilité git : route read-only `GET /api/projects/{p}/git` (branches · ahead/behind main↔dev · log par réf) + primitives bare-safe + onglet **Git** (bannière « main rattrape dev ») | ✅ | feature-verified visuel |
| **P3** | Modèle d'entité `kind`+`owner` (migration DB idempotente) + `--kind` CLI/API + rail 2 sections (Projets/Outils) | ✅ | feature-verified visuel |
| **P4** | Onboarding self-hosted : **store de secrets pluggable** + `credential_ref` par entité (**0 plaintext DB**) + check config-requise au 1er démarrage + **bandeau/panneau Réglages** + **token par repo** | ✅ | smoke e2e sur CT neuf + boucle visuelle |
| **P5** | Distribution turnkey : wheel Node-less (dist embarquée), `cockpit setup` build le front from-clone, dist absente → **page fail-loud actionnable** (jamais un 404 muet), garde-fou comportemental « le wheel SERT la SPA à `/` » | ✅ | `deploy/acceptance-fresh-venv.sh` `[4/4]` |

### Détail P4 — le store de secrets (self-hosted)

- **Module interne** `src/cockpit/secrets/` derrière un Protocol `SecretStore` (`put→ref` / `get(ref)` /
  `delete` / `has` / `list_entries` / `health`) — pas un repo séparé (un seul consommateur, tissé dans le
  seam writeback git).
- **Défaut = `EncryptedFileStore`** : chiffrement authentifié au repos (Fernet, `cryptography` cœur),
  clé-600 sous `home/secrets/`. **Adaptateur `BwsStore`** (SDK Bitwarden, secrets par **UUID** =
  `credential_ref`, extra `cockpit[bws]`). Keyring OS différé (fragile headless). Store choisi
  **globalement par instance** via `COCKPIT_SECRET_STORE` (défaut `file`) ; `credential_ref` **opaque**.
- **git n'importe JAMAIS `cockpit.secrets`** : le writeback reçoit un `credential_ref`, résolu **lazy+total**
  par l'appelant ; creds+identité injectés le temps du push (`GIT_CONFIG_*`, `x-access-token`), jamais
  persistés (spec [`merge-writeback-injected-creds-identity`](specs/merge-writeback-injected-creds-identity.md)).
- **Racine de confiance assumée & documentée** : clé-600 (file) ou `BWS_ACCESS_TOKEN` (bws) = l'**unique**
  secret « en clair » sur l'hôte. Gain = N tokens éparpillés → 1 racine verrouillée (un dump DB/backup
  n'expose plus rien). Surface réduite, pas magique.
- Surface : CLI `cockpit onboard status|link|unlink` + routes `GET /api/onboarding`,
  `POST/DELETE /api/projects/{p}/credential` + UI (bandeau non bloquant + panneau **Réglages** + carte Git
  du projet + formulaire miroir GitHub-backed depuis l'UI).

## Décisions de conception verrouillées

Ne pas re-débattre (distillées en [`specs/`](specs/) quand elles portent des invariants de test) :

- **Claude = par-environnement, pas par-projet** — un seul binaire par CT/env sert tous les projets ;
  l'utilisateur lie son compte lui-même (login interactif). Aucun secret claude injecté par le cockpit.
- **Projet vs Outil = classification, pas entité séparée** — une seule table + discriminateur `kind`
  (`project|tool`, enum extensible) + `owner`. Deux tables = rejeté (join/dup prématurés).
- **Isolation des index = par-racine** — chaque index (`<repo>/.docsmap/` …) est scopé à ce repo,
  gitignoré, jamais committé/propagé. Le `.gitignore` semé est la barrière technique.
- **Self-hosted par utilisateur** — chacun lance SA propre instance ; pas de comptes/auth serveur
  (archi pensée pour ne pas fermer la porte au multi-tenant plus tard).
- **Ne jamais committer `web/dist`** — la dist voyage dans le wheel (build mainteneur, Node) ; la
  provision cible est Python-seul (spec [`web-cockpit-spa`](specs/web-cockpit-spa.md)). `cockpit serve`
  n'auto-build **jamais** : build (mainteneur) et provision (cible) restent séparés.
- **Maison du capital visuel = vendored/cockpit, MCP différé** — les templates UI de référence vivent
  côté cockpit (`web/dist/templates/`), pas dans un index MCP `ui-kit`. Données terrain : N=1 template,
  0 application → bâtir un genre MCP maintenant serait un forward-feature. On livre le **mécanisme
  d'application** (`inspire`, opérateur → worker customise) ; le MCP gradue sur réutilisation cross-projet
  **prouvée** (spec [`template-ui-application-lifecycle`](specs/template-ui-application-lifecycle.md)).
- **Frontière client WS = Origin + token serveur, PAS le réseau** — les handshakes WebSocket sont gardés
  **côté serveur** (contrôle d'Origin same-origin/allowlist + token par-instance), **avant** `accept()`. Le
  réseau (LAN/VPN) ne filtre pas le vecteur navigateur (CSWSH) ; le CORS ne couvre pas les WS. C'est un
  **prérequis de distribution** — `--host 0.0.0.0` n'est sûr que grâce à cette garde (spec
  [`ws-origin-token-boundary`](specs/ws-origin-token-boundary.md)).
- **Générique neutre ⟂ style servi (pas un bundle hand-codé)** — un **type** bundle est **générique** (neutre,
  réutilisable pour toute la classe, ex. `browser-game`) ; le **spécialisé** n'est **pas** un type hand-codé
  mais un **STYLE distillé en capital servi** (blueprint + templates, `mcp-catalogs-data`, servi par le MCP).
  L'interview first-session du générique **énumère les styles servis** (`list_collections` filtré
  `browser-game:`) et en tire la guidance ; le **worker construit** le jeu depuis le capital — c'est ça, le
  crash-test void-runner. *(L'ex-bundle-type `ogame-rogue-like-pve` hand-codé a été **défait** le 2026-07-24 :
  coder le jeu à la place du worker vidait le test et gaspillait le capital — spec
  [`ogame-rogue-like-pve-bundle`](specs/ogame-rogue-like-pve-bundle.md), superseded.)*

## Horizons (non planifiés — ouverts)

- **Tag des 5 repos framework en `tool`** — le mécanisme (`--kind tool`) est livré ; le tag effectif se
  fait à l'adoption de ces repos comme entités. Un `kind=core` distinct du cockpit reste à trancher.
- **Keyring OS** comme 3ᵉ adaptateur de store (différé — fragile en headless).
- **BWS self-hosted** (appliance Proxmox enterprise) — différé (licence entreprise requise) ; tout par
  UUID sur le cloud BWS en attendant.
- **Multi-tenant / auth serveur** — la porte est laissée ouverte par `owner` + le modèle self-hosted,
  non implémentée.
- **Extensions payantes** — matérialisation du modèle « cœur léger + extensions ».
