#!/usr/bin/env bash
# build-wheel.sh — construit le wheel turnkey du forgemaster DEPUIS HEAD (édition maintainer, D5).
#
# Node est requis ICI (build-time uniquement) pour empaqueter la SPA sous `forgemaster/_web_dist` dans le wheel ;
# l'hôte CIBLE n'aura besoin que de Python. Le wheel embarque AUSSI deux packages sibling stdlib-purs, stagés
# ici : `codemap` (l'outil code-map, `sys.executable -m codemap` → l'onglet Flow marche sans rien installer)
# ET `taskmap` (moteur DAG importé par `roadmap/resolver.py` → wheel AUTO-CONTENU, plus de dép git privée
# `task-map @ git+…` à cloner au `pip install` sur l'hôte cible). Sortie :
# `dist/forgemaster-<version>-py3-none-any.whl`, prêt à copier sur un hôte vierge et à donner à `provision-ct.sh`.
#
# Usage : deploy/build-wheel.sh   (depuis n'importe où dans le checkout ; Node ≥ 18 + npm requis)
#   FORGEMASTER_VENDOR_CODEMAP_SRC=<chemin> / FORGEMASTER_VENDOR_TASKMAP_SRC=<chemin> pour pointer un checkout hors
#   sibling (défauts ../code-map, ../task-map).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # racine du checkout
cd "$root"

# stage_tracked <repo> <sous-chemin> <dest> — stage EXACTEMENT les fichiers que git SUIT sous <sous-chemin>.
#
# Pourquoi pas `cp -a` : le wheel a DEUX portes et une seule filtrait. Le walk de `packages =
# ["src/forgemaster"]` écarte ce que le `.gitignore` écarte ; `force_include` (hatch_build.py) embarque le
# dossier TEL QU'IL EST SUR LE DISQUE. Mesuré le 2026-08-08 : `deploy/runners` = 2 fichiers suivis, 14 Mo sur
# le disque (un `npm install` local pour éprouver le runner, geste normal) → 135 membres / 12,7 Mo de
# `node_modules` MORT dans chaque wheel publié, et le contenu de l'artefact dépendait donc du POSTE qui l'a
# bâti. Ce helper rend la seconde porte symétrique de la première : ce qui entre est ce que git suit.
# Une exclusion nommée (`--exclude node_modules`) aurait laissé passer le résidu suivant ; la liste positive
# non.
stage_tracked() {
  local repo="$1" sub="$2" dest="$3" liste rel n=0
  git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "✗ '$repo' n'est pas un checkout git — impossible de savoir ce qui y est DÉCLARÉ, donc impossible" >&2
    echo "  d'en tirer un wheel de release reproductible (et sa provenance sortirait vide, en silence)." >&2
    exit 1; }
  rm -rf "$dest"; mkdir -p "$dest" "$root/build/declared"
  # La liste DÉCLARÉE, écrite une fois et servant DEUX fois : elle pilote la copie ci-dessous ET la garde
  # d'inventaire de l'étape 4/4. Un seul foyer de vérité — ce qui est stagé et ce qui est admis dans le wheel
  # ne peuvent pas diverger. `git ls-files` lancé DEPUIS le sous-dossier rend des chemins RELATIFS à lui.
  liste="$root/build/declared/$(basename "$dest").txt"
  git -C "$repo/$sub" ls-files > "$liste"
  while IFS= read -r rel; do
    mkdir -p "$dest/$(dirname "$rel")"
    cp -p "$repo/$sub/$rel" "$dest/$rel" || {
      echo "✗ '$repo/$sub/$rel' est suivi par git mais illisible — checkout amputé, pas de release." >&2
      exit 1; }
    n=$((n + 1))
  done < "$liste"
  [ "$n" -gt 0 ] || { echo "✗ aucun fichier suivi sous '$repo/$sub' — staging vide" >&2; exit 1; }
  echo "   $repo/$sub → $dest ($n fichiers suivis)"
}

echo "→ [1/4] build de la SPA (Node build-time) → web/dist"
( cd web && npm ci && npm run build )
test -f web/dist/index.html || { echo "✗ web/dist/index.html absent — build front cassé" >&2; exit 1; }

