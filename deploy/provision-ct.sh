#!/usr/bin/env bash
# provision-ct.sh — provisionne un hôte VIERGE en forgemaster prêt-à-l'emploi, batteries incluses, EN UNE
# COMMANDE : venv → install du wheel → [Claude Code, opt-in] → service systemd → OUTILLAGE hôte-niveau
# (`forgemaster toolchain install` : maps + Node + qualité py, ce que les bundles DÉCLARENT) → dépôt du manifeste
# → `forgemaster bootstrap` → [serveur MCP co-installé, opt-in] → activation. AUCUN Node requis au build (l'UI
# voyage dans le wheel ; Node runtime est provisionné par l'étape outillage pour les projets front).
#
# À lancer SUR l'hôte cible, en tant que l'utilisateur qui fera tourner le service (JAMAIS root pour un
# service `--user` : la DB écrite par le bootstrap doit appartenir à l'utilisateur du service). Le wheel,
# le manifeste et l'éventuel token-file y ont été copiés au préalable (voir build-wheel.sh + docs/install.md).
#
# Propriétés (discipline no-footgun, cf. service.py) : IMPRIME chaque étape (rien en douce) ; IDEMPOTENT
# (ré-exécution sûre — venv réutilisé, bootstrap skip les outils déjà là, install-service n'écrase pas
# forgemaster.env) ; FAIL-LOUD (`set -euo pipefail`) ; AUCUN secret en argv (le token passe par --token-file).
#
# `--token-file` ne sert PLUS aux 3 cartes (publiques depuis 2026-08-03 → clone anonyme à l'étape [4/9]) :
# il sert à l'AMORÇAGE [7/9] pour les dépôts du manifeste encore privés, et au co-install [8/9] tant que
# `forgemaster-catalogs` l'est aussi. Manifeste entièrement public → provisionnement sans credential.
#
# `--with-mcp <racine-corpus>` co-installe le serveur MCP de corpus SUR CET HÔTE (topologie `co-installed`,
# servie en loopback) au lieu de dépendre d'un endpoint distant. La racine est TA donnée : ce script n'en
# clone aucune — le corpus ne se distribue pas avec le produit.
#
# Usage :
#   deploy/provision-ct.sh --wheel <chemin.whl> [--manifest deploy/bootstrap.yaml] [--token-file <token>]
#     [--venv ~/.venvs/forgemaster] [--home ~/.forgemaster] [--projects-root ~/projects]
#     [--host 0.0.0.0] [--port 8700] [--system] [--no-enable] [--with-claude]
#     [--with-mcp <racine-corpus>] [--mcp-port 8080]
set -euo pipefail

wheel=""; manifest=""; token_file=""
venv="$HOME/.venvs/forgemaster"
home="${FORGEMASTER_HOME:-$HOME/.forgemaster}"
projects_root="${FORGEMASTER_PROJECTS_ROOT:-$HOME/projects}"
host="0.0.0.0"; port="8700"
scope="user"; enable="yes"; with_claude="no"
mcp_data=""; mcp_port="8080"

while [ $# -gt 0 ]; do
  case "$1" in
    --wheel)         wheel="$2"; shift 2;;
    --manifest)      manifest="$2"; shift 2;;
    --token-file)    token_file="$2"; shift 2;;
    --venv)          venv="$2"; shift 2;;
    --home)          home="$2"; shift 2;;
    --projects-root) projects_root="$2"; shift 2;;
    --host)          host="$2"; shift 2;;
    --port)          port="$2"; shift 2;;
    --system)        scope="system"; shift;;
    --no-enable)     enable="no"; shift;;
    --with-claude)   with_claude="yes"; shift;;
    --with-mcp)      mcp_data="$2"; shift 2;;
    --mcp-port)      mcp_port="$2"; shift 2;;
    -h|--help)       awk 'NR>1 { if (/^#/) print; else exit }' "$0"; exit 0;;
    *) echo "argument inconnu : $1" >&2; exit 2;;
  esac
done

