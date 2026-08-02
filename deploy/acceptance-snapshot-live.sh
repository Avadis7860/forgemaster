#!/usr/bin/env bash
# acceptance-snapshot-live.sh — prouve que l'instantané se prend sur une instance VIVANTE, sans la perturber.
#
# Les tests unitaires simulent l'écrivain concurrent ; ici c'est le VRAI daemon qui écrit, par la VRAIE API,
# et le VRAI verbe `cockpit snapshot create` qui prend — daemon NON arrêté. Ce que ce niveau prouve et qu'un
# test unitaire ne peut pas montrer : la prise n'est PAS une écriture (« VACUUM (but not VACUUM INTO) is a
# write operation »), donc l'instance reste saine, sert toujours et accepte encore d'écrire APRÈS.
#
# Ce que ce script ne prétend PAS prouver : la perte qu'un `cp` subirait. Constaté en écrivant ce script —
# `daemon/deps.py` ouvre une connexion PAR REQUÊTE et la referme, donc SQLite checkpointe et supprime le
# `-wal` : au repos le daemon ne laisse pas de WAL peuplé. Le régime dangereux n'arrive que connexion TENUE
# (requête en vol, `cockpit run` et ses N connexions-par-thread). L'étape [3/5] CONSTATE l'état réel au lieu
# de le fabriquer ; la perte elle-même est prouvée au niveau unitaire
# (`tests/test_snapshot.py::test_prise_a_chaud_emporte_le_valide_qui_vit_encore_dans_le_wal`, falsifié par
# mutation : une copie naïve rougit ce test-là et lui seul).
#
# Usage : deploy/acceptance-snapshot-live.sh            (utilise le venv du repo)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${PYTHON:-$root/.venv/bin/python}"
[ -x "$py" ] || { echo "✗ interpréteur introuvable : $py (PYTHON=… pour le choisir)" >&2; exit 1; }

tmp="$(mktemp -d)"; srv=""
trap 'set +e; [ -n "$srv" ] && kill "$srv" 2>/dev/null; rm -rf "$tmp"' EXIT
export COCKPIT_HOME="$tmp/home" COCKPIT_PROJECTS_ROOT="$tmp/projects" PYTHONPATH="$root/src"

port="$("$py" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
echo "→ daemon sur 127.0.0.1:$port, home jetable $COCKPIT_HOME"
# `cockpit.env` semé comme le pose `install-service` : le réglage de l'instance est une VRAIE
# entrée du périmètre, pas une absence commode.
mkdir -p "$COCKPIT_HOME" && printf 'COCKPIT_SECRET_STORE=file\n' > "$COCKPIT_HOME/cockpit.env"
"$py" -m cockpit serve --host 127.0.0.1 --port "$port" >"$tmp/serve.log" 2>&1 & srv=$!

if ! "$py" - "$port" "$tmp" <<'PY'
import json, sqlite3, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

port, tmp = sys.argv[1], Path(sys.argv[2])
base, home = f"http://127.0.0.1:{port}", tmp / "home"


def call(path, payload=None):
    req = urllib.request.Request(
        base + path, method="POST" if payload else "GET",
        data=json.dumps(payload).encode() if payload else None,
        headers={"content-type": "application/json"} if payload else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read() or b"null")


print("→ [1/5] le daemon répond")
for _ in range(40):
    try:
        if call("/health")[0] == 200:
            break
    except (urllib.error.URLError, ConnectionError, OSError):
        time.sleep(0.5)
else:
    sys.exit("✗ le daemon n'a pas démarré (/health muet après ~20 s)")

print("→ [2/5] un projet est créé PAR L'API — c'est le daemon qui écrit, pas nous")
status, _ = call("/api/projects", {"slug": "atelier-fictif", "name": "atelier-fictif"})
assert status == 201, f"✗ POST /api/projects = {status}"

# …et le coffre est peuplé par le chemin du produit (`onboard link`), pas en écrivant le blob à la main :
# les trois entrées du périmètre existent donc vraiment quand on prend l'instantané.
(tmp / "jeton").write_text("jeton-fictif\n", encoding="utf-8")
subprocess.run([sys.executable, "-m", "cockpit", "onboard", "link", "atelier-fictif",
                "--token-file", str(tmp / "jeton"), "--label", "forge"],
               capture_output=True, text=True, check=True)
