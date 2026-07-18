#!/usr/bin/env bash
# build-wheel.sh — construit le wheel turnkey du cockpit DEPUIS HEAD (édition maintainer, D5).
#
# Node est requis ICI (build-time uniquement) pour empaqueter la SPA sous `cockpit/_web_dist` dans le wheel ;
# l'hôte CIBLE n'aura besoin que de Python. Le wheel embarque AUSSI deux packages sibling stdlib-purs, stagés
# ici : `codemap` (l'outil code-map, `sys.executable -m codemap` → l'onglet Flow marche sans rien installer)
# ET `taskmap` (moteur DAG importé par `roadmap/resolver.py` → wheel AUTO-CONTENU, plus de dép git privée
# `task-map @ git+…` à cloner au `pip install` sur l'hôte cible). Sortie :
# `dist/cockpit-<version>-py3-none-any.whl`, prêt à copier sur un hôte vierge et à donner à `provision-ct.sh`.
#
# Usage : deploy/build-wheel.sh   (depuis n'importe où dans le checkout ; Node ≥ 18 + npm requis)
#   COCKPIT_VENDOR_CODEMAP_SRC=<chemin> / COCKPIT_VENDOR_TASKMAP_SRC=<chemin> pour pointer un checkout hors
#   sibling (défauts ../code-map, ../task-map).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # racine du checkout
cd "$root"

echo "→ [1/4] build de la SPA (Node build-time) → web/dist"
( cd web && npm ci && npm run build )
test -f web/dist/index.html || { echo "✗ web/dist/index.html absent — build front cassé" >&2; exit 1; }

echo "→ [2/4] staging des packages sibling vendorés → build/vendor/{codemap,taskmap} (embarqués top-level)"
mkdir -p build/vendor
# code-map (outil Flow) — sibling ../code-map
cm_src="${COCKPIT_VENDOR_CODEMAP_SRC:-$root/../code-map}"
test -f "$cm_src/src/codemap/__main__.py" || {
  echo "✗ code-map introuvable à '$cm_src/src/codemap' — clone le repo code-map à côté du cockpit," >&2
  echo "  ou pointe COCKPIT_VENDOR_CODEMAP_SRC vers un checkout code-map." >&2; exit 1; }
rm -rf build/vendor/codemap
cp -a "$cm_src/src/codemap" build/vendor/codemap
# provenance (non-secret) : d'où vient le code-map empaqueté — utile au debug du contrat schema_version.
( cd "$cm_src" && git rev-parse HEAD 2>/dev/null ) > build/vendor/codemap/_vendored_from.txt || true
# task-map (moteur DAG importé par le daemon) — sibling ../task-map ; vendoré → plus de dép git privée
tm_src="${COCKPIT_VENDOR_TASKMAP_SRC:-$root/../task-map}"
test -f "$tm_src/src/taskmap/__init__.py" || {
  echo "✗ task-map introuvable à '$tm_src/src/taskmap' — clone le repo task-map à côté du cockpit," >&2
  echo "  ou pointe COCKPIT_VENDOR_TASKMAP_SRC vers un checkout task-map." >&2; exit 1; }
rm -rf build/vendor/taskmap
cp -a "$tm_src/src/taskmap" build/vendor/taskmap
( cd "$tm_src" && git rev-parse HEAD 2>/dev/null ) > build/vendor/taskmap/_vendored_from.txt || true
# verify-runner (gate Tier-1.5) — vit DANS le repo (deploy/runners) ; stagé pour n'embarquer qu'en release
# (l'editable saute, comme codemap/taskmap). → cockpit/_verify_runner, tiré du wheel par provision-ct.sh.
rm -rf build/vendor/verify-runner
cp -a "$root/deploy/runners" build/vendor/verify-runner

echo "→ [3/4] build du wheel (le hook hatch embarque web/dist → cockpit/_web_dist, codemap → codemap, taskmap → taskmap)"
rm -f dist/cockpit-*-py3-none-any.whl
python3 -m pip wheel --no-deps . -w dist/
whl="$(ls -t dist/cockpit-*-py3-none-any.whl | head -1)"

echo "→ [4/4] garde-fou : UI + code-map + taskmap DOIVENT être embarqués (sinon écran blanc / Flow mort / daemon mort)"
python3 - "$whl" <<'PY'
import sys, zipfile
whl = sys.argv[1]
names = zipfile.ZipFile(whl).namelist()
assert any(n.startswith("cockpit/_web_dist/") and n.endswith("index.html") for n in names), \
    f"UI absente du wheel {whl} — web/dist non embarquée (relance après `npm run build`)"
assert "codemap/__main__.py" in names and "codemap/cli.py" in names, \
    f"code-map absent du wheel {whl} — build/vendor/codemap non embarqué (Flow serait mort côté cible)"
assert "taskmap/__init__.py" in names and "taskmap/core/__init__.py" in names, \
    f"taskmap absent du wheel {whl} — build/vendor/taskmap non embarqué (daemon mort : No module named taskmap)"
assert "cockpit/_verify_runner/render_check.js" in names, \
    f"runner verify absent du wheel {whl} — build/vendor/verify-runner non embarqué (gate verify mort côté cible)"
PY

echo "✓ wheel prêt : $whl  (UI + code-map + taskmap embarqués)"
echo "  → copie-le sur l'hôte cible avec deploy/{provision-ct.sh,bootstrap.yaml}, puis lance provision-ct.sh."
