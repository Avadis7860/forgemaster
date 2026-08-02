#!/usr/bin/env bash
# acceptance-update-rollback.sh — une MAJ qui casse doit se DÉFAIRE TOUTE SEULE, sans qu'on lui demande.
#
# Les tests unitaires gardent les décisions (quoi refuser, dans quel ordre arrêter/relancer) en simulant les
# étapes coûteuses. Ici, rien n'est simulé du produit : de VRAIS wheels bâtis depuis ce checkout, de VRAIS
# venvs créés par `python3 -m venv`, un VRAI daemon qui sert, une VRAIE base peuplée par l'API, et le VRAI
# verbe `cockpit update apply` lancé depuis le cockpit INSTALLÉ (pas depuis le repo).
#
# Deux actes, parce que « ça revient en arrière » ne vaut que si « ça passe » vaut aussi :
#   • acte 1 — un wheel sain est posé : le lien bascule, le daemon relancé sert le NOUVEAU build (comparaison
#     par SHA de build, pas par « il répond ») et les données sont intactes ;
#   • acte 2 — un wheel dont la migration casse sur une base PEUPLÉE : il passe la sonde en isolation (home
#     vierge → rien à migrer), écrit son dégât dans la vraie base, puis meurt. Personne ne diagnostique :
#     la machine rebascule le lien, restaure l'instantané, relance — et l'instance sert de nouveau.
#
# Le SEUL élément substitué est `systemctl` : la machine de test n'a pas de systemd utilisateur joignable
# (WSL sans session lingering). Le shim n'est pas un mime — il lit l'`ExecStart` de l'unité RÉELLE et
# démarre/arrête vraiment le processus, donc la bascule du lien est bien ce qui décide quel binaire sert.
# Ce qu'il ne prouve donc pas : que `systemctl` accepte l'unité (c'est le rôle d'`install-service`).
#
# Usage : deploy/acceptance-update-rollback.sh     (réseau requis : `pip install` résout fastapi & co)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${PYTHON:-$root/.venv/bin/python}"
[ -x "$py" ] || { echo "✗ interpréteur introuvable : $py (PYTHON=… pour le choisir)" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'set +e; [ -f "$tmp/shim.pid" ] && kill "$(cat "$tmp/shim.pid")" 2>/dev/null; rm -rf "$tmp"' EXIT
export COCKPIT_HOME="$tmp/home" COCKPIT_PROJECTS_ROOT="$tmp/projects"
export SHIM_UNIT="$tmp/fakehome/.config/systemd/user/cockpit.service" \
       SHIM_PIDFILE="$tmp/shim.pid" SHIM_LOG="$tmp/serve.log"

echo "→ [1/8] trois wheels RÉELS depuis ce checkout (aucun npm : l'UI n'est pas ce qu'on juge ici)"
# `build-wheel.sh` exige Node pour empaqueter la SPA ; ce qu'on vérifie ici est `/health` + `/api/version`,
# donc on rejoue ses étapes SANS le front. `taskmap` reste indispensable (le daemon l'importe).
mkdir -p "$root/build/vendor"
for pkg in codemap taskmap; do
  src="$root/../${pkg/codemap/code-map}"; src="${src/taskmap/task-map}"
  [ -d "$src/src/$pkg" ] || { echo "✗ sibling $src absent — clone-le à côté du cockpit" >&2; exit 1; }
  rm -rf "$root/build/vendor/$pkg"; cp -a "$src/src/$pkg" "$root/build/vendor/$pkg"
done
build_one() {   # $1 = sha de build tamponné, $2 = dossier de sortie
  printf '{"sha": "%s", "committed_at": "2026-08-02T00:00:00+00:00"}\n' "$1" > "$root/src/cockpit/_build.json"
  "$py" -m pip wheel --no-deps --quiet "$root" -w "$2"
  ls "$2"/cockpit-*.whl | head -1
}
whl_a="$(build_one "$(printf 'a%.0s' {1..40})" "$tmp/wa")"
whl_c="$(build_one "$(printf 'c%.0s' {1..40})" "$tmp/wc")"
rm -f "$root/src/cockpit/_build.json"          # artefact par-build, jamais laissé dans le checkout
echo "   sain (avant) : $(basename "$whl_a") · sain (après) : $(basename "$whl_c")"

"$py" - "$whl_c" "$tmp/wb" <<'PY'
import shutil, sys, zipfile
from pathlib import Path
src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
dst = out / src.name
# Le wheel EMPOISONNÉ : une « migration » qui ne casse QUE sur une base déjà peuplée. C'est le cas réaliste
# — la sonde en isolation part d'un home vierge, donc elle le laisse passer ; seul le vivant le révèle. Et
# il écrit son dégât AVANT de mourir : c'est ce qui rend l'instantané indispensable au retour arrière.
POISON = '''
import os as _os, pathlib as _pl, sqlite3 as _sq
_db = _pl.Path(_os.environ.get("COCKPIT_HOME", "/nulle-part")) / "cockpit.db"
if _db.exists():
    _c = _sq.connect(str(_db))
    if _c.execute("SELECT count(*) FROM projects").fetchone()[0]:
        _c.execute("INSERT INTO projects (id, slug, name, sot_path, created_at) VALUES (?,?,?,?,?)",
                   ("id-degat", "migration-a-moitie", "x", "/y.git", "2026-08-02T00:00:00Z"))
        _c.commit(); _c.close()
        raise RuntimeError("migration cassee : cette version ne sait pas monter une base peuplee")
    _c.close()
'''
cible = "cockpit/daemon/app.py"
# Injecté APRÈS le `from __future__` : en tête de fichier ce serait une SyntaxError, donc un wheel qui
# échoue DÈS la sonde en isolation — un autre chemin (correct, mais déjà couvert). Ce qu'on veut ici est le
# cas dur : une version qui passe en isolation et ne casse QUE sur la vraie base.
marqueur = "from __future__ import annotations\n"
zin = zipfile.ZipFile(src)
assert cible in zin.namelist(), cible
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == cible:
            texte = data.decode()
            assert marqueur in texte, f"{cible} n'a plus de `from __future__` — poison à replacer"
            data = texte.replace(marqueur, marqueur + POISON, 1).encode()
        zout.writestr(item, data)
zin.close()
print(f"   empoisonné : {dst.name} (migration qui casse sur base peuplée)")
PY
whl_b="$(ls "$tmp/wb"/cockpit-*.whl | head -1)"

echo "→ [2/8] installation RÉELLE du wheel « avant » + \`install-service\` (pose le lien stable)"
"$py" -m venv "$tmp/v1"
"$tmp/v1/bin/pip" install --quiet "$whl_a"
port="$("$py" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
HOME="$tmp/fakehome" "$tmp/v1/bin/cockpit" install-service --port "$port" | sed 's/^/   /'
grep -q "ExecStart=$COCKPIT_HOME/current/bin/cockpit" "$SHIM_UNIT" \
  || { echo "✗ l'unité ne passe pas par le lien stable — la bascule n'aurait aucun effet" >&2; exit 1; }
[ -L "$COCKPIT_HOME/current" ] || { echo "✗ lien stable non posé" >&2; exit 1; }

# Le shim `systemctl` : il LIT l'unité réelle et démarre/arrête vraiment le processus qu'elle déclare.
cat > "$tmp/systemctl" <<'PY'
#!/usr/bin/env python3
"""Faux `systemctl`, vrai superviseur : start = lance l'ExecStart de l'unité, stop = le tue et l'attend."""
import os, shlex, signal, subprocess, sys, time
from pathlib import Path

unit, pidfile = Path(os.environ["SHIM_UNIT"]), Path(os.environ["SHIM_PIDFILE"])
action = next(a for a in sys.argv[1:] if not a.startswith("-"))
text = unit.read_text(encoding="utf-8")
env = {**os.environ}
for line in text.splitlines():
    if line.startswith("Environment="):
        key, _, val = line.split("=", 1)[1].partition("=")
        env[key] = val


def stop():
    if not pidfile.exists():
        return
    pid = int(pidfile.read_text())
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(80):
            os.kill(pid, 0)
            time.sleep(0.25)
    except OSError:
        pass
    pidfile.unlink(missing_ok=True)


if action in ("stop", "restart"):
    stop()
if action in ("start", "restart"):
    argv = shlex.split([ln for ln in text.splitlines() if ln.startswith("ExecStart=")][-1].split("=", 1)[1])
    log = open(os.environ["SHIM_LOG"], "a")
    proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pidfile.write_text(str(proc.pid))
PY
chmod +x "$tmp/systemctl"

echo "→ [3/8] le service tourne, et un projet est créé PAR L'API (c'est le daemon qui écrit)"
"$tmp/systemctl" --user start cockpit
if ! "$py" - "$port" "$tmp" <<'PY'
import json, sys, time, urllib.error, urllib.request
from pathlib import Path
port, tmp = sys.argv[1], Path(sys.argv[2])
base = f"http://127.0.0.1:{port}"


def call(path, payload=None):
    req = urllib.request.Request(
        base + path, method="POST" if payload else "GET",
        data=json.dumps(payload).encode() if payload else None,
        headers={"content-type": "application/json"} if payload else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read() or b"null")


for _ in range(60):
    try:
        if call("/health")[0] == 200:
            break
    except (urllib.error.URLError, ConnectionError, OSError):
        time.sleep(0.5)
else:
    sys.exit("✗ le daemon installé n'a pas démarré")
assert call("/api/projects", {"slug": "atelier-fictif", "name": "atelier-fictif"})[0] == 201
sha = call("/api/version")[1]["sha"]
assert sha == "a" * 40, f"✗ le build servi n'est pas celui installé : {sha}"
print(f"   ✓ sert le build {sha[:12]} et porte 1 projet")
PY
then tail -n 20 "$SHIM_LOG" >&2; exit 1; fi

echo "→ [4/8] ACTE 1 — \`cockpit update apply\` avec un wheel SAIN : ça doit passer"
set +e
COCKPIT_HOME="$COCKPIT_HOME" "$tmp/v1/bin/cockpit" update apply --wheel "$whl_c" \
  --unit "$SHIM_UNIT" --systemctl "$tmp/systemctl" 2>&1 | sed 's/^/   /'
rc="${PIPESTATUS[0]}"
set -e
[ "$rc" = 0 ] || { echo "✗ une MAJ saine a échoué (rc=$rc)" >&2; tail -n 20 "$SHIM_LOG" >&2; exit 1; }

echo "→ [5/8] le vivant sert le NOUVEAU build, et les données ont traversé"
"$py" - "$port" "$COCKPIT_HOME" <<'PY'
import json, sys, urllib.request
from pathlib import Path
base, home = f"http://127.0.0.1:{sys.argv[1]}", Path(sys.argv[2])


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


sha = get("/api/version")["sha"]
assert sha == "c" * 40, f"✗ le vivant sert encore {sha[:12]} — la bascule n'a rien changé"
projets = [p["slug"] for p in get("/api/projects")["projects"]]
assert projets == ["atelier-fictif"], f"✗ données perdues par une MAJ SAINE : {projets}"
assert (home / "current").resolve().name != "v1", "✗ le lien n'a pas bougé"
print(f"   ✓ build {sha[:12]} servi, projet conservé, lien → {(home / 'current').resolve().name}")
PY
apres_acte1="$(readlink -f "$COCKPIT_HOME/current")"

echo "→ [6/8] ACTE 2 — wheel dont la MIGRATION casse sur une base peuplée : la MAJ doit échouer"
set +e
COCKPIT_HOME="$COCKPIT_HOME" "$tmp/v1/bin/cockpit" update apply --wheel "$whl_b" \
  --unit "$SHIM_UNIT" --systemctl "$tmp/systemctl" 2>&1 | tee "$tmp/acte2.log" | sed 's/^/   /'
rc="${PIPESTATUS[0]}"
set -e
[ "$rc" = 1 ] || { echo "✗ attendu rc=1 (échec + retour arrière), obtenu rc=$rc" >&2; exit 1; }
grep -q "revenue à l'état d'avant" "$tmp/acte2.log" \
  || { echo "✗ le verdict ne dit pas que l'instance est revenue en arrière" >&2; exit 1; }

echo "→ [7/8] retour arrière CONSTATÉ : le lien, le binaire servi ET la base"
[ "$(readlink -f "$COCKPIT_HOME/current")" = "$apres_acte1" ] \
  || { echo "✗ le lien n'est pas revenu sur $apres_acte1" >&2; exit 1; }
"$py" - "$port" <<'PY'
import json, sys, urllib.request
base = f"http://127.0.0.1:{sys.argv[1]}"


def call(path, payload=None):
    req = urllib.request.Request(
        base + path, method="POST" if payload else "GET",
        data=json.dumps(payload).encode() if payload else None,
        headers={"content-type": "application/json"} if payload else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read() or b"null")


sha = call("/api/version")[1]["sha"]
assert sha == "c" * 40, f"✗ le vivant sert {sha[:12]} — ce n'est pas la version d'avant la MAJ ratée"
projets = sorted(p["slug"] for p in call("/api/projects")[1]["projects"])
assert projets == ["atelier-fictif"], \
    f"✗ la base porte encore la trace de la migration ratée : {projets}"
print(f"   ✓ build {sha[:12]} de nouveau servi ; `migration-a-moitie` a disparu de la base")
PY

echo "→ [8/8] l'instance n'est pas seulement debout : elle écrit encore"
"$py" - "$port" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(
    f"http://127.0.0.1:{sys.argv[1]}/api/projects", method="POST",
    data=json.dumps({"slug": "apres-retour", "name": "apres-retour"}).encode(),
    headers={"content-type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    assert r.status == 201, r.status
print("   ✓ écriture acceptée après le retour arrière")
PY

echo "✓ acceptance MAJ+retour-arrière OK — une MAJ saine passe, une MAJ qui casse se défait toute seule."