[ -n "$wheel" ] && [ -f "$wheel" ] || { echo "✗ --wheel <chemin.whl> requis (fichier existant)" >&2; exit 2; }
[ -z "$manifest" ] || [ -f "$manifest" ] || { echo "✗ manifeste introuvable : $manifest" >&2; exit 2; }
[ -z "$token_file" ] || [ -f "$token_file" ] || { echo "✗ token-file introuvable : $token_file" >&2; exit 2; }
# Validé ICI, avant la moindre écriture : découvrir une racine de corpus absente à l'étape [8/9] ferait
# échouer un provisionnement de 10 minutes sur un argument qu'on pouvait refuser en 10 millisecondes.
[ -z "$mcp_data" ] || [ -d "$mcp_data" ] || { echo "✗ --with-mcp : racine de corpus introuvable : $mcp_data" >&2; exit 2; }

export FORGEMASTER_HOME="$home" FORGEMASTER_PROJECTS_ROOT="$projects_root"
forgemaster="$venv/bin/forgemaster"

# systemctl : portée user (défaut, sans root) ou system (root). En user, le linger fait survivre le service
# à l'absence de session (hôte headless).
if [ "$scope" = "system" ]; then sysctl="sudo systemctl"; svc_flag="--system"; else sysctl="systemctl --user"; svc_flag=""; fi

# Claude Code (binaire abonnement — AUCUNE clé API ; login OAuth interactif au 1er `claude`). Installé pour
# l'utilisateur du service (= propriétaire du PTY du terminal web) : le PTY lance `bash -l`, qui lit
# ~/.profile/~/.bashrc → d'où le guard PATH sur ~/.local/bin. download-PUIS-exec (jamais `curl | bash` :
# l'installeur hérite du pipe comme stdin et échoue EN SILENCE en non-interactif) + retry x2 + vérif dure.
# Idempotent (skip si déjà présent). Méthode reprise du bake template LXC durci du vault.
#
# CLAUDE_INSTALL_ALLOW_SUDO : depuis 2026 l'installeur ABORTE sous root/sudo (« do not run this installer
# with sudo ») et documente cette variable comme l'échappatoire pour installer DÉLIBÉRÉMENT pour root. En
# portée `--system`, c'est exactement notre cas : le service tourne en root, et root possède le PTY du
# terminal web — un `claude` dans le HOME d'un autre utilisateur y serait introuvable. On la pose donc
# toujours (no-op en portée `--user`, où l'installeur ne la lit même pas).
# Découvert le 2026-08-02 sur une install VRAIMENT fraîche : le template golden d'alors avait Claude BAKÉ
# depuis juillet et ne rejouait jamais l'installeur, donc la rupture amont restait invisible côté produit.
install_claude() {
  command -v curl >/dev/null 2>&1 || { echo "✗ curl requis pour --with-claude (installe-le puis relance)" >&2; return 1; }
  local rc attempt ok=""
  for rc in "$HOME/.profile" "$HOME/.bashrc"; do
    grep -qs '.local/bin' "$rc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
  done
  export PATH="$HOME/.local/bin:$PATH"
  if command -v claude >/dev/null 2>&1; then
    echo "   déjà présent : $(claude --version 2>/dev/null || echo claude)"; return 0
  fi
  for attempt in 1 2; do
    if curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh \
       && CLAUDE_INSTALL_ALLOW_SUDO=1 bash /tmp/claude-install.sh; then ok=1; break; fi
    echo "   (retry $attempt) install Claude échouée — nouvelle tentative…"; sleep 3
  done
  rm -f /tmp/claude-install.sh
  { [ -n "$ok" ] && command -v claude >/dev/null 2>&1; } || { echo "✗ Claude Code absent après install" >&2; return 1; }
  echo "   ✓ Claude OK : $(claude --version 2>/dev/null || echo installé) — tape \`claude\` dans le terminal pour te connecter"
}

