# Spec — crash-test void-runner (câblage MCP réel, bout-en-bout)

> **Statut** : livré (P6, épic bundle-system). Preuve live enregistrée ci-dessous.
> **Portée** : câblage du MCP de corpus dans un worker dispatché + preuve e2e rejouable du critère binaire.

## Le contrat

Le crash-test prouve **le critère binaire de l'épic bundle-system** : un projet typé se crée **de zéro** et un
**worker réel** y tourne **sans crash outils**, câblé à son outillage — MCP de corpus compris. C'est le rejeu
du crash d'origine (« un worker sur void-runner a crashé, il ne connaissait pas ses outils »).

Invariants verrouillés :

1. **Création de zéro** — `cockpit project create void-runner --type browser-game` sème le SoT (bundle
   `base ⊕ browser-game`, `.cockpit/provenance.toml` stampée `browser-game@<v>`), sans toucher Python ni DB.
2. **Dispatch réel, sans crash** — `cockpit dispatch void-runner/<feature>` spawn un **vrai `claude -p`**
   (jamais un fake) qui termine `ok` (rc 0, `is_error` faux). Le fix historique (`--permission-mode
   acceptEdits` + allowlist) tient.
3. **MCP câblé (post-création, jamais baké)** — un `.mcp.json` (serveur `mcp-catalogs` http + **Bearer JWT
   minté à la demande**, scopé `sub=cockpit:<slug>`) est **injecté dans le worktree** au dispatch et chargé
   par `claude -p` via `--mcp-config`. Le secret HMAC partagé est résolu par le coffre à l'usage ; absent →
   **no-op honnête** (le worker tourne sans MCP, aucun crash — install public sans le corpus privé).
4. **Sécurité (load-bearing)** — le `.mcp.json` porte un Bearer → il est **gitignoré** (bundle de base). Le
   `git add -A` de la forge ne peut donc **jamais** l'embarquer : le JWT n'apparaît dans **aucun commit**.
5. **Commit propre** — la forge committe le travail du worker sur la branche `feature/<slug>` ; arbre propre →
   no-op propre. Post-dispatch, le worktree est **propre** (rien de non-committé, le `.mcp.json` invisible).

## Nommage (verbatim assumé)

Le serveur, l'`aud` et l'`iss` du JWT reproduisent **verbatim le contrat validé par le serveur mcp-catalogs**,
hérité de son hébergement précédent. Ce n'est **pas** un bug : c'est ce qui authentifie
aujourd'hui. Le retrait du verbatim (`vault-catalogs → mcp-catalogs`) est un renommage **coordonné**
(serveur-d'abord), suivi hors de cet épic — backlog vault `mcp-catalogs-naming-coherence`. Surtout pas une
demi-migration côté client.

## Vérification

- **Déterministe (gate natif)** : `tests/test_mcp_wiring.py` — mint JWT stdlib, rendu du `.mcp.json`, injection
  (chmod 600, Bearer scopé), **dégradation honnête** (no-op sans secret), **invariant de sécurité** (`git add
  -A` ne stage pas `.mcp.json` sur un worktree réel), câblage `--mcp-config` + chemin de dispatch réel (coffre
  fichier + runner injecté).
- **Live (hors gate natif, rejouable)** : `scripts/e2e_crash_test_voidrunner.py` — vrai `claude -p`, lent, non
  déterministe (comme `runtime-e2e-verification`, règle 6). `COCKPIT_HOME` jetable, teardown garanti. Exige le
  coffre résolvant le secret MCP + `claude` authentifié (prérequis env documentés dans l'entête du script).

## Preuve live enregistrée

- **2026-07-13** — `scripts/e2e_crash_test_voidrunner.py` → **✅ 14 asserts verts** (coffre BWS, secret MCP
  résolu, MCP joignable). void-runner créé de zéro, worker `claude -p` réel terminé `ok`, `.mcp.json` injecté +
  gitignoré, worktree propre, JWT hors de tout commit ; le worker a committé `docs/CRASH_TEST_OK.md`
  (`feat(crash-check): note (worker dispatch)`) — preuve qu'il connaissait et utilisait ses outils. Landé en P6
  (`cockpit-bundle-crash-test-voidrunner`), clôt l'épic bundle-system.
