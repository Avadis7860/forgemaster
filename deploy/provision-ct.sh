#!/usr/bin/env bash
# provision-ct.sh — provisionne un hôte VIERGE en cockpit prêt-à-l'emploi, batteries incluses, EN UNE
# COMMANDE : venv → install du wheel → [Claude Code, opt-in] → service systemd → dépôt du manifeste d'outils
# → `cockpit bootstrap` → activation. AUCUN Node requis (l'UI voyage dans le wheel).
#
# À lancer SUR l'hôte cible, en tant que l'utilisateur qui fera tourner le service (JAMAIS root pour un
# service `--user` : la DB écrite par le bootstrap doit appartenir à l'utilisateur du service). Le wheel,
# le manifeste et l'éventuel token-file y ont été copiés au préalable (voir build-wheel.sh + docs/install.md).
#
# Propriétés (discipline no-footgun, cf. service.py) : IMPRIME chaque étape (rien en douce) ; IDEMPOTENT
# (ré-exécution sûre — venv réutilisé, bootstrap skip les outils déjà là, install-service n'écrase pas
# cockpit.env) ; FAIL-LOUD (`set -euo pipefail`) ; AUCUN secret en argv (le token passe par --token-file).
#
# Usage :
#   deploy/provision-ct.sh --wheel <chemin.whl> [--manifest deploy/bootstrap.yaml] [--token-file <token>]
#     [--venv ~/.venvs/cockpit] [--home ~/.cockpit] [--projects-root ~/projects]
#     [--host 0.0.0.0] [--port 8700] [--system] [--no-enable] [--with-claude]
set -euo pipefail

wheel=""; manifest=""; token_file=""
venv="$HOME/.venvs/cockpit"
home="${COCKPIT_HOME:-$HOME/.cockpit}"
projects_root="${COCKPIT_PROJECTS_ROOT:-$HOME/projects}"
host="0.0.0.0"; port="8700"
scope="user"; enable="yes"; with_claude="no"

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
    -h|--help)       sed -n '2,30p' "$0"; exit 0;;
    *) echo "argument inconnu : $1" >&2; exit 2;;
  esac
done

[ -n "$wheel" ] && [ -f "$wheel" ] || { echo "✗ --wheel <chemin.whl> requis (fichier existant)" >&2; exit 2; }
[ -z "$manifest" ] || [ -f "$manifest" ] || { echo "✗ manifeste introuvable : $manifest" >&2; exit 2; }
[ -z "$token_file" ] || [ -f "$token_file" ] || { echo "✗ token-file introuvable : $token_file" >&2; exit 2; }

export COCKPIT_HOME="$home" COCKPIT_PROJECTS_ROOT="$projects_root"
cockpit="$venv/bin/cockpit"

# systemctl : portée user (défaut, sans root) ou system (root). En user, le linger fait survivre le service
# à l'absence de session (hôte headless).
if [ "$scope" = "system" ]; then sysctl="sudo systemctl"; svc_flag="--system"; else sysctl="systemctl --user"; svc_flag=""; fi

# Claude Code (binaire abonnement — AUCUNE clé API ; login OAuth interactif au 1er `claude`). Installé pour
# l'utilisateur du service (= propriétaire du PTY du terminal web) : le PTY lance `bash -l`, qui lit
# ~/.profile/~/.bashrc → d'où le guard PATH sur ~/.local/bin. download-PUIS-exec (jamais `curl | bash` :
# l'installeur hérite du pipe comme stdin et échoue EN SILENCE en non-interactif) + retry x2 + vérif dure.
# Idempotent (skip si déjà présent). Méthode reprise du bake template LXC durci du vault.
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
    if curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh && bash /tmp/claude-install.sh; then ok=1; break; fi
    echo "   (retry $attempt) install Claude échouée — nouvelle tentative…"; sleep 3
  done
  rm -f /tmp/claude-install.sh
  { [ -n "$ok" ] && command -v claude >/dev/null 2>&1; } || { echo "✗ Claude Code absent après install" >&2; return 1; }
  echo "   ✓ Claude OK : $(claude --version 2>/dev/null || echo installé) — tape \`claude\` dans le terminal pour te connecter"
}

echo "→ [1/7] venv Python : $venv"
python3 -m venv "$venv"

echo "→ [2/7] install du wheel (sans Node — l'UI est empaquetée dans le wheel)"
"$venv/bin/pip" install --quiet --upgrade "$wheel"
echo -n "   "; "$cockpit" --version

echo "→ [3/7] Claude Code dans le terminal ($([ "$with_claude" = yes ] && echo "installe" || echo "ignoré — passe --with-claude"))"
if [ "$with_claude" = "yes" ]; then install_claude; fi

echo "→ [4/7] unité systemd (portée $scope, host=$host port=$port)"
"$cockpit" install-service $svc_flag --host "$host" --port "$port"

echo "→ [5/7] dépôt du manifeste d'outils sous COCKPIT_HOME"
mkdir -p "$home"
if [ -n "$manifest" ]; then
  install -m 0644 "$manifest" "$home/bootstrap.yaml"
  echo "   manifeste posé : $home/bootstrap.yaml ($(grep -c 'slug:' "$home/bootstrap.yaml") outil(s))"
else
  echo "   (pas de --manifest : install générique — le wizard /setup reste intact)"
fi

echo "→ [6/7] amorçage des outils (idempotent — skip ceux déjà adoptés)"
if [ -n "$manifest" ]; then
  if [ -n "$token_file" ]; then "$cockpit" bootstrap --token-file "$token_file"; else "$cockpit" bootstrap; fi
else
  echo "   (rien à amorcer sans manifeste)"
fi

echo "→ [7/7] activation du service"
if [ "$enable" = "no" ]; then
  echo "   (--no-enable) active-le : $sysctl daemon-reload && $sysctl enable --now cockpit"
else
  if [ "$scope" = "user" ]; then
    loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"
  fi
  $sysctl daemon-reload
  $sysctl enable --now cockpit
fi

echo "✓ cockpit provisionné → http://$host:$port"
echo "  ouvre-le : le rail « Outils » présente les outils du manifeste, avec leur VRAI contenu git."
[ "$with_claude" = "yes" ] && echo "  onglet Terminal : tape \`claude\` pour lancer Claude Code (login au 1er run)."