# Podman = le moteur de run conteneur pour `forgemaster deploy` (P2). Le wheel n'apporte QUE `forgemaster`, jamais le
# runtime → sans podman, `forgemaster deploy` échouerait (le `doctor` ci-dessous le rougit comme bloquant, cf.
# provisioning-loop-gaps). Idempotent (skip si présent), fail-loud, imprime. apt exige root → `sudo` sinon.
install_podman() {
  if command -v podman >/dev/null 2>&1; then
    echo "   déjà présent : $(podman --version 2>/dev/null || echo podman)"; return 0
  fi
  local APT=apt-get
  [ "$(id -u)" -eq 0 ] || APT="sudo apt-get"
  echo "   installe podman (rootless : + uidmap/slirp4netns/fuse-overlayfs) via $APT…"
  $APT update -qq && $APT install -y -qq podman uidmap slirp4netns fuse-overlayfs \
    || { echo "✗ install podman échouée (apt indisponible ?) — installe podman à la main puis relance" >&2; return 1; }
  command -v podman >/dev/null 2>&1 || { echo "✗ podman absent après install" >&2; return 1; }
  echo "   ✓ podman OK : $(podman --version 2>/dev/null || echo installé)"
}

# Moteur compose : `forgemaster deploy` lance `podman-compose` (défaut config). Debian 12 ne package QUE podman
# 4.3.1, DÉPOURVU de la sous-commande `podman compose` (≥ 4.4) → on installe le binaire STANDALONE
# `podman-compose` (celui contre lequel le backend est écrit, cf. runtime/backend.py ; le backend interroge le
# moteur `podman` DIRECTEMENT pour ps/logs). Idempotent (skip si présent), fail-loud. apt root → `sudo` sinon.
install_compose_provider() {
  if command -v podman-compose >/dev/null 2>&1; then
    echo "   déjà présent : $(podman-compose --version 2>&1 | tail -1 || echo podman-compose)"; return 0
  fi
  local APT=apt-get
  [ "$(id -u)" -eq 0 ] || APT="sudo apt-get"
  echo "   installe podman-compose (moteur compose standalone) via $APT…"
  $APT update -qq && $APT install -y -qq podman-compose \
    || { echo "✗ install podman-compose échouée — installe-le à la main ou configure FORGEMASTER_COMPOSE_CMD=docker compose" >&2; return 1; }
  command -v podman-compose >/dev/null 2>&1 \
    || { echo "✗ podman-compose absent après install" >&2; return 1; }
  echo "   ✓ podman-compose OK"
}

# Runner de vérification visuelle (gate Tier-1.5 `forgemaster gate verify`) : Playwright headless. `verify.py` le
# résout au défaut `$FORGEMASTER_HOME/runners/render_check.js` (aucun env à câbler). On sème le .js vendoré
# (deploy/runners/) + ses deps `playwright-core` (pinné) et `pngjs` (plancher canvas, package.json) + le browser Chromium (peuplé
# sous `~/.cache/ms-playwright` de l'utilisateur du service ; sous --system root = /root = le HOME du service).
# Node vient de `forgemaster toolchain install` (tools/bin). Idempotent (install browser skip si déjà au bon SHA).
seed_verify_runner() {
  local rdir src pw
  # Le runner est EMBARQUÉ dans le wheel (`forgemaster/_verify_runner`) → on le tire du PACKAGE installé, jamais
  # d'un fichier voisin (provision-ct.sh voyage seul sur l'hôte). Le wheel est l'artefact unique auto-contenu.
  src="$("$venv/bin/python" -c 'import forgemaster, pathlib; print(pathlib.Path(forgemaster.__file__).parent / "_verify_runner")' 2>/dev/null)"
  rdir="$home/runners"
  [ -n "$src" ] && [ -f "$src/render_check.js" ] && [ -f "$src/package.json" ] \
    || { echo "✗ runner absent du wheel installé ($src) — wheel sans forgemaster/_verify_runner ? (rebuild build-wheel.sh)" >&2; return 1; }
  command -v node >/dev/null 2>&1 || export PATH="$home/tools/bin:$PATH"
  command -v node >/dev/null 2>&1 \
    || { echo "✗ node introuvable (tools/bin) — le runner exige Node ; relance \`forgemaster toolchain install\`" >&2; return 1; }
  mkdir -p "$rdir"
  install -m 0644 "$src/render_check.js" "$rdir/render_check.js"
  install -m 0644 "$src/package.json"    "$rdir/package.json"
  echo "   runner posé : $rdir/render_check.js (+ playwright-core pinné)"
  ( cd "$rdir" && npm install --no-audit --no-fund --loglevel=error ) \
    || { echo "✗ npm install du runner échoué" >&2; return 1; }
  pw="$rdir/node_modules/playwright-core/cli.js"
  if [ "$(id -u)" -eq 0 ]; then
    node "$pw" install-deps chromium || echo "   (install-deps chromium : libs peut-être déjà là — verify tranchera)"
  else
    echo "   (non-root : libs système Chromium non posées — \`sudo node $pw install-deps chromium\` si verify échoue)"
  fi
  node "$pw" install chromium \
    || { echo "✗ install du browser chromium échouée (réseau ? cdn.playwright.dev)" >&2; return 1; }
  echo "   ✓ runner verify prêt (playwright-core + chromium sous ~/.cache/ms-playwright)"
}

