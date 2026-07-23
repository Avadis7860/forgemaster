# spec — frontière client WebSocket (Origin + token par-instance)

> Contrainte distillée (mission vault `cockpit-ws-origin-token-hardening`, 2026-07-23).
> Cibles : `daemon/wsguard.py` (garde), `daemon/ws_token.py` (secret d'instance), `daemon/routes/`
> (`terminal._accept_project_pty`, `dispatch.dispatch_ws`), `config.Settings.ws_allowed_origins`,
> `web/src/lib/ws.tokenProtocols` + `queries.useWsToken`.

## Problème tranché

Les poignées WebSocket du daemon (`/ws/terminal`, `/ws/interview`, `/ws/dispatch`) n'avaient **aucun
contrôle client** avant `accept()` — la seule garde était l'anti-traversal du workdir. Le docstring assumait
« la frontière client reste la frontière réseau (LAN/VPN) ». **Faux** contre le vecteur navigateur (CSWSH,
Cross-Site WebSocket Hijacking) : une page tierce chargée dans le navigateur de l'opérateur exécute
`new WebSocket("ws://<hôte>:<port>/ws/terminal/<projet>")` ; la connexion **part de la machine de l'opérateur
vers l'intérieur** → routeur/pare-feu ne la filtrent pas ; la **same-origin policy ne s'applique pas** au
handshake WS, et le **CORS non plus** (`app.py` `allow_origins` ne couvre que les `fetch`). Seul un contrôle
**côté serveur** l'arrête.

## Règles verrouillées

1. **Garde AVANT `accept()`, sur TOUTES les poignées WS.** Une garde partagée (`wsguard.authorize_ws`)
   est appelée en tête de `_accept_project_pty` (shell + interview) et `dispatch_ws`, **avant** toute autre
   validation (avant même l'oracle d'existence job/projet). Refus → `close(1008)`, jamais un flux.
2. **Deux barrières composées** (l'une sans l'autre ne suffit pas) :
   - **Origin** (anti-navigateur) : le navigateur envoie toujours un `Origin` **vrai, non-forgeable** depuis
     une page. Autorisé ssi **same-origin** (autorité de l'`Origin` == en-tête `Host` — **zéro-config**, couvre
     l'hôte réel de l'instance, LAN inclus), OU origine de **dev Vite**, OU dans `ws_allowed_origins`
     (reverse-proxy à nom public différent). `Origin` **absent** (client non-navigateur : sonde, void-runner)
     → **toléré** (hors vecteur CSWSH), mais le token reste exigé.
   - **Token par-instance** (anti-client-non-navigateur + defense-in-depth) : exigé au handshake via le
     sous-protocole `Sec-WebSocket-Protocol: cockpit.token.<valeur>` — **hors des access-logs** (choisi contre
     query-param pour ne pas fuiter derrière un reverse-proxy loggant). Comparé en **temps constant**, **echo**
     du sous-protocole retenu à l'`accept` (obligation RFC 6455).
3. **Token = secret d'instance, pas un `credential_ref`.** Minté une fois au 1er boot
   (`secrets.token_urlsafe(32)`), persisté `home/ws_token` **chmod 600**, posé sur `app.state.ws_token`. Le
   coffre `SecretStore` (refs per-projet en DB) est **surdimensionné** pour un token unique d'instance.
4. **Livraison transparente au front same-origin.** `GET /api/ws-token` rend le token ; une page tierce ne
   peut **PAS** lire ce corps (same-origin policy + CORS localhost-only). Le front (`useWsToken`,
   `tokenProtocols`) l'injecte dans le sous-protocole ; il **n'ouvre pas** le WS tant que le token manque
   (sinon `1008`). Aucune friction opérateur, aucun deadlock avec le `claude login` d'onboarding (le shell est
   same-origin → Origin valide + token).
5. **CORS ≠ garde WS.** L'`allow_origins` CORS protège les `fetch`, **jamais** les handshakes WS — l'Origin
   allowlist de `wsguard` est la vraie barrière. Ne pas confondre les deux.
6. **Ne jamais exposer nu sans la garde.** `--host 0.0.0.0` hors loopback n'est sûr que **parce que** la garde
   Origin+token existe ; c'est un **prérequis de distribution** (le daemon tournera sur la machine principale
   de l'utilisateur). Un reverse-proxy à nom public → ajouter l'origine dans `ws_allowed_origins`.

## Invariants de test (encodés dans cockpit)

- `origin_allowed` : `Origin` absent → toléré ; autorité `Origin` == `Host` → autorisé (zéro-config) ;
  cross-origin hors dev/allowlist → refusé ; dev Vite → autorisé ; `null` (iframe sandbox) → refusé.
- `match_token_subprotocol` : bon token → echo du sous-protocole exact ; mauvais/absent → `None` (temps
  constant).
- `ensure_ws_token` : minté une fois (600), idempotent, relit l'existant.
- Route réelle : cross-origin **même avec token valide** → fermé avant `accept` ; sans/mauvais token → fermé ;
  same-origin + token → accepté. `GET /api/ws-token` rend le token d'`app.state`.
- Front : `tokenProtocols(token)` → `['cockpit.token.<v>']` ; `tokenProtocols(undefined)` → `undefined`
  (le consommateur n'ouvre pas le WS).
