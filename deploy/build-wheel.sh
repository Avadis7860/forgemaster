#!/usr/bin/env bash
# build-wheel.sh — construit le wheel turnkey du cockpit DEPUIS HEAD (édition maintainer, D5).
#
# Node est requis ICI (build-time uniquement) pour empaqueter la SPA sous `cockpit/_web_dist` dans le wheel ;
# l'hôte CIBLE n'aura besoin que de Python. Sortie : `dist/cockpit-<version>-py3-none-any.whl`, prêt à copier
# sur un hôte vierge et à donner à `provision-ct.sh`.
#
# Usage : deploy/build-wheel.sh   (depuis n'importe où dans le checkout ; Node ≥ 18 + npm requis)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # racine du checkout
cd "$root"

echo "→ [1/3] build de la SPA (Node build-time) → web/dist"
( cd web && npm ci && npm run build )
test -f web/dist/index.html || { echo "✗ web/dist/index.html absent — build front cassé" >&2; exit 1; }

echo "→ [2/3] build du wheel (le hook hatch embarque web/dist → cockpit/_web_dist)"
rm -f dist/cockpit-*-py3-none-any.whl
python3 -m pip wheel --no-deps . -w dist/
whl="$(ls -t dist/cockpit-*-py3-none-any.whl | head -1)"

echo "→ [3/3] garde-fou : l'UI DOIT être embarquée (sinon écran blanc côté cible)"
python3 - "$whl" <<'PY'
import sys, zipfile
whl = sys.argv[1]
names = zipfile.ZipFile(whl).namelist()
assert any(n.startswith("cockpit/_web_dist/") and n.endswith("index.html") for n in names), \
    f"UI absente du wheel {whl} — web/dist non embarquée (relance après `npm run build`)"
PY

echo "✓ wheel prêt : $whl"
echo "  → copie-le sur l'hôte cible avec deploy/{provision-ct.sh,bootstrap.yaml}, puis lance provision-ct.sh."