# PATH du LOGIN shell (le terminal web est un `bash -l`). `/etc/profile` RÉINITIALISE `PATH` (root → défaut
# Debian), écrasant tout PATH hérité/injecté → sans ça `forgemaster`/`codemap`/`node` sont `command not found`
# dans le terminal (et le handoff « auto-run `forgemaster interview` » casse). On ré-ajoute le toolchain APRÈS ce
# reset : via `/etc/profile.d` (root, système) ou `~/.profile`+`~/.bashrc` (portée user), tous deux sourcés
# après `/etc/profile`. Idempotent (overwrite / guard). Le venv forgemaster + `tools/bin` d'abord.
install_login_path() {
  local line="export PATH=\"$venv/bin:$home/tools/bin:\$PATH\""
  if [ "$(id -u)" -eq 0 ]; then
    printf '# forgemaster — toolchain sur le PATH du login shell (terminal web), APRÈS le reset de /etc/profile.\n%s\n' \
      "$line" > /etc/profile.d/forgemaster-path.sh
    echo "   PATH login shell → /etc/profile.d/forgemaster-path.sh ($venv/bin + $home/tools/bin)"
  else
    for rc in "$HOME/.profile" "$HOME/.bashrc"; do
      grep -qs 'forgemaster toolchain PATH' "$rc" 2>/dev/null \
        || printf '# forgemaster toolchain PATH\n%s\n' "$line" >> "$rc"
    done
    echo "   PATH login shell → $HOME/.profile + ~/.bashrc ($venv/bin + $home/tools/bin)"
  fi
}

