#!/usr/bin/env python3
"""ui_shot.py — boucle visuelle d'ITÉRATION de la SPA cockpit (rend l'assistant VOYANT).

Après un changement `web/`, screenshote une ou plusieurs routes et imprime les chemins PNG — que
l'assistant LIT (Read) pour critiquer le rendu, au lieu de coder en aveugle. C'est l'ITÉRATION, pas le
GATE : aucun marqueur, aucun verdict SHA (≠ feature-verified). Adapté du ui_shot legacy mais **sans auth**
(le daemon local n'a pas de session) et en **browser-history** (le fallback SPA sert index.html, donc un
deep-link `/slug` s'ouvre côté serveur — pas de `#`).

Mécanique : build (optionnel) → home cockpit JETABLE seedé de projets démo → `cockpit serve` sert la dist
→ pour chaque route, le runner Playwright `render_check.js` fait goto+screenshot → PNG. Teardown par PID
(jamais `pkill`). Le runner est celui du vault (playwright-core vendoré) ; override par COCKPIT_UI_RUNNER.

Usage :
  python web/tools/ui_shot.py /                       # accueil (rail projets)
  python web/tools/ui_shot.py / /atlas-demo --viewport 1600x1000 --full-page
  python web/tools/ui_shot.py / --viewport 390x844    # largeur mobile
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]             # web/tools/ui_shot.py → repo
WEB = REPO / "web"
DIST = WEB / "dist"
VENV_COCKPIT = REPO / ".venv" / "bin" / "cockpit"
DEFAULT_RUNNER = Path(
    os.environ.get(
        "COCKPIT_UI_RUNNER",
        "/home/avadis/Documents/Vault-V1/.claude/skills/playwright/render_check.js",
    )
)
DEMO_PROJECTS = ["atlas-demo", "nebula-demo"]           # slugs FICTIFS (jamais un vrai basename)
# Outils démo (kind='tool') — rendent la 2ᵉ section « Outils » du rail VOYANTE (miroir des repos framework).
DEMO_TOOLS = ["docsmap-tool", "codemap-tool"]           # slugs FICTIFS
_SLUG = re.compile(r"[^a-z0-9]+")

# Roadmap démo seedée dans le PREMIER projet (atlas-demo) : deps INTRA-feature (le modèle réel), qui
# produisent READY/BLOCKED_DEPS/CYCLE + un NEXT par feature + un fan-out — de quoi juger le layout du
# graphe. nebula-demo reste vide (test de l'état vide). Slugs FICTIFS. (Tasks todo : DONE/ACTIVE sont
# des variantes de couleur couvertes par la légende + les tests unitaires, non seedées ici.)
# (slug, titre FR, deps) — des titres ≠ slug rendent le graphe représentatif (pas de titre défaut = slug).
SEED_ROADMAP: dict[str, list[tuple[str, str, list[str]]]] = {
    "ingest-pipeline": [
        ("schema-contract", "Contrat de schéma", []),
        ("parser-core", "Cœur du parseur", ["schema-contract"]),
        ("stream-reader", "Lecteur de flux", ["parser-core"]),
        ("backpressure", "Contre-pression", ["stream-reader"]),
    ],
    "query-layer": [
        ("index-builder", "Constructeur d’index", []),
        ("fts-adapter", "Adaptateur FTS", ["index-builder"]),
        ("ranking", "Scoring / ranking", ["fts-adapter"]),
    ],
    "web-console": [
        ("auth-shell", "Coquille d’auth", []),
        ("dashboard", "Tableau de bord", ["auth-shell"]),
        ("settings", "Réglages", ["auth-shell"]),
    ],
    "nightly-gc": [
        ("sweep-planner", "Planificateur de balayage", ["sweep-runner"]),
        ("sweep-runner", "Exécuteur de balayage", ["sweep-planner"]),
    ],
}


def _slug(route: str) -> str:
    return _SLUG.sub("-", route.lower()).strip("-") or "root"


def _parse_viewport(s: str | None) -> dict | None:
    if not s:
        return {"width": 1600, "height": 1000}
    m = re.fullmatch(r"(\d+)x(\d+)", s.strip())
    if not m:
        raise SystemExit(f"--viewport attendu WxH (ex. 1600x1000), reçu {s!r}")
    return {"width": int(m.group(1)), "height": int(m.group(2))}


def _parse_clicks(specs: list[str] | None) -> list[dict]:
    """Traduit des `--click` CLI en gestes READ-ONLY pour le runner. Syntaxe : `TEXTE` (clique le 1er
    élément contenant ce texte) ou `TEXTE#N` (le Nᵉ, 0-indexé) pour lever une ambiguïté (texte répété).
    Ces clics n'ouvrent que des surfaces read-only (détail de commit, visionneuse, historique) pilotées par
    un state React sans route — jamais un geste mutant (cf. contrat du runner). Absent → aucun clic."""
    clicks: list[dict] = []
    for spec in specs or []:
        text, sep, tail = spec.rpartition("#")
        if sep and tail.isdigit():
            clicks.append({"text": text, "nth": int(tail)})
        else:
            clicks.append({"text": spec})
    return clicks


def _wait_health(port: int, *, tries: int = 60, delay: float = 0.5) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 — localhost fixe
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(delay)
    return False


def _post(port: int, path: str, body: dict) -> None:
    """POST JSON sur l'API démo (idempotent : un doublon 400 est ignoré)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()      # noqa: S310 — localhost fixe
    except urllib.error.HTTPError as e:
        if e.code != 400:                                  # 400 = doublon → ok
            raise


