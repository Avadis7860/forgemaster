#!/usr/bin/env bash
# acceptance-fresh-venv.sh — prouve que le wheel LIVRÉ est auto-suffisant pour l'onglet **Flow**.
#
# Le venv de DEV du cockpit a code-map en éditable (sibling) → il MASQUE le trou de packaging. Ici on crée un
# venv JETABLE, on installe le wheel SEUL, et on rejoue les 3 appels exacts du consommateur
# (`src/cockpit/codemap/{index,flow}.py` : `python -m codemap --schema-version | build | flow --list`). C'est
# le test qui aurait attrapé « No module named codemap ». Aucun réseau, aucun code-map éditable.
#
# Usage : deploy/acceptance-fresh-venv.sh [chemin.whl]   (défaut : le dernier dist/cockpit-*.whl)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
whl="${1:-$(ls -t "$root"/dist/cockpit-*-py3-none-any.whl 2>/dev/null | head -1)}"
[ -n "$whl" ] && [ -f "$whl" ] || {
  echo "✗ wheel introuvable — lance deploy/build-wheel.sh (ou passe le .whl en argument)" >&2; exit 1; }
echo "→ wheel testé : $whl"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
python3 -m venv "$tmp/venv"
py="$tmp/venv/bin/python"
"$py" -m pip install --quiet --upgrade "$whl"
# Garde : le venv ne doit PAS avoir un code-map éditable qui masquerait le trou (on teste bien le wheel seul).
"$py" -c "import codemap, sys; assert 'site-packages' in codemap.__file__, codemap.__file__" \
  || { echo "✗ code-map n'est pas celui du wheel (éditable ?) — acceptance invalide" >&2; exit 1; }

echo "→ [1/3] \`codemap --schema-version\` (le trou historique : sans code-map, ce call plantait)"
sv="$("$py" -m codemap --schema-version)"
[ -n "$sv" ] || { echo "✗ schema-version vide" >&2; exit 1; }
echo "   schema_version = $sv"

echo "→ [2/3] \`codemap build\` sur un repo seed"
seed="$tmp/seed"; mkdir -p "$seed/app"
cat > "$seed/app/main.py" <<'PY'
def helper():
    return 1

def run():
    def inner():
        return helper()
    return inner()

if __name__ == "__main__":
    run()
PY
( cd "$seed" && git init -q && git add -A && git -c user.email=a@b.c -c user.name=acc commit -qm seed )
"$py" -m codemap build --root "$seed" >/dev/null

echo "→ [3/3] \`codemap flow --list\` rend des opérations (contrat consommé par le cockpit)"
"$py" - "$seed" <<'PY'
import subprocess, sys, json
seed = sys.argv[1]
out = subprocess.run([sys.executable, "-m", "codemap", "flow", "--list", "--root", seed],
                     capture_output=True, text=True, check=True).stdout
data = json.loads(out)
ops = data.get("operations", data) if isinstance(data, dict) else data
assert ops, f"aucune opération rendue : {out[:200]}"
print(f"   opérations découvertes : {len(ops)}")
PY

echo "✓ acceptance venv-neuf OK — le wheel livré est auto-suffisant pour Flow (sans code-map éditable)."