# Prérequis OS de l'install ELLE-MÊME (avant le venv) : une image cloud MINIMALE (Ubuntu/Debian genericcloud)
# n'inclut PAS python3-venv → `python3 -m venv` échoue (« ensurepip is not available »), NI git (bootstrap clone
# les 5 outils + tools install fait des pip git+https), NI forcément curl (install de Claude). Idempotent (skip
# si tout présent), fail-loud. apt exige root → `sudo` sinon. Ne PAS supposer un hôte pré-outillé (un vrai user
# part d'une image nue). cf. install.md (prérequis) + fix-shipped-product (le CT dev-base masquait ce trou).
ensure_base_deps() {
  local need=() APT=apt-get
  python3 -c 'import ensurepip' >/dev/null 2>&1 || need+=(python3-venv)
  command -v git  >/dev/null 2>&1 || need+=(git)
  command -v curl >/dev/null 2>&1 || need+=(curl ca-certificates)
  [ ${#need[@]} -eq 0 ] && { echo "   prérequis de base déjà présents (python3-venv, git, curl)"; return 0; }
  [ "$(id -u)" -eq 0 ] || APT="sudo apt-get"
  echo "   installe les prérequis de base via $APT : ${need[*]}…"
  $APT update -qq && $APT install -y -qq "${need[@]}" \
    || { echo "✗ install des prérequis de base échouée (apt indisponible ?) — pose ${need[*]} à la main puis relance" >&2; return 1; }
  python3 -c 'import ensurepip' >/dev/null 2>&1 \
    || { echo "✗ ensurepip toujours absent après install (python3-venv non posé ?)" >&2; return 1; }
  echo "   ✓ prérequis de base OK"
}

echo "→ [1/9] prérequis de base (python3-venv, git, curl) + venv Python : $venv"
ensure_base_deps
python3 -m venv "$venv"

echo "→ [2/9] install du wheel (sans Node — l'UI est empaquetée dans le wheel)"
"$venv/bin/pip" install --quiet --upgrade "$wheel"
echo -n "   "; "$forgemaster" --version

echo "→ [3/9] Claude Code dans le terminal ($([ "$with_claude" = yes ] && echo "installe" || echo "ignoré — passe --with-claude"))"
if [ "$with_claude" = "yes" ]; then install_claude; fi

# Outillage hôte-niveau : ce que les bundles DÉCLARENT (codemap/docsmap/frontmap + Node + ruff/pytest/mypy),
# installé dans un venv d'outils dédié sous FORGEMASTER_HOME et exposé sur tools/bin — le dispatch worker ET le
# gate natif préfixent ce bin au PATH. Sans ça, un worker sur n'importe quel type CONSTATE ses outils absents
# (le wheel n'expose que `forgemaster`). Idempotent, fail-loud. Les 3 cartes sont PUBLIQUES → clone ANONYME :
# cette étape ne reçoit AUCUN credential, même si un --token-file a été passé pour l'amorçage [7/9].
# Node via nodeenv (rootless), sans sudo.
echo "→ [4/9] outillage hôte-niveau (maps + Node + qualité py → $home/tools/bin) — sans credential"
"$forgemaster" tools install
echo "   runtime conteneur (podman) pour \`forgemaster deploy\` (P2)"
install_podman
echo "   moteur compose (podman-compose standalone — podman 4.3.1 de Debian 12 n'a pas \`podman compose\`)"
install_compose_provider
echo "   runner de vérification visuelle (gate Tier-1.5) + Chromium sous \$FORGEMASTER_HOME/runners"
seed_verify_runner
# Auto-vérification (fail-loud) : les binaires que les bundles DÉCLARENT résolvent-ils vraiment sous
# `tools_env` ? `forgemaster doctor` = SONDE PURE (rc 0 = tout présent ; rc 1 = un binaire manque, il le nomme
# + rappelle `forgemaster toolchain install`). Sur rc 1 on ABORTE l'install ICI, au lieu de laisser le 1er worker le
# constater absent. Sonde ≠ installe : on renvoie vers `forgemaster toolchain install`, on ne réinstalle pas en boucle.
echo "   auto-vérification de présence (forgemaster doctor)"
"$forgemaster" doctor || { echo "✗ [4/9] outillage INCOMPLET après install (détail ci-dessus) — corrige (\`forgemaster toolchain install\`) puis relance." >&2; exit 1; }
echo "   PATH du login shell (terminal web = bash -l) : ré-ajoute le toolchain après le reset /etc/profile"
install_login_path

echo "→ [5/9] unité systemd (portée $scope, host=$host port=$port)"
"$forgemaster" install-service $svc_flag --host "$host" --port "$port"

echo "→ [6/9] dépôt du manifeste d'outils sous FORGEMASTER_HOME"
mkdir -p "$home"
if [ -n "$manifest" ]; then
  install -m 0644 "$manifest" "$home/bootstrap.yaml"
  echo "   manifeste posé : $home/bootstrap.yaml ($(grep -c 'slug:' "$home/bootstrap.yaml") outil(s))"
else
  echo "   (pas de --manifest : install générique — le wizard /setup reste intact)"
fi

# Une adoption ratée est une DONNÉE manquante, pas une infrastructure cassée : elle ne doit pas empêcher le
# service d'exister. Mesuré le 2026-08-03 sur une VM vierge — deux dépôts du manifeste encore privés
# (`forgemaster`, `forgemaster-catalogs`) faisaient rc 1 ici, et `set -e` tuait l'install AVANT l'activation :
# forgemaster jamais
# activé, à cause de deux clones. Même conclusion pour un utilisateur dont une URL de manifeste a bougé.
# On NE MASQUE RIEN : `forgemaster bootstrap` a déjà imprimé un 🔴 par outil, on ajoute une bannière et la
# commande de reprise, et on la RÉPÈTE en fin de script pour qu'elle ne soit pas enterrée sous [8/9]+[9/9].
bootstrap_rc=0
echo "→ [7/9] amorçage des outils (idempotent — skip ceux déjà adoptés)"
if [ -n "$manifest" ]; then
  if [ -n "$token_file" ]; then "$forgemaster" bootstrap --token-file "$token_file" || bootstrap_rc=$?
  else "$forgemaster" bootstrap || bootstrap_rc=$?; fi
  if [ "$bootstrap_rc" != 0 ]; then
    echo "   ⚠ amorçage INCOMPLET (détail ci-dessus). L'install CONTINUE : le service doit exister même"
    echo "     si un dépôt du manifeste est injoignable (privé, renommé, hors ligne)."
    echo "     Reprise quand l'accès est rétabli (idempotent, ne re-clone pas l'adopté) :"
    echo "       $forgemaster bootstrap${token_file:+ --token-file <token>}"
  fi
else
  echo "   (rien à amorcer sans manifeste)"
fi

# Co-install du serveur MCP de corpus : l'instance fait tourner SON serveur au lieu d'en consommer un
# distant (§4 de la décision d'édition : deux topologies, et l'instance DIT laquelle elle est — lisible
# ensuite sur `GET /api/version`, clé `mcp`). Ce qu'on installe est un LECTEUR : la racine de corpus est la
# donnée de l'opérateur, jamais clonée par nous. Opt-in — sans `--with-mcp`, l'instance reste `remote` (ou
# `none` si aucun endpoint n'est câblé), ce qui est un état parfaitement valide.
echo "→ [8/9] serveur MCP de corpus ($([ -n "$mcp_data" ] && echo "co-installé depuis $mcp_data" || echo "ignoré — passe --with-mcp <racine-corpus>"))"
if [ -n "$mcp_data" ]; then
  mcp_args=(mcp install --data-root "$mcp_data" --port "$mcp_port")
  if [ "$scope" = "system" ]; then mcp_args+=(--system); fi
  # Le dépôt du serveur est encore PRIVÉ (sa publication est le volet suivant) : le même PAT de lecture
  # que l'amorçage sert au clone pip. Il devient inutile ici dès que le dépôt est public — même
  # trajectoire que les 3 cartes, anonymes depuis le 2026-08-03.
  if [ -n "$token_file" ]; then mcp_args+=(--token-file "$token_file"); fi
  "$forgemaster" "${mcp_args[@]}"
fi

echo "→ [9/9] activation du service"
if [ "$enable" = "no" ]; then
  echo "   (--no-enable) active-le : $sysctl daemon-reload && $sysctl enable --now forgemaster${mcp_data:+ forgemaster-catalogs}"
else
  if [ "$scope" = "user" ]; then
    loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"
  fi
  $sysctl daemon-reload
  $sysctl enable --now forgemaster
  # Le serveur co-installé est une pièce de cette instance, pas un extra : il monte avec elle. Sans ça,
  # `forgemaster mcp install` aurait posé une unité que personne ne démarre, et le câblage loopback pointerait
  # un port fermé — un « co-installé » qui ne répond pas est pire qu'un `remote` assumé.
  if [ -n "$mcp_data" ]; then $sysctl enable --now forgemaster-catalogs; fi
fi

echo "✓ forgemaster provisionné → http://$host:$port"
echo "  ouvre-le : le rail « Outils » présente les outils du manifeste, avec leur VRAI contenu git."
# `if` (pas `[ … ] && echo`) : en fin de script, un test faux hériterait son rc au script → sortie ≠ 0 en
# succès, ce qui casserait un appelant `set -e` / la CI-on-tag. On garde un rc 0 explicite.
if [ "$with_claude" = "yes" ]; then
  echo "  onglet Terminal : tape \`claude\` pour lancer Claude Code (login au 1er run)."
fi
# Répété EN DERNIER : une alerte enterrée sous deux étapes vertes n'est pas une alerte.
if [ "$bootstrap_rc" != 0 ]; then
  echo "  ⚠ RAPPEL — l'amorçage [7/9] est INCOMPLET : des outils du manifeste ne sont PAS adoptés"
  echo "    (le rail « Outils » les montrera absents). Relance \`$forgemaster bootstrap\` une fois l'accès rétabli."
fi