# Transcript démo (JSONL au format Claude Code) pour rendre l'onglet Dispatch VOYANT : normalize_line le
# transforme en tours assistant (texte + outils) et résultats d'outils. Contenu FICTIF, représentatif.
_DEMO_TRANSCRIPT = [
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "J’analyse le contrat de schéma existant avant d’écrire le parseur."},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "src/ingest/schema.py"}},
        {"type": "tool_use", "name": "Grep", "input": {"pattern": "class .*Contract"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "2 fichiers, 1 classe SchemaContract", "is_error": False}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "J’implémente le contrat et je couvre le cas de la ligne partielle."},
        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/ingest/schema.py"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/test_schema.py -q"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "4 passed in 0.62s", "is_error": False}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Tests verts — le contrat de schéma est prêt à merger."}],
        "usage": {"output_tokens": 1840}}},
]

# Transcript d'un run REVIEWER Tier-1 (kind='review') — rend VOYANT que le reviewer-de-gate laisse une trace
# consultable, même sur un pass 0-finding (« vert sans transcript » = zéro confiance qu'il a vraiment revu).
_DEMO_REVIEW_TRANSCRIPT = [
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Je revois le diff de la feature (correctness, sécurité, conventions)."},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "src/ingest/schema.py"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "diff lu : 1 fichier, +42/-3", "is_error": False}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Aucun finding bloquant. Le contrat couvre la ligne partielle."}],
        "usage": {"output_tokens": 620}}},
]


def _seed(port: int, slugs: list[str], *, home: Path) -> None:
    """Crée les projets démo + une roadmap dans le premier (graphe), et un JOB terminé avec transcript pour
    rendre l'onglet Dispatch (transcript live). Le job est inséré EN DIRECT dans la DB jetable du daemon
    (mêmes settings via l'env COCKPIT_HOME) — aucune API ne fabrique un run sans spawner un vrai `claude`."""
    for slug in slugs:
        _post(port, "/api/projects", {"slug": slug, "name": slug.replace("-", " ").title()})
    for slug in DEMO_TOOLS:            # rail 2 sections : les outils peuplent la section « Outils »
        _post(port, "/api/projects", {"slug": slug, "name": slug.replace("-", " ").title(),
                                      "kind": "tool"})
    proj = slugs[0]
    for feature, tasks in SEED_ROADMAP.items():
        _post(port, f"/api/projects/{proj}/features", {"slug": feature})
        for task, title, deps in tasks:
            _post(port, f"/api/features/{proj}/{feature}/tasks",
                  {"slug": task, "title": title, "depends_on": deps})
    _seed_dispatch_job(home, proj)
    _seed_interview_socle(home, proj)
    _seed_gate_states(home, proj)
    _seed_git_state(home, proj)
    _seed_credential_state(home, slugs)
    _seed_deploy_state(home, proj)