assert (home / "secrets" / "store.enc").exists(), "✗ le coffre n'a pas été peuplé par `onboard link`"

print("→ [3/5] état réel du WAL au moment de la prise — CONSTATÉ, pas supposé")
# `daemon/deps.py` ouvre une connexion PAR REQUÊTE et la referme : à la dernière fermeture SQLite
# checkpointe et SUPPRIME le `-wal`. Au repos, le daemon ne laisse donc pas de WAL peuplé — le régime
# dangereux (transaction validée encore dans le `-wal`) n'arrive que connexion TENUE : requête en vol, ou
# `cockpit run` qui garde N connexions-par-thread. On le CONSTATE ici au lieu de le fabriquer ; la perte que
# subirait un `cp` est prouvée au niveau unitaire (`test_prise_a_chaud_emporte_le_valide…`, falsifié).
db = home / "cockpit.db"
wal = db.with_name("cockpit.db-wal")
if wal.exists() and wal.stat().st_size:
    seul = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    dans_le_fichier = [r[0] for r in seul.execute("SELECT slug FROM projects")]
    seul.close()
    manque = "atelier-fictif" not in dans_le_fichier
    print(f"   -wal peuplé ({wal.stat().st_size} o) ; un `cp` du seul .db "
          f"{'AURAIT PERDU le projet' if manque else 'aurait suffi cette fois'}")
else:
    print("   pas de -wal au repos (connexion par requête, refermée → checkpoint). `VACUUM INTO` reste le "
          "bon choix : un fichier AUTONOME, correct dans les deux régimes, sans avoir à savoir qui tient "
          "une connexion à cet instant.")

print("→ [4/5] `cockpit snapshot create` pendant que le daemon SERT (non arrêté)")
out = subprocess.run([sys.executable, "-m", "cockpit", "snapshot", "create"],
                     capture_output=True, text=True, check=True).stdout
dest = Path(out.splitlines()[0].split("→", 1)[1].strip())
manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
assert manifest["schema"] == 1, manifest["schema"]
assert [e["name"] for e in manifest["entries"]] == ["cockpit.db", "cockpit.env",
                                                    "secrets/store.enc"], manifest["entries"]
assert manifest["absent"] == [], f"✗ une entrée du périmètre manquait : {manifest['absent']}"

copie = sqlite3.connect(str(dest / "cockpit.db"))
slugs = [r[0] for r in copie.execute("SELECT slug FROM projects")]
copie.close()
assert slugs == ["atelier-fictif"], f"✗ l'instantané a PERDU le projet validé : {slugs}"
assert not list(dest.glob("cockpit.db-wal")), "✗ l'instantané traîne un -wal : il n'est pas autonome"
assert not list(dest.rglob("master.key")), "✗ la clé maîtresse est partie dans l'instantané"
assert "secrets/master.key" in manifest["excluded"], "✗ l'exclusion n'est pas dite dans le manifeste"
print(f"   ✓ {dest.name} : {[e['name'] for e in manifest['entries']]}, absent={manifest['absent']}")

print("→ [5/5] la prise n'était PAS une écriture : le daemon est intact et sert toujours")
assert call("/health")[0] == 200, "✗ le daemon ne répond plus après la prise"
status, corps = call("/api/projects")
projets = [p["slug"] for p in corps["projects"]]
assert status == 200 and "atelier-fictif" in projets, \
    f"✗ l'instance vivante a changé sous la prise : {projets}"
status, _ = call("/api/projects", {"slug": "apres-prise", "name": "apres-prise"})
assert status == 201, f"✗ la base n'accepte plus d'écriture après la prise : {status}"
PY
then
  echo "   ↳ serve.log (20 dernières lignes) :" >&2; tail -n 20 "$tmp/serve.log" >&2 || true
  exit 1
fi

echo "✓ acceptance instantané-vivant OK — pris à chaud sans rien perdre ni rien perturber."
