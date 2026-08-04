#!/usr/bin/env python3
"""e2e_runtime.py — harnais d'acceptance e2e du moteur runtime (P6, clôt l'épic runtime-hosting).

Prouve le **critère binaire** de l'épic en RÉEL (podman/docker-rootless — aucun seed DB, aucun FakeBackend),
via la **vraie surface HTTP** du daemon (les routes que l'UI appelle) :

  Étage 1 — Déploiement réel : un projet se déploie sur `main` ET `dev` → 2 conteneurs, 2 ports distincts,
            `GET .../status` live = `running`, `GET .../logs` = lignes réelles, chaque `url` répond **200**.
  Étage 2 — Deux projets simultanés : un 2ᵉ projet tourne en même temps → 4 compose-projects, 4 ports
            disjoints, les 4 répondent, **aucun conflit** ; l'API liste les 2 projets sainement.
  Étage 3 — Non-pollution (rejeu e2e de P4) : le conteneur de A ne voit ni le **secret du daemon** ni le
            **fichier privé** de B ; le résolveur de secrets de A refuse le `credential_ref` de B (ACL).
  Étage 4 — Feature-verified (lié au SHA de HEAD) : l'onglet Runtime rend ses **marqueurs FR** dans le DOM
            (issus d'un VRAI deploy, pas d'un seed) + **screenshot** ; verdict écrit, ancré au HEAD
            forgemaster.

**Rejouable** : `FORGEMASTER_HOME` jetable, teardown en `finally` (down des 4 déploiements + rm du home +
arrêt du
daemon ; aucun conteneur/home résiduel). **Hors gate natif** (podman requis, lent) — comme les smokes P2-P5.

Prérequis : lancer sous le venv du repo (`.venv/bin/python scripts/e2e_runtime.py`), `podman` (ou `docker`)
rootless dispo, `web/dist` buildé (`forgemaster setup` / `npm run build`), et `node` (nvm 22) dans le PATH
pour le
runner Playwright de l'étage 4. Le runner est celui du vault (override `FORGEMASTER_UI_RUNNER`).

Usage : .venv/bin/python scripts/e2e_runtime.py [--serve-port 8788] [--fv-out DIR] [--keep]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV_FORGEMASTER = REPO / ".venv" / "bin" / "forgemaster"
WEB_DIST = REPO / "web" / "dist"
DEFAULT_RUNNER = Path(os.environ.get(
    "FORGEMASTER_UI_RUNNER",
    "/home/avadis/Documents/Vault-V1/.claude/skills/playwright/render_check.js",
))
# Moteur de conteneurs = 1er token de la CLI compose (podman|docker) — pour les asserts `ps`/`exec` DIRECTS.
ENGINE = os.environ.get("FORGEMASTER_COMPOSE_CMD", "podman compose").split()[0]

PROJ_A = "demo-a"                                          # slugs FICTIFS (jamais un vrai basename)
PROJ_B = "demo-b"
# Secret du daemon qui NE DOIT PAS atteindre le conteneur (rejeu P4 : allowlist d'env deny-by-default).
SENTINEL_ENV = "E2E_DAEMON_SECRET"
SENTINEL_VAL = f"leak-canary-{uuid.uuid4().hex[:12]}"
# Fichier privé commité sur le SoT de B → présent dans SON conteneur, invisible depuis A (frontière de build).
PRIVATE_B = f"private-b-{uuid.uuid4().hex[:8]}.txt"
PRIVATE_B_BODY = "contenu privé de demo-b — jamais visible depuis demo-a\n"


class Fail(Exception):
    """Un assert e2e qui casse — remonte en sortie 1 (fail-loud, jamais un faux-vert)."""


_checks: list[str] = []


def check(cond: bool, msg: str) -> None:
    """Assert lisible : ✓ accumulé, ✗ fatal (`Fail`). Le harnais est fail-loud — un e2e à moitié vert ment."""
    mark = "✓" if cond else "✗"
    print(f"  {mark} {msg}", flush=True)
    _checks.append(f"{mark} {msg}")
    if not cond:
        raise Fail(msg)


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


# -- surface HTTP du daemon (les routes que l'UI appelle) -------------------------------------------------
def http(method: str, url: str, *, timeout: float = 30, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — localhost fixe
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def get_json(url: str, *, timeout: float = 30) -> dict:
    status, body = http("GET", url, timeout=timeout)
    if status != 200:
        raise Fail(f"GET {url} → {status}: {body[:200]}")
    return json.loads(body)


# -- moteur de conteneurs DIRECT (asserts d'isolation par label compose) ----------------------------------
def containers(project_name: str) -> list[dict]:
    """Conteneurs d'un compose-project via le label `com.docker.compose.project` (posé par podman/docker)."""
    r = _run([ENGINE, "ps", "-a", "--format", "json",
              "--filter", f"label=com.docker.compose.project={project_name}"])
    text = r.stdout.strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:                          # NDJSON (podman)
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def cid_of(project_name: str) -> str:
    cs = containers(project_name)
    if not cs:
        raise Fail(f"aucun conteneur pour {project_name}")
    c = cs[0]
    return str(c.get("Id") or c.get("ID") or (c.get("Names") or [""])[0])