def _seed_dispatch_job(home: Path, proj: str) -> None:
    """Insère 3 runs + leurs transcripts dans la DB du daemon (import local des couches cockpit) pour rendre
    l'historique VOYANT : un run task `done`, un run **reviewer** `review` `done` (badge de genre + transcript
    consultable sur un pass 0-finding), un run task `failed` (pied « raison de l'échec »)."""
    try:
        from cockpit.config import Settings
        from cockpit.db import store
        from cockpit.dispatch import jobs
    except ImportError:
        return  # cockpit non importable (build-only) → on saute le seed du run, sans casser le screenshot
    settings = Settings.resolve(home=home / "home", projects_root=home / "projects")

    def _write(name: str, events: list[dict]) -> str:
        path = home / name
        path.write_text("".join(json.dumps(o) + "\n" for o in events), encoding="utf-8")
        return str(path)

    conn = store.open_db(settings)
    try:
        # 1ʳᵉ task de la 1ʳᵉ feature (ordre list_tasks) — les runs y sont ancrés (le reviewer de même).
        row = conn.execute(
            "SELECT t.id FROM tasks t JOIN features f ON t.feature_id = f.id "
            "JOIN projects p ON f.project_id = p.id WHERE p.slug = ? ORDER BY f.slug, t.slug LIMIT 1",
            (proj,)).fetchone()
        if row is None:
            return
        task_id = row[0]
        done = jobs.record_start(conn, task_id=task_id, worktree=str(home / "wt"), session_id="demo-sess",
                                 log_path=_write("demo-transcript.jsonl", _DEMO_TRANSCRIPT))
        conn.execute("UPDATE dispatch_jobs SET status = 'done', num_turns = 5, cost_usd = 0.1421 "
                     "WHERE id = ?", (done,))
        review = jobs.record_start(conn, task_id=task_id, worktree="(review)", kind="review",
                                   session_id="demo-review",
                                   log_path=_write("demo-review.jsonl", _DEMO_REVIEW_TRANSCRIPT))
        conn.execute("UPDATE dispatch_jobs SET status = 'done', num_turns = 3, cost_usd = 0.0209 "
                     "WHERE id = ?", (review,))
        failed = jobs.record_start(conn, task_id=task_id, worktree=str(home / "wt"), session_id="demo-fail",
                                   log_path=_write("demo-fail.jsonl", _DEMO_TRANSCRIPT))
        conn.execute("UPDATE dispatch_jobs SET status = 'failed', error = ? WHERE id = ?",
                     ("claude -p rc=1 : ToolPreflightError — `ruff` introuvable sur le PATH", failed))
        conn.commit()
    finally:
        conn.close()


def _seed_interview_socle(home: Path, proj: str) -> None:
    """Seed une feature de SOCLE dont la NEXT task est `interactive` (mode v12) → rend VOYANT at-rest le bouton
    « Lancer l'interview » du DispatchPanel (action primaire gated sur la ROADMAP, pas sur une mutation). En
    direct-DB car l'API task-add n'expose pas `mode`. Le voir : `…/atlas-demo/travail?feature=socle-design`."""
    try:
        from cockpit.config import Settings
        from cockpit.db import store
        from cockpit.roadmap import model
    except ImportError:
        return  # build-only → le bouton interview ne sera pas seedé (screenshot non cassé)
    settings = Settings.resolve(home=home / "home", projects_root=home / "projects")
    conn = store.open_db(settings)
    try:
        model.add_feature(conn, project_slug=proj, slug="socle-design",
                          title="Socle — design & décomposition")
        model.add_task(conn, feature_ref=f"{proj}/socle-design", slug="interview",
                       title="Interview de conception (1ʳᵉ session)",
                       acceptance="docs/design.md § Concept renseigné.", mode="interactive")
    finally:
        conn.close()


# Features de démo pour l'onglet Gate — chacune a une VRAIE branche committée sur le SoT bare + un verdict
# Tier-1 ancré, pour rendre la décision composée VOYANTE : (slug, titre, verdict-🔴 ?). `core.py` (≠ UI) →
# Tier-1.5 N/A. La verte (0 🔴, fraîche) donne le HOLD « gate vert sans GO » ; la rouge donne le bloqué.
_GATE_FEATURES = [
    ("gate-green-demo", "Gate vert (prêt à merger)", False),
    ("gate-blocked-demo", "Gate rouge (revue bloquante)", True),
]


