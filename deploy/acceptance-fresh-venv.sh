#!/usr/bin/env bash
# acceptance-fresh-venv.sh — prouve que le wheel LIVRÉ est auto-suffisant : l'onglet **Flow** ET l'**UI servie**.
#
# Le venv de DEV du forgemaster a code-map en éditable (sibling) → il MASQUE le trou de packaging. Ici on crée un
# venv JETABLE, on installe le wheel SEUL, et on rejoue les 3 appels exacts du consommateur
# (`src/forgemaster/codemap/{index,flow}.py` : `python -m codemap --schema-version | build | flow --list`). C'est
# le test qui aurait attrapé « No module named codemap ». Aucun réseau, aucun code-map éditable.
#
# Puis [4/4] on démarre le daemon depuis ce même wheel et on vérifie que `/` sert la VRAIE SPA (pas le
# placeholder « UI non buildée ») : le wheel garde `_web_dist` STRUCTURELLEMENT (build-wheel.sh) — ici on
# prouve qu'elle est aussi servie COMPORTEMENTALEMENT (une régression de web_dist_dir/_mount_spa passerait
# sinon tous les gates verts). Sonde en `urllib` stdlib : `httpx`/TestClient sont dev-only, absents du wheel.
#
# Usage : deploy/acceptance-fresh-venv.sh [chemin.whl]   (défaut : le dernier dist/forgemaster-*.whl)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
whl="${1:-$(ls -t "$root"/dist/forgemaster-*-py3-none-any.whl 2>/dev/null | head -1)}"
[ -n "$whl" ] && [ -f "$whl" ] || {
  echo "✗ wheel introuvable — lance deploy/build-wheel.sh (ou passe le .whl en argument)" >&2; exit 1; }
echo "→ wheel testé : $whl"

tmp="$(mktemp -d)"; srv=""
trap 'set +e; [ -n "$srv" ] && kill "$srv" 2>/dev/null; rm -rf "$tmp"' EXIT
python3 -m venv "$tmp/venv"
py="$tmp/venv/bin/python"
"$py" -m pip install --quiet --upgrade "$whl"
# Garde : le venv ne doit PAS avoir un code-map éditable qui masquerait le trou (on teste bien le wheel seul).
"$py" -c "import codemap, sys; assert 'site-packages' in codemap.__file__, codemap.__file__" \
  || { echo "✗ code-map n'est pas celui du wheel (éditable ?) — acceptance invalide" >&2; exit 1; }

echo "→ [1/4] \`codemap --schema-version\` (le trou historique : sans code-map, ce call plantait)"
sv="$("$py" -m codemap --schema-version)"
[ -n "$sv" ] || { echo "✗ schema-version vide" >&2; exit 1; }
echo "   schema_version = $sv"

echo "→ [2/4] \`codemap build\` sur un repo seed"
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

echo "→ [3/4] \`codemap flow --list\` rend des opérations (contrat consommé par le forgemaster)"
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

echo "→ [4/4] le wheel SERT la SPA à / (UI empaquetée dans le wheel, servie — pas seulement présente)"
export FORGEMASTER_HOME="$tmp/home" FORGEMASTER_PROJECTS_ROOT="$tmp/projects"
port="$("$py" - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
)"
"$tmp/venv/bin/forgemaster" serve --host 127.0.0.1 --port "$port" >"$tmp/serve.log" 2>&1 & srv=$!
if ! "$py" - "$port" <<'PY'
import sys, time, urllib.request, urllib.error
base = f"http://127.0.0.1:{sys.argv[1]}"

def get(path):
    with urllib.request.urlopen(base + path, timeout=3) as r:
        return r.status, r.headers.get("content-type", ""), r.read().decode("utf-8", "replace")

# 1) attendre que le daemon réponde (fail-loud si jamais up)
for _ in range(30):
    try:
        if get("/health")[0] == 200:
            break
    except (urllib.error.URLError, ConnectionError, OSError):
        time.sleep(0.5)
else:
    sys.exit("✗ le daemon n'a pas démarré (/health muet après ~15 s)")

# 2) / sert la VRAIE SPA (pas le placeholder « UI non buildée »)
st, ct, body = get("/")
assert st == 200, f"✗ GET / = {st} (attendu 200)"
assert "text/html" in ct, f"✗ GET / content-type = {ct!r}"
assert 'id="root"' in body, '✗ GET / sans marqueur SPA id="root" (dist absente/non servie ?)'
assert "non buildée" not in body, "✗ GET / sert le PLACEHOLDER fail-loud, pas la vraie SPA"

# 3) deep-link inconnu → fallback SPA (index.html en 200), jamais 404
st, ct, body = get("/void-runner")
assert st == 200 and "text/html" in ct, f"✗ deep-link /void-runner = {st} {ct!r} (attendu 200 html)"
assert 'id="root"' in body, "✗ deep-link ne retombe pas sur l'index SPA"

print('   ✓ SPA servie à / (id="root", pas le placeholder) + fallback deep-link OK')
PY
then
  echo "   ↳ serve.log (20 dernières lignes) :" >&2; tail -n 20 "$tmp/serve.log" >&2 || true
  exit 1
fi
kill "$srv" 2>/dev/null; srv=""

echo "✓ acceptance venv-neuf OK — le wheel livré est auto-suffisant : Flow (code-map) + UI servie à /."