echo "→ [2/4] staging des packages sibling vendorés → build/vendor/{codemap,taskmap} (embarqués top-level)"
mkdir -p build/vendor
# code-map (outil Flow) — sibling ../code-map
cm_src="${FORGEMASTER_VENDOR_CODEMAP_SRC:-$root/../code-map}"
test -f "$cm_src/src/codemap/__main__.py" || {
  echo "✗ code-map introuvable à '$cm_src/src/codemap' — clone le repo code-map à côté du forgemaster," >&2
  echo "  ou pointe FORGEMASTER_VENDOR_CODEMAP_SRC vers un checkout code-map." >&2; exit 1; }
stage_tracked "$cm_src" "src/codemap" "build/vendor/codemap"
# provenance (non-secret) : d'où vient le code-map empaqueté — utile au debug du contrat schema_version.
git -C "$cm_src" rev-parse HEAD > build/vendor/codemap/_vendored_from.txt
# task-map (moteur DAG importé par le daemon) — sibling ../task-map ; vendoré → plus de dép git privée
tm_src="${FORGEMASTER_VENDOR_TASKMAP_SRC:-$root/../task-map}"
test -f "$tm_src/src/taskmap/__init__.py" || {
  echo "✗ task-map introuvable à '$tm_src/src/taskmap' — clone le repo task-map à côté du forgemaster," >&2
  echo "  ou pointe FORGEMASTER_VENDOR_TASKMAP_SRC vers un checkout task-map." >&2; exit 1; }
stage_tracked "$tm_src" "src/taskmap" "build/vendor/taskmap"
git -C "$tm_src" rev-parse HEAD > build/vendor/taskmap/_vendored_from.txt
# verify-runner (gate Tier-1.5) — vit DANS le repo (deploy/runners) ; stagé pour n'embarquer qu'en release
# (l'editable saute, comme codemap/taskmap). → forgemaster/_verify_runner, tiré du wheel par provision-ct.sh.
# Les 2 fichiers suivis sont EXACTEMENT ceux que `seed_verify_runner` (provision-ct.sh) `install` côté cible,
# avant d'y lancer son propre `npm install` : le `node_modules` du poste de dev n'a jamais été lu par personne.
stage_tracked "$root" "deploy/runners" "build/vendor/verify-runner"

echo "→ [2b/4] tampon de provenance de build → src/forgemaster/_build.json (SHA de HEAD, jamais mtime)"
# Le signal de fraîcheur (`forgemaster/build_provenance.py`) lit ce tampon : de quel commit ce wheel est né,
# pour se comparer au HEAD du miroir SoT local. Gitignoré (artefact par-build, jamais committé) → embarqué
# par force_include (hatch_build.py). Fail-loud si git ne résout pas HEAD (provenance = SHA, pas mtime).
build_sha="$(git -C "$root" rev-parse HEAD)" || { echo "✗ git rev-parse HEAD échoue — provenance impossible" >&2; exit 1; }
build_committed_at="$(git -C "$root" show -s --format=%cI HEAD)" || { echo "✗ git show HEAD échoue" >&2; exit 1; }
printf '{"sha": "%s", "committed_at": "%s"}\n' "$build_sha" "$build_committed_at" > "$root/src/forgemaster/_build.json"

echo "→ [3/4] build du wheel (le hook hatch embarque web/dist → forgemaster/_web_dist, codemap → codemap, taskmap → taskmap)"
rm -f dist/forgemaster-*-py3-none-any.whl
python3 -m pip wheel --no-deps . -w dist/
whl="$(ls -t dist/forgemaster-*-py3-none-any.whl | head -1)"

echo "→ [4/4] garde-fou : PRÉSENCE (UI + contrat + code-map + taskmap + attribution) ET INVENTAIRE (rien d'autre)"
# La liste déclarée de `src/forgemaster` : le walk de `packages=` applique déjà le `.gitignore` (mesuré le
# 2026-08-08 : 287 membres pour 287 fichiers suivis), mais la garde ne le SUPPOSE pas — elle le vérifie.
git -C src/forgemaster ls-files > build/declared/src-forgemaster.txt
python3 - "$whl" build/declared <<'PY'
import sys, zipfile
from pathlib import Path

from hatch_build import unexpected_wheel_members