def _seed_gate_states(home: Path, proj: str) -> None:
    """Seed des features avec branche committée + verdict Tier-1 pour rendre l'onglet Gate (hold / bloqué).
    Réplique le patron des tests (worktree réservé → commit worker → `review.write_verdict` SHA-bound) en
    direct sur la DB jetable — aucune API ne fabrique une branche sans dispatcher un vrai worker."""
    try:
        from cockpit.config import Settings
        from cockpit.db import store
        from cockpit.dispatch import worktree
        from cockpit.gate import review
        from cockpit.git.identity import resolve_identity
        from cockpit.git.internal import InternalGit
        from cockpit.projects import registry
        from cockpit.roadmap import model
    except ImportError:
        return  # build-only → on saute (le screenshot montrera « aucune branche à merger »)
    settings = Settings.resolve(home=home / "home", projects_root=home / "projects")
    git = InternalGit()
    sot = registry.sot_path_for(settings, proj)
    conn = store.open_db(settings)
    try:
        for slug, title, red in _GATE_FEATURES:
            model.add_feature(conn, project_slug=proj, slug=slug, title=title)
            model.add_task(conn, feature_ref=f"{proj}/{slug}", slug="impl", title="Implémentation")
            conn.execute("UPDATE tasks SET status = 'in_progress' WHERE slug = 'impl' AND feature_id = "
                         "(SELECT f.id FROM features f JOIN projects p ON f.project_id = p.id "
                         "WHERE f.slug = ? AND p.slug = ?)", (slug, proj))
            conn.commit()
            res = worktree.reserve(conn, settings, git, project=proj, feature=slug, probe=None)
            (res["path"] / "core.py").write_text("value = 1\n", encoding="utf-8")
            git.commit_worktree(res["path"], message="feat: work",
                                identity=resolve_identity(proj, "dev", role="worker"))
            head_sha = git.feature_sha(sot, f"feature/{slug}")
            diff_text = git.diff_text(sot, base="dev", head=f"feature/{slug}")
            findings = ([{"severity": "🔴", "file": "core.py", "line": 1,
                          "evidence": "core.py:1 — value = 1"}] if red else [])
            review.write_verdict(settings, proj, slug, {"findings": findings},
                                 sha=head_sha, diff_text=diff_text)
        # + un état review-ABSENTE : branche committée + Tier-0 vert mais AUCUN verdict Tier-1 → rend le
        #   nouveau bloc « Re-lancer la review Tier-1 » du GatePanel (le dead-end « attend review » refermé).
        from cockpit.gate import toolchain
        slug = "gate-review-absent-demo"
        model.add_feature(conn, project_slug=proj, slug=slug, title="Gate — review à produire")
        model.add_task(conn, feature_ref=f"{proj}/{slug}", slug="impl", title="Implémentation")
        conn.execute("UPDATE tasks SET status = 'in_progress' WHERE slug = 'impl' AND feature_id = "
                     "(SELECT f.id FROM features f JOIN projects p ON f.project_id = p.id "
                     "WHERE f.slug = ? AND p.slug = ?)", (slug, proj))
        conn.commit()
        res = worktree.reserve(conn, settings, git, project=proj, feature=slug, probe=None)
        (res["path"] / "core.py").write_text("value = 2\n", encoding="utf-8")
        git.commit_worktree(res["path"], message="feat: work",
                            identity=resolve_identity(proj, "dev", role="worker"))
        head_sha = git.feature_sha(sot, f"feature/{slug}")
        toolchain.write_verdict(settings, proj, slug,
                                [{"group": "backend", "name": "ruff", "cmd": "ruff check .",
                                  "exit_code": 0, "ok": True}], sha=head_sha)   # Tier-0 vert, review absente
    finally:
        conn.close()


