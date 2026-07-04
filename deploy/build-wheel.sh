#!/usr/bin/env bash
# build-wheel.sh — construit le wheel turnkey du cockpit DEPUIS HEAD (édition maintainer, D5).
#
# Node est requis ICI (build-time uniquement) pour empaqueter la SPA sous `cockpit/_web_dist` dans le wheel ;
# l'hôte CIBLE n'aura besoin que de Python. Le wheel embarque AUSSI le package `codemap` (l'outil code-map,
# stdlib-pur), stagé depuis le sibling `../code-map` → l'onglet Flow marche sans rien installer de plus
# (cf. src/cockpit/codemap/index.py : `sys.executable -m codemap`). Sortie :
# `dist/cockpit-<version>-py3-none-any.whl`, prêt à copier sur un hôte vierge et à donner à `provision-ct.sh`.
#
# Usage : deploy/build-wheel.sh   (depuis n'importe où dans le checkout ; Node ≥ 18 + npm requis)
#   COCKPIT_VENDOR_CODEMAP_SRC=<chemin> pour pointer un checkout code-map hors sibling (défaut ../code-map).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # racine du checkout
cd "$root"

echo "→ [1/4] build de la SPA (Node build-time) → web/dist"
( cd web && npm ci && npm run build )
test -f web/dist/index.html || { echo "✗ web/dist/index.html absent — build front cassé" >&2; exit 1; }

echo "→ [2/4] staging de code-map (sibling) → build/vendor/codemap (embarqué comme package `codemap`)"
cm_src="${COCKPIT_VENDOR_CODEMAP_SRC:-$root/../code-map}"
test -f "$cm_src/src/codemap/__main__.py" || {
  echo "✗ code-map introuvable à '$cm_src/src/codemap' — clone le repo code-map à côté du cockpit," >&2
  echo "  ou pointe COCKPIT_VENDOR_CODEMAP_SRC vers un checkout code-map." >&2; exit 1; }
rm -rf build/vendor/codemap
mkdir -p build/vendor
cp -a "$cm_src/src/codemap" build/vendor/codemap
# provenance (non-secret) : d'où vient le code-map empaqueté — utile au debug du contrat schema_version.
( cd "$cm_src" && git rev-parse HEAD 2>/dev/null ) > build/vendor/codemap/_vendored_from.txt || true

echo "→ [3/4] build du wheel (le hook hatch embarque web/dist → cockpit/_web_dist ET build/vendor/codemap → codemap)"
rm -f dist/cockpit-*-py3-none-any.whl
python3 -m pip wheel --no-deps . -w dist/
whl="$(ls -t dist/cockpit-*-py3-none-any.whl | head -1)"

echo "→ [4/4] garde-fou : l'UI ET code-map DOIVENT être embarqués (sinon écran blanc / Flow mort côté cible)"
python3 - "$whl" <<'PY'
import sys, zipfile
whl = sys.argv[1]
names = zipfile.ZipFile(whl).namelist()
assert any(n.startswith("cockpit/_web_dist/") and n.endswith("index.html") for n in names), \
    f"UI absente du wheel {whl} — web/dist non embarquée (relance après `npm run build`)"
assert "codemap/__main__.py" in names and "codemap/cli.py" in names, \
    f"code-map absent du wheel {whl} — build/vendor/codemap non embarqué (Flow serait mort côté cible)"
PY

echo "✓ wheel prêt : $whl  (UI + code-map embarqués)"
echo "  → copie-le sur l'hôte cible avec deploy/{provision-ct.sh,bootstrap.yaml}, puis lance provision-ct.sh."
