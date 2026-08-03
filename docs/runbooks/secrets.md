# secrets — runbook (coffres de secrets pluggables (fichier chiffré / BWS) + résolveur scopé + JWT HS256)

Le cockpit ne stocke jamais un token en DB : il garde une **référence opaque** (`credential_ref`) et résout la valeur à l'usage (writeback git) via un `SecretStore` pluggable, choisi globalement par instance (`COCKPIT_SECRET_STORE`, défaut `file`). Deux backends : `EncryptedFileStore` (Fernet, zéro-config) et `BwsStore` (Bitwarden Secrets Manager, bring-your-own). La *policy* de résolution vit dans cette couche : un résolveur **total** (absence → `''`) et un résolveur **scopé** qui applique un least-privilege par projet. Séparément, `jwt` forge/vérifie des JWT HS256 en stdlib pure pour le Bearer MCP.

## build_store() — fabrique le store actif d'après la config
`src/cockpit/secrets/__init__.py:28` · appelé par `cred_resolver` / `scoped_cred_resolver` (et tout appelant onboarding).
Lit `settings.secret_store` et importe le backend **paresseusement** : `file` → `EncryptedFileStore(settings.secrets_dir)`, `bws` → `BwsStore()`. Le défaut `file` ne tire jamais le SDK BWS et inversement. Backend inconnu → `SecretStoreError` (attendu `file | bws`).

## cred_resolver() — résolveur `credential_ref → token` partagé
`src/cockpit/secrets/__init__.py:43` · appelé par les couches qui injectent un token dans une op git (writeback merge, adoption/clone, bootstrap).
Retourne une closure `resolve(ref)`. **Lazy** : le store n'est construit (`build_store`) que si une ref est réellement présentée. **Total** : tout `SecretStoreError` (absent/illisible) dégrade en `''`, jamais d'exception — l'auth git reste best-effort côté appelant. La policy vit ICI, jamais dans `git/internal` (qui n'importe pas ce paquet).

## scoped_cred_resolver() — résolveur scopé à UN projet (ACL least-privilege)
`src/cockpit/secrets/__init__.py:57` · appelé par le chemin worker/dispatch d'un projet donné (ACL, P4).
**Invariant clé** : durcit `cred_resolver` en ne résolvant QUE le ref lié à `slug` — il lit `projects.credential_ref` via `get_project(conn, slug)` (import paresseux pour casser le cycle `projects.registry ↔ secrets`) et compare : si `ref` est vide, hors-scope, ou appartient à un autre projet (ou à aucun), il refuse et rend `''` — jamais le token d'autrui. Ferme la pollution *control-plane* : le résolveur d'un projet ne peut pas tirer le secret d'un voisin. **Lazy/Total** comme `cred_resolver` (projet inconnu → `KeyError` capté → `''` ; aucune exception ne fuit).

## SecretStore — le contrat (Protocol runtime-checkable) + erreurs
`src/cockpit/secrets/base.py:36` · implémenté par `EncryptedFileStore` / `BwsStore`, construit via `build_store`.
`Protocol` **stdlib-pur** (aucune dép crypto/SDK ici) : attribut `backend` (`"file"|"bws"`) + `put(value, label=)→ref` / `get(ref)→value` / `delete(ref)` (idempotent) / `has(ref)→bool` / `list_entries()→[{ref,label}]` (métadonnées SANS valeurs) / `health()→(ready, detail)` (racine de confiance joignable, sans secret). Hiérarchie d'erreurs (`base.py:17/21/30`) : `SecretStoreError` (échec générique — un `except` large les attrape toutes) ⊃ `SecretNotFound(ref)` (ref inconnue) et `SecretUnsupported` (op non offerte par le backend, ex. `put` sur BWS).

## EncryptedFileStore — store par défaut, Fernet, zéro-config
`src/cockpit/secrets/file_store.py:28` · construit par `build_store` quand `secret_store == "file"`.
Deux fichiers sous `secrets_dir` : `master.key` (clé Fernet en clair mais **0600**, dossier **0700** — l'unique racine non-chiffrée, née via `O_EXCL`+0600 sans fenêtre lisible) et `store.enc` (blob JSON `{ref:{value,label}}` re-chiffré à chaque écriture, remplacé atomiquement par `os.replace`). Chiffrement authentifié Fernet (AES-CBC + HMAC-SHA256) importé **paresseusement**. `put` génère un `ref` = `uuid4().hex`. Blob altéré / clé erronée (`InvalidToken`) → `SecretStoreError` plutôt que du faux. `health()` toujours prêt (zéro-config).

## BwsStore — adaptateur Bitwarden Secrets Manager (optionnel, bring-your-own)
`src/cockpit/secrets/bws_store.py:31` · construit par `build_store` quand `secret_store == "bws"` (extra `cockpit[bws]`).
Transport = SDK officiel `bitwarden-sdk` (client in-process, auth réutilisée), via un `client_factory` **injectable** (swappable en test). Modèle **bring-your-own** v1 : l'utilisateur crée le secret dans Bitwarden et fournit son UUID comme `credential_ref` ; `get` le résout (cache mémoire par process) — `put`/`delete` lèvent `SecretUnsupported`, `list_entries` rend `[]`. Racine de confiance : `BWS_ACCESS_TOKEN` (env ou fichier-600), résolu localement, jamais loggé (`state_file=None`, auth en mémoire). Endpoints `.com` par défaut, surchargeables (`BWS_API_URL`/`BWS_IDENTITY_URL`, instances EU). `health()` ne fait PAS de login réseau : prêt seulement si le token se résout.

## mint_hs256() — forge un JWT HS256 (stdlib pure)
`src/cockpit/secrets/jwt.py:29` · appelé par `provision.mcp` (Bearer du `.mcp.json` injecté au dispatch).
Assemble `header{alg:HS256,typ:JWT}` + `payload{sub/iss/aud/iat/exp}` (défauts `issuer="vault-mcp"`, `ttl_seconds=3600`), signe en HMAC-SHA256 via `hmac`/`hashlib` (stdlib, pas de `pyjwt`). Rejette un `secret` < 32 caractères par `ValueError` (`_MIN_SECRET_LEN`). Le contrat de claims (`aud`, `iss`) est celui validé par le serveur forgemaster-catalogs.

## verify_hs256() — vérifie un JWT HS256
`src/cockpit/secrets/jwt.py:44` · pendant de `mint_hs256` (port fidèle de `jwt_stdlib` du vault).
Retourne les claims si valide, sinon `None`. Garanties : anti alg-confusion (rejette `alg≠HS256`, donc `none`/asym) ; signature comparée en **constant-time** (`hmac.compare_digest`) ; contrôles `exp` (avec `leeway`), `aud`, `iss` optionnel ; token malformé (≠3 parts ou décodage KO) → `None`.

## Zones non détaillées
- `b64url_encode`/`b64url_decode` (`jwt.py:21/25`) : primitives base64url stdlib (strip/re-pad du `=`), plomberie d'encodage du JWT.
- Helpers privés de `EncryptedFileStore` (`_cipher`, `_load_or_create_key`, `_read_all`/`_write_all`) et de `BwsStore` (`_resolve_token`, `_get_client`, `_default_client_factory`) : mécanique interne (import paresseux, I/O 0600, login SDK) couverte par les leads ci-dessus.