def _seed_git_state(home: Path, proj: str) -> None:
    """Avance `dev` d'un cran au-dessus de `main` (ff dev sur une feature déjà committée par le seed Gate),
    pour rendre l'onglet Git VOYANT : la bannière montre « main en retard de N sur dev » (le signal
    main-rattrape-dev), sans toucher main. PUIS câble un **miroir bare LOCAL** en avance sur le SoT (cas
    vécu : travail hors cockpit) → le badge sync miroir affiche `remote_ahead` après un refresh manuel.
    Aucune API ne fait ça — plumbing direct sur le SoT + un « GitHub » jetable local (zéro réseau)."""
    try:
        from cockpit.config import Settings
        from cockpit.core import run
        from cockpit.git.internal import GitOpError, InternalGit, writeback_env
        from cockpit.projects import registry
    except ImportError:
        return  # build-only → vue à 0/0, sans casser le screenshot
    settings = Settings.resolve(home=home / "home", projects_root=home / "projects")
    sot = registry.sot_path_for(settings, proj)
    try:
        InternalGit().merge_ff(sot, into="dev", source="feature/gate-green-demo")  # dev avance, main reste
    except GitOpError:
        return  # feature absente (seed Gate sauté) → vue à 0/0, sans casser le screenshot
    # Miroir divergent LOCAL : cloné du SoT (histoire partagée), câblé comme remote `mirror`, puis SON `dev`
    # avancé de 2 commits → le miroir prend de l'avance sur le SoT (remote_ahead). L'endpoint /git/sync le
    # verra au refresh manuel ; le badge rend « GitHub +2 » (jamais un faux-vert). Zéro réseau.
    env = writeback_env(("Seed", "seed@cockpit.local"))
    mirror = home / "seed-mirror" / f"{proj}.git"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    if not run.run(["git", "clone", "--bare", "-q", str(sot), str(mirror)], env=env).ok:
        return  # clone KO → vue sans badge sync, sans casser le screenshot
    InternalGit().set_remote(sot, "mirror", str(mirror))
    wt = home / "seed-mirror" / f"{proj}-wt"
    if run.run(["git", "-C", str(mirror), "worktree", "add", "-q", str(wt), "dev"], env=env).ok:
        for i in (1, 2):
            (wt / f"hors-cockpit-{i}.txt").write_text("travail hors cockpit\n", encoding="utf-8")
            run.run(["git", "-C", str(wt), "add", "-A"], env=env)
            run.run(["git", "-C", str(wt), "commit", "-q", "-m", f"hors cockpit {i}"], env=env)


def _seed_credential_state(home: Path, slugs: list[str]) -> None:
    """Donne un miroir aux projets démo (→ besoin d'un token) et LIE un credential au premier (voie fichier,
    via le store réel), en laissant le second non lié — pour rendre l'onboarding VOYANT : bandeau « 1 token
    requis », panneau Réglages mixte (✅ lié / 🔴 requis) + carte credential sur la vue projet (onglet Git).
    Plumbing direct sur la DB + le store jetables (aucune API ne fabrique cet état)."""
    try:
        from cockpit.config import Settings
        from cockpit.db import store
        from cockpit.onboarding import link_credential
        from cockpit.projects import registry  # noqa: F401 (garantit le package cockpit importable)
        from cockpit.secrets import build_store
    except ImportError:
        return  # build-only → onboarding « complet » (aucun miroir), sans casser le screenshot
    settings = Settings.resolve(home=home / "home", projects_root=home / "projects")
    conn = store.open_db(settings)
    try:
        for slug in slugs:
            conn.execute("UPDATE projects SET mirror_remote = ? WHERE slug = ?",
                         (f"https://github.com/demo/{slug}.git", slug))
        conn.commit()
        # 1er projet : token lié (voie fichier) → réf en DB, valeur chiffrée dans le store. Le 2ᵉ reste 🔴.
        link_credential(conn, build_store(settings), slugs[0], token="ghp_demo_token", label="github")
    finally:
        conn.close()


def _seed_deploy_state(home: Path, proj: str) -> None:
    """Pose le déploiement `dev` en `running` (+ port/url/sha) et laisse `main` en `no_deploy`, pour rendre
    l'onglet Runtime VOYANT : une carte « en marche » avec **lien health-gated actif** + une carte « jamais
    déployé » avec lien inerte. Plumbing direct sur la DB jetable — aucun conteneur réel (c'est l'ITÉRATION
    visuelle, pas le smoke ; la santé live se réconcilierait à `stopped` sans conteneur, ce qu'on ne fait pas
    ici pour figer l'état running visible)."""
    try:
        from cockpit.config import Settings
        from cockpit.db import store
        from cockpit.projects import deployments, registry
    except ImportError:
        return  # build-only → 2 cartes `no_deploy`, sans casser le screenshot
    settings = Settings.resolve(home=home / "home", projects_root=home / "projects")
    conn = store.open_db(settings)
    try:
        pid = registry.get_project(conn, proj)["id"]
        deployments.ensure_deployments(conn, pid)
        deployments.set_deployment(conn, pid, "dev", status="running", port=5250,
                                   url="http://127.0.0.1:5250", last_deploy_sha="cafe1234deadbeef")
    finally:
        conn.close()