whl, declared = sys.argv[1], Path(sys.argv[2])
names = zipfile.ZipFile(whl).namelist()
assert any(n.startswith("forgemaster/_web_dist/") and n.endswith("index.html") for n in names), \
    f"UI absente du wheel {whl} — web/dist non embarquée (relance après `npm run build`)"
assert "codemap/__main__.py" in names and "codemap/cli.py" in names, \
    f"code-map absent du wheel {whl} — build/vendor/codemap non embarqué (Flow serait mort côté cible)"
assert "taskmap/__init__.py" in names and "taskmap/core/__init__.py" in names, \
    f"taskmap absent du wheel {whl} — build/vendor/taskmap non embarqué (daemon mort : No module named taskmap)"
assert "forgemaster/_verify_runner/render_check.js" in names, \
    f"runner verify absent du wheel {whl} — build/vendor/verify-runner non embarqué (gate verify mort côté cible)"
assert "forgemaster/_ui_contract.json" in names, \
    f"contrat d'interface absent du wheel {whl} — sans lui l'applicateur ne peut PAS juger si la page " \
    f"s'affiche : il dégraderait honnêtement sur /health + SHA, donc ce wheel-ci pourrait poser un écran " \
    f"blanc sans que rien ne le voie. C'est exactement le faux-vert que le détecteur de panne ferme."
assert "forgemaster/_build.json" in names, \
    f"provenance de build absente du wheel {whl} — src/forgemaster/_build.json non embarqué (le signal de " \
    f"fraîcheur serait aveugle : c'est le faux-vert qu'on corrige)"
# Le wheel est un artefact COMPOSITE : AGPL-3.0 dans son ensemble, avec `codemap/` et `taskmap/` sous
# Apache-2.0. §4(a) exige de livrer une copie de la licence Apache au destinataire, §4(d) d'y propager le
# NOTICE. Sans ces deux fichiers, le wheel embarque du code sans son attribution — la symétrie du garde-fou
# ci-dessus : on échoue déjà si le CODE manque, on échoue désormais si son ATTRIBUTION manque.
lic = [n for n in names if "/licenses/" in n]
assert any(n.endswith("/licenses/NOTICE") for n in lic), \
    f"NOTICE absent du wheel {whl} — attribution Apache-2.0 §4(d) manquante pour codemap/ et taskmap/ " \
    f"(vérifie `license-files` du pyproject ET hatchling>=1.27 : en dessous il dégrade EN SILENCE)"
assert any(n.endswith("/licenses/LICENSES/Apache-2.0.txt") for n in lic), \
    f"texte Apache-2.0 absent du wheel {whl} — §4(a) exige d'en livrer une copie au destinataire"

# INVENTAIRE — la moitié que les asserts ci-dessus ne peuvent PAS porter : ils disent « c'est là », jamais
# « et rien d'autre ». Une garde de présence laisse passer, par construction, tout ce qui s'ajoute — c'est par
# là que 12,7 Mo de `node_modules` sont partis dans les releases jusqu'au 2026-08-08.
def lire(nom):
    return {ligne for ligne in (declared / nom).read_text().splitlines() if ligne}


trop = unexpected_wheel_members(
    names,
    src_files=lire("src-forgemaster.txt"),
    codemap_files=lire("codemap.txt"),
    taskmap_files=lire("taskmap.txt"),
    runner_files=lire("verify-runner.txt"),
)
if trop:
    for n in trop[:10]:
        print(f"   ✗ {n}", file=sys.stderr)
    reste = len(trop) - 10
    if reste > 0:
        print(f"   … et {reste} autre(s)", file=sys.stderr)
    sys.exit(
        f"✗ {len(trop)} membre(s) NON DÉCLARÉ(S) dans {whl}. Le wheel n'embarque QUE ce que git suit "
        f"(+ la SPA buildée et le tampon de build). Un membre en trop = un artefact dont le contenu dépend "
        f"du poste qui l'a bâti : c'est ce que la phase 4·0 ferme. Ne « corrige » pas en élargissant la "
        f"liste — regarde d'où vient le fichier."
    )
print(f"   ✓ inventaire clos : {len(names)} membres, tous déclarés")
PY

echo "✓ wheel prêt : $whl  (UI + code-map + taskmap + leur attribution embarqués)"
echo "  → copie-le sur l'hôte cible avec deploy/{provision-ct.sh,bootstrap.yaml}, puis lance provision-ct.sh."