def exec_in(cid: str, *cmd: str) -> subprocess.CompletedProcess:
    return _run([ENGINE, "exec", cid, *cmd])


# -- daemon jetable ---------------------------------------------------------------------------------------
def wait_health(port: int, *, tries: int = 80, delay: float = 0.5) -> bool:
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:  # noqa: S310
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(delay)
    return False


def deploy_up(base: str, project: str, branch: str) -> dict:
    """POST .../up = build + `compose up -d` synchrone (le 1er tire l'image → long) → running."""
    status, body = http("POST", f"{base}/api/projects/{project}/deployments/{branch}/up",
                        body={}, timeout=900)
    if status != 200:
        raise Fail(f"deploy up {project}/{branch} → {status}: {body[:300]}")
    return json.loads(body)["deployment"]


def deploy_down(base: str, project: str, branch: str) -> None:
    http("POST", f"{base}/api/projects/{project}/deployments/{branch}/down", body={}, timeout=120)


# -- non-pollution : commit d'un fichier privé sur le SoT bare de B ---------------------------------------
def commit_private_file(projects_root: Path, slug: str) -> None:
    """Commit `PRIVATE_B` sur `dev` du SoT **bare** de B (worktree jetable) → entre dans SON contexte de build
    (`git archive` du deploy), donc dans SON conteneur — jamais dans celui de A. Rejeu e2e du smoke P4."""
    sot = projects_root / slug / "sot.git"
    if not sot.exists():                                  # layout alternatif éventuel
        sot = projects_root / slug / "sot"
    ident = ["-c", "user.name=e2e", "-c", "user.email=e2e@forgemaster.local"]
    wt = projects_root / slug / "_e2e_wt"
    r = _run(["git", "-C", str(sot), *ident, "worktree", "add", "-q", str(wt), "dev"])
    if r.returncode != 0:
        raise Fail(f"worktree add sur {sot}: {r.stderr[:200]}")
    (wt / PRIVATE_B).write_text(PRIVATE_B_BODY, encoding="utf-8")
    _run(["git", "-C", str(wt), *ident, "add", "-A"])
    _run(["git", "-C", str(wt), *ident, "commit", "-q", "-m", "e2e: fichier privé de B"])
    _run(["git", "-C", str(sot), "worktree", "remove", "--force", str(wt)])


# -- étage 4 : feature-verified (marqueurs FR rendus, SHA-bound) ------------------------------------------
def head_sha() -> str:
    return _run(["git", "-C", str(REPO), "rev-parse", "HEAD"]).stdout.strip()