def shoot(routes: list[str], *, port: int, viewport: dict | None, full_page: bool, out_dir: Path,
          runner: Path, timeout_ms: int, seed: bool, wait_text: str | None = None,
          clicks: list[dict] | None = None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="cockpit-uishot-"))
    env = {**os.environ, "COCKPIT_HOME": str(home / "home"),
           "COCKPIT_PROJECTS_ROOT": str(home / "projects")}
    proc = subprocess.Popen(
        [str(VENV_COCKPIT), "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    results: list[dict] = []
    try:
        if not _wait_health(port):
            tail = proc.stdout.read()[-1500:] if proc.stdout else ""
            raise SystemExit(f"le daemon n'a pas démarré sur :{port}\n{tail}")
        if seed:
            _seed(port, DEMO_PROJECTS, home=home)
        for route in routes:
            path = route if route.startswith("/") else "/" + route
            url = f"http://127.0.0.1:{port}{path}"          # browser-history (fallback SPA côté serveur)
            png = out_dir / f"{_slug(path)}.png"
            payload: dict = {"url": url, "markers": [], "screenshot": str(png),
                             "timeout_ms": timeout_ms, "full_page": full_page}
            if viewport:
                payload["viewport"] = viewport
            if wait_text:                                   # surface WS (terminal) : attendre le repère
                payload["wait_for_text"] = wait_text
            if clicks:                                       # gestes read-only (ouvrir une UI sans route)
                payload["clicks"] = clicks
            try:
                p = subprocess.run(["node", str(runner), json.dumps(payload)],
                                   capture_output=True, text=True, timeout=timeout_ms / 1000 + 20)
                data = json.loads(p.stdout or "{}")
                results.append({"route": route, "url": url, "png": str(png),
                                "ok": bool(data.get("screenshot")) and png.exists(),
                                "title": data.get("title"), "clicks": data.get("clicks"),
                                "error": data.get("error") or (p.stderr or None)})
            except (subprocess.TimeoutExpired, OSError, ValueError) as e:  # noqa: BLE001
                results.append({"route": route, "url": url, "png": str(png), "ok": False,
                                "error": f"{type(e).__name__}: {e}"})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("routes", nargs="+", help="routes SPA (ex. '/' ou '/atlas-demo')")
    ap.add_argument("--port", type=int, default=8799, help="port du daemon jetable (défaut 8799)")
    ap.add_argument("--viewport", help="taille WxH (défaut 1600x1000 ; mobile 390x844)")
    ap.add_argument("--full-page", action="store_true", help="capture toute la hauteur scrollable")
    ap.add_argument("--out", default=str(WEB / ".ui-shots"), help="dossier de sortie des PNG")
    ap.add_argument("--runner", default=str(DEFAULT_RUNNER), help="render_check.js (playwright)")
    ap.add_argument("--no-seed", action="store_true", help="ne pas créer de projets démo")
    ap.add_argument("--build", action="store_true", help="npm run build avant de servir")
    ap.add_argument("--wait-text", help="attendre ce texte dans le DOM avant capture (surface WS : terminal)")
    ap.add_argument("--click", action="append", metavar="TEXTE[#N]",
                    help="geste READ-ONLY avant capture : clique le texte (ou son Nᵉ) pour ouvrir une UI "
                         "derrière un state React (répétable, séquentiel). Jamais un geste mutant.")
    ap.add_argument("--timeout-ms", type=int, default=15000)
    a = ap.parse_args(argv)

    if a.build:
        subprocess.run(["npm", "run", "build"], cwd=WEB, check=True)
    if not (DIST / "index.html").exists():
        raise SystemExit(f"build absent : {DIST}/index.html — lance avec --build ou `npm run build`")
    if not Path(a.runner).exists():
        raise SystemExit(f"runner playwright introuvable : {a.runner} (override COCKPIT_UI_RUNNER)")

    results = shoot(a.routes, port=a.port, viewport=_parse_viewport(a.viewport),
                    full_page=a.full_page, out_dir=Path(a.out), runner=Path(a.runner),
                    timeout_ms=a.timeout_ms, seed=not a.no_seed, wait_text=a.wait_text,
                    clicks=_parse_clicks(a.click))
    for r in results:
        flag = "✓" if r["ok"] else "✗"
        print(f"{flag} {r['route']:24} → {r['png']}" + (f"   [{r['error']}]" if r.get("error") else ""))
        for c in r.get("clicks") or []:                     # trace des gestes read-only (clic manqué = ✗)
            print(f"    {'·' if c.get('ok') else '✗'} click {c.get('step')!r}"
                  + (f"   [{c.get('error')}]" if c.get("error") else ""))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