def feature_verify(base_port: int, project: str, runner: Path, out_dir: Path) -> dict:
    """Runner Playwright goto-only sur `/{project}/runtime` → asserte les marqueurs FR rendus + screenshot ;
    écrit un verdict **ancré au HEAD forgemaster** (frais ssi `reviewed_sha == HEAD`). Limite honnête assumée
    :
    la santé LIVE (RefreshButton → GET .../status) n'est pas cliquée (runner goto-only) ; le screenshot
    capture l'état **DB persisté** issu d'un VRAI deploy (donc genuine), et le reconcile live est couvert à
    part par l'étage 1 (l'API `status` renvoie `running` depuis un `ps` réel). Les 2 moitiés se couvrent."""
    sha = head_sha()
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"runtime-{project}-{sha[:8]}.png"
    url = f"http://127.0.0.1:{base_port}/{project}/runtime"   # browser-history (fallback SPA côté serveur)
    markers = ["Déploiements", "en marche", "ouvrir dev ↗", "ouvrir main ↗"]
    payload = {"url": url, "markers": markers, "screenshot": str(png),
               "timeout_ms": 20000, "viewport": {"width": 1400, "height": 1000}}
    p = _run(["node", str(runner), json.dumps(payload)], timeout=60)
    data = json.loads(p.stdout or "{}")
    verdict = {"reviewed_sha": sha, "project": project, "url": url,
               "ok": bool(data.get("ok")) and png.exists(),
               "found": data.get("found", []), "missing": data.get("missing", []),
               "screenshot": str(png), "title": data.get("title"),
               "error": data.get("error") or (p.stderr[:300] or None)}
    (out_dir / f"runtime-e2e-verify-{sha[:8]}.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


# -- orchestration --------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--serve-port", type=int, default=8788, help="port du daemon jetable")
    ap.add_argument("--fv-out", help="dossier de sortie feature-verified (défaut: tempdir imprimé)")
    ap.add_argument("--runner", default=str(DEFAULT_RUNNER), help="render_check.js (playwright)")
    ap.add_argument("--keep", action="store_true", help="ne pas supprimer le home jetable (debug)")
    a = ap.parse_args(argv)

    if not (WEB_DIST / "index.html").exists():
        raise SystemExit(f"web/dist absent : {WEB_DIST}/index.html — build (forgemaster setup / npm run "
        f"build)")
    if not Path(a.runner).exists():
        raise SystemExit(f"runner playwright introuvable : {a.runner} (override FORGEMASTER_UI_RUNNER)")

    home = Path(tempfile.mkdtemp(prefix="forgemaster-e2e-"))
    fv_out = Path(a.fv_out) if a.fv_out else Path(tempfile.mkdtemp(prefix="forgemaster-e2e-fv-"))
    projects_root = home / "projects"
    base = f"http://127.0.0.1:{a.serve_port}"
    # Env du daemon : porte le SECRET SENTINELLE (DOIT être filtré par l'allowlist P4 avant tout conteneur).
    env = {**os.environ, "FORGEMASTER_HOME": str(home / "home"),
           "FORGEMASTER_PROJECTS_ROOT": str(projects_root),
           "FORGEMASTER_WEB_DIST": str(WEB_DIST), SENTINEL_ENV: SENTINEL_VAL}
    deployed: list[tuple[str, str]] = []
    proc: subprocess.Popen | None = None
    home.mkdir(parents=True, exist_ok=True)
    daemon_log = home / "daemon.log"                       # sortie du daemon → fichier (tail NON bloquant)

    def daemon_tail(n: int = 1500) -> str:
        return daemon_log.read_text(errors="replace")[-n:] if daemon_log.exists() else ""

    try:
        with daemon_log.open("w") as logf:
            proc = subprocess.Popen(
                [str(VENV_FORGEMASTER), "serve", "--host", "127.0.0.1", "--port", str(a.serve_port)],
                env=env, stdout=logf, stderr=subprocess.STDOUT, text=True)
        if not wait_health(a.serve_port):
            raise Fail(f"le daemon n'a pas démarré sur :{a.serve_port}\n{daemon_tail()}")

        # projets créés en RÉEL (bundle service-api semé : compose+Dockerfile+stub runnable) via l'API.
        for slug in (PROJ_A, PROJ_B):
            st, bd = http("POST", f"{base}/api/projects",
                          body={"slug": slug, "name": slug.title(), "project_type": "service-api"})
            check(st in (200, 201), f"projet {slug} créé (service-api) [{st}]")
        # Fichier privé de B commité sur SON dev AVANT tout deploy → entre dans son 1er build (donc son
        # conteneur), jamais dans celui de A. Fait ici (pré-deploy) car podman-compose ne RECRÉE pas un
        # conteneur au re-`up` sur simple changement de contexte — on prouve l'isolation, pas le rebuild.
        commit_private_file(projects_root, PROJ_B)

        # ---- Étage 1 : déploiement réel (main + dev) -----------------------------------------------------
        print("\n[Étage 1] déploiement réel — main + dev", flush=True)
        for branch in ("main", "dev"):
            dep = deploy_up(base, PROJ_A, branch)
            deployed.append((PROJ_A, branch))
            check(dep["status"] == "running", f"{PROJ_A}/{branch} deploy → running")
        for branch in ("main", "dev"):
            live = get_json(f"{base}/api/projects/{PROJ_A}/deployments/{branch}/status")["deployment"]
            check(live["status"] == "running", f"{PROJ_A}/{branch} status LIVE (ps réel) = running")
            code, _ = http("GET", live["url"], timeout=10)
            check(code == 200, f"{PROJ_A}/{branch} service répond 200 ({live['url']})")
        a_main = get_json(f"{base}/api/projects/{PROJ_A}/deployments/main/status")["deployment"]
        a_dev = get_json(f"{base}/api/projects/{PROJ_A}/deployments/dev/status")["deployment"]
        check(a_main["port"] != a_dev["port"], f"2 ports distincts (main={a_main['port']} dev={a_dev['port']})")  # noqa: E501
        check(len(containers("forgemaster-demo-a-main")) == 1
              and len(containers("forgemaster-demo-a-dev")) == 1, "2 compose-projects isolés (1 "
              "conteneur/br)")
        logs = get_json(f"{base}/api/projects/{PROJ_A}/deployments/dev/logs?tail=100")["lines"]
        check(len(logs) >= 1, f"logs réels (dev) — {len(logs)} ligne(s) après trafic")

        # ---- Étage 2 : deux projets simultanés -----------------------------------------------------------
        print("\n[Étage 2] deux projets simultanés", flush=True)
        for branch in ("main", "dev"):
            dep = deploy_up(base, PROJ_B, branch)
            deployed.append((PROJ_B, branch))
            check(dep["status"] == "running", f"{PROJ_B}/{branch} deploy → running (pendant que {PROJ_A} tourne)")  # noqa: E501
        names = [f"forgemaster-{PROJ_A}-main", f"forgemaster-{PROJ_A}-dev",
                 f"forgemaster-{PROJ_B}-main", f"forgemaster-{PROJ_B}-dev"]
        check(all(len(containers(n)) == 1 for n in names), "4 compose-projects vivants simultanément")
        ports = []
        for slug in (PROJ_A, PROJ_B):
            for branch in ("main", "dev"):
                d = get_json(f"{base}/api/projects/{slug}/deployments/{branch}/status")["deployment"]
                ports.append(d["port"])
                code, _ = http("GET", d["url"], timeout=10)
                check(code == 200, f"{slug}/{branch} répond 200 (co-résident)")
        check(len(set(ports)) == 4, f"4 ports disjoints, aucun conflit : {sorted(ports)}")

        # ---- Étage 3 : non-pollution prouvée (rejeu e2e de P4) -------------------------------------------
        print("\n[Étage 3] non-pollution (rejeu e2e P4)", flush=True)
        # son 1er build a embarqué le fichier privé (supra)
        b_dev_cid = cid_of("forgemaster-demo-b-dev")
        a_dev_cid = cid_of("forgemaster-demo-a-dev")
        r = exec_in(b_dev_cid, "cat", f"/app/{PRIVATE_B}")
        check(r.returncode == 0 and PRIVATE_B_BODY.strip() in r.stdout,
              f"le conteneur de B contient bien SON fichier privé (/app/{PRIVATE_B})")
        r = exec_in(a_dev_cid, "ls", "/app")
        check(PRIVATE_B not in r.stdout, "le conteneur de A ne voit PAS le fichier privé de B (frontière FS)")
        r = exec_in(a_dev_cid, "env")
        check(SENTINEL_VAL not in r.stdout and SENTINEL_ENV not in r.stdout,
              "le conteneur de A ne voit PAS le secret du daemon (allowlist d'env P4)")
        # ACL control-plane : le résolveur de secrets de A refuse le credential_ref de B (P4, in-process).
        sys.path.insert(0, str(REPO / "src"))
        from forgemaster.config import Settings
        from forgemaster.db import store
        from forgemaster.projects import registry
        from forgemaster.secrets import scoped_cred_resolver
        settings = Settings.resolve(home=home / "home", projects_root=projects_root)
        conn = store.open_db(settings)
        try:
            ref_b = f"ref-of-b-{uuid.uuid4().hex[:8]}"
            registry.set_credential_ref(conn, PROJ_B, ref_b)
            resolve_a = scoped_cred_resolver(settings, conn, slug=PROJ_A)
            check(resolve_a(ref_b) == "", "le résolveur de secrets de A refuse le credential_ref de B (ACL)")
        finally:
            conn.close()

        # ---- Étage 4 : feature-verified (marqueurs FR rendus, SHA-bound) ---------------------------------
        print("\n[Étage 4] feature-verified — onglet Runtime (SHA-bound)", flush=True)
        v = feature_verify(a.serve_port, PROJ_A, Path(a.runner), fv_out)
        check(v["ok"], f"marqueurs FR rendus dans le DOM : {v['found']}"
              + (f" — MANQUANTS {v['missing']} / {v.get('error')}" if not v["ok"] else ""))
        check(Path(v["screenshot"]).exists(), f"screenshot écrit : {v['screenshot']}")
        print(f"  · verdict ancré au HEAD {v['reviewed_sha'][:8]} → {fv_out}", flush=True)

        print(f"\n✅ e2e RUNTIME VERT — {len([c for c in _checks if c.startswith('✓')])} asserts", flush=True)
        print(f"   feature-verified : {fv_out}", flush=True)
        return 0
    except Fail as e:
        print(f"\n❌ e2e ÉCHEC : {e}", flush=True)
        tail = daemon_tail(1200)
        if tail:
            print("--- daemon (tail) ---\n" + tail, flush=True)
        return 1
    finally:
        for slug, branch in deployed:                     # teardown idempotent (aucun conteneur résiduel)
            with contextlib.suppress(Exception):          # best-effort cleanup
                deploy_down(base, slug, branch)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not a.keep:
            shutil.rmtree(home, ignore_errors=True)
        else:
            print(f"[--keep] home conservé : {home}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
