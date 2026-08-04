"""woaw dispatch — dispatche le **juge esthétique** (`site-vitrine-woaw-critic`) sur le RENDU d'une feature
et écrit un verdict woaw **advisory** SHA-bound (`gate/woaw`). Symétrique de `dispatch/reviewer` (Tier-1)
mais côté esthétique : le worker GÉNÈRE, ce juge NOTE — read-only (`work=False`), il ne code jamais.

Différence clé avec le reviewer : le reviewer juge un **diff** (texte) ; le juge woaw juge un **pixel** — il
lui faut un **screenshot at-rest** de la route rendue. Le dispatch preview-déploie donc le worktree (comme
`gate/verify.autoverify_feature`), capture le screenshot via le runner Node (Playwright), démonte, puis
dispatche le juge headless qui **lit l'image** (`Read`) et rend ses findings classés P1–P7.

**Advisory (doctrine §4 « advisory d'abord »)** : le verdict ne bloque JAMAIS le merge (`gate/woaw.status`
n'expose pas de `blocking`). `compose_merge_decision` le surface en reason consultative. Promotion en
bloquant-overridable = choix futur, quand le taux de flake du juge est mesuré bas.

**v1 sans journalisation `dispatch_jobs`** : la table a un `kind` sous CHECK fermé (`task|review|toolchain|
fix`) — un `kind='woaw'` honnête exigerait un bump d'enum schéma → même follow-up que la surface cloche
(`cockpit-woaw-alert-surface`). v1 lance donc le juge via `core.run.run` direct (transcript non journalisé) ;
l'observabilité DB viendra avec le bump. Le transport (`runner`/`deployer`) reste **injectable** → les tests
ne spawnent jamais un vrai `claude` ni un vrai conteneur.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Callable
from pathlib import Path

from forgemaster import auth
from forgemaster.config import Settings
from forgemaster.core import ids, run
from forgemaster.dispatch import reviewer, worker
from forgemaster.dispatch import worktree as worktree_mod
from forgemaster.gate import verify, woaw
from forgemaster.git.internal import GitOpError, InternalGit
from forgemaster.projects.registry import sot_path_for
from forgemaster.roadmap import model
from forgemaster.tools import ToolPreflightError, preflight_tools, tools_env

WOAW_BASE = "dev"
WOAW_TIMEOUT = 600.0                 # s ; le juge lit UNE image + raisonne — plus court qu'un run de travail
WOAW_TOOLS = "Read,Grep,Glob"        # read-only : `Read` ouvre le PNG ; aucun Bash mutant (0 hang headless)
SHOT_TIMEOUT_MS = 20000

# runner(payload_json) -> RunResult (screenshot Node) ; deployer/teardowner = preview-deploy injectables.
Runner = Callable[..., run.RunResult]


# -- rubrique du juge (distillée de docs/site-vitrine-woaw-language.md — AUTO-CONTENUE : la doctrine n'est
#    PAS seedée dans les projets, le juge tourne dans le worktree du projet) --------------------------------

def build_woaw_prompt(screenshot_path: str, feature: dict, route: str) -> str:
    """Compose le prompt du juge woaw : rubrique P1–P7 EMBARQUÉE (la doctrine forgemaster n'est pas dans le
    worktree du projet), le screenshot at-rest à LIRE, et le contrat de sortie JSON. Le juge ne note QUE le
    rendu réel (jamais du code inféré). PUR."""
    mandate = (
        "Tu es le **juge esthétique woaw** dispatché en headless (aucun interlocuteur). Tu NOTES le RENDU "
        "d'une vitrine, tu ne modifies RIEN (lecture seule). **LIS d'abord le screenshot at-rest** avec "
        f"l'outil `Read` : `{screenshot_path}` (route `{route}`). N'appelle aucun autre outil mutant "
        "(pas de `Write`/`Edit`/`Bash`) : en headless personne ne l'approuve → tu resterais bloqué.\n\n"
        "Juge UNIQUEMENT ce qui est PEINT à l'écran (jamais du code que tu inférerais). Une vitrine "
        "« correcte » qui ne fait pas *woaw* est un échec d'objectif : **refuse le plat**. Note le rendu "
        "contre les **7 principes woaw** — pour chacun, un signal MESURABLE au pixel :\n"
        "- **P1 · Matière, pas aplat** — ≥1 surface focale porte une matière (texture / dégradé multi-stop), "
        "pas un aplat de teinte unie.\n"
        "- **P2 · Tissu, pas cartes** — ≥2 registres de surface (élevé / creusé) ; PAS de vue dont >60 % des "
        "blocs sont des cartes bordées iso-morphes. Un « creux » qui n'est qu'un fond teinté (sans ombre "
        "insérée perceptible) ne compte pas comme un 2ᵉ registre.\n"
        "- **P3 · Drame du héro** — ratio d'échelle titre/corps ≥ ~2.5× ; un point focal NON-textuel ; "
        "respiration généreuse.\n"
        "- **P4 · Densité d'ornement** — ≥1 ornement par section porteuse, même famille réutilisée.\n"
        "- **P5 · Voix typographique** — ≥2 rôles typo (display vs corps) ; le wordmark est un TRAITEMENT "
        "(image/SVG/police traitée), pas un `<span>` nu.\n"
        "- **P6 · Profondeur & relief** — ≥2 plans z perceptibles ; ombres/halos NON nuls sur les surfaces "
        "élevées ; pas de vue 100 % plate. Un « creux » sans ombre insérée ne crée PAS de plan z.\n"
        "- **P7 · Mouvement retenu** — non jugeable sur un screenshot → marque-le `angle non couvert`, "
        "n'invente pas de finding dessus.\n\n"
        "**Seuil de plat (§4.1)** : le rendu est PLAT (`flat: true`) s'il cumule mur de texte + cartes "
        "iso-bordées avec **zéro matière (P1) ET zéro relief (P6)**. Un rendu au-dessus du seuil "
        "(ornements + ≥2 registres réels) n'est PAS plat même s'il garde du plat résiduel.\n\n"
        "Sévérité : **🔴** = principe en échec DUR contribuant au seuil de plat (P1/P6 à zéro sur une vue "
        "porteuse) ; **🟡** = manque net non-bloquant (cartes iso, wordmark nu, ornement absent) ; **🟣** = "
        "raffinement / choix de rythme. Pour CHAQUE finding, tente de le **réfuter** en re-regardant "
        "l'image ; s'il ne survit pas, jette-le.\n\n"
        "Rends **UNIQUEMENT** un objet JSON final (dernier bloc), sans autre texte après :\n"
        "```json\n"
        f"{{\"route\": \"{route}\", \"flat\": false, \"findings\": [\n"
        "  {\"severity\": \"🔴\", \"principle\": \"P6\",\n"
        "   \"claim\": \"ce qui est plat/manquant, en une phrase\",\n"
        "   \"evidence\": \"ce qui est RENDU à l'écran qui le prouve (jamais du code)\",\n"
        "   \"fix\": \"le geste capital qui lèverait ça (token relief, primitive, hero…)\"}\n"
        "]}\n"
        "```\n"
        "Rendu conforme (aucun aplat bloquant) → `\"flat\": false, \"findings\": []` (ou juste des 🟡/🟣)."
    )
    branch = feature.get("branch", "")
    header = f"# Juge woaw — feature `{feature['slug']}` (branche `{branch}`, route `{route}`)"
    return "\n\n".join([header, mandate]) + "\n"


def _extract_payload(result_text: str | None) -> dict:
    """Extrait l'objet verdict `{route?, flat?, findings[]}` de la sortie du juge (dernier objet JSON portant
    `findings`). Tolérant au préambule (le juge raisonne avant le bloc final) et aux fences ```json. Vide
    (`{"findings": []}`) si rien d'exploitable — un juge muet ⇒ verdict conforme (l'axe est advisory)."""
    if not result_text:
        return {"findings": []}
    text = result_text.strip()
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for i in reversed(starts):
        candidate = text[i:]
        for end in (len(candidate), candidate.rfind("```")):
            if end <= 0:
                continue
            try:
                obj = json.loads(candidate[:end])
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
                return obj
    return {"findings": []}


# -- capture du screenshot at-rest (preview-deploy → runner Node → teardown) --------------------------------

def _default_shot_runner(settings: Settings) -> Runner:
    """Runner Node par défaut : exécute `render_check.js` avec un payload screenshot (full-page at-rest). Ne
    lève jamais ici (l'appelant gère l'échec best-effort). Injecté en test → jamais de vrai Playwright."""
    def _run(payload: dict) -> run.RunResult:
        runner = verify.runner_path(settings)
        return run.run(["node", str(runner), json.dumps(payload)],
                       timeout=SHOT_TIMEOUT_MS / 1000 + 30, env=tools_env(settings))
    return _run


def capture_route_screenshot(conn, settings: Settings, *, project: str, feature: str,
                             out_path: Path, deployer=None, teardowner=None,
                             shot_runner: Runner | None = None) -> str | None:
    """Preview-déploie le worktree de la feature, capture un screenshot **full-page at-rest** de la route
    déclarée (`verify-markers.json:path`, défaut `/`) via le runner Node, démonte TOUJOURS (finally).
    Best-effort : runner absent / deploy KO / image non écrite → `None` (pas de faux screenshot). Retourne la
    route sondée (pour l'ancrer au verdict) ou None. `deployer`/`teardowner`/`shot_runner` injectables (tests
    ne montent aucun conteneur)."""
    from forgemaster.runtime.engine import deploy_preview, teardown_preview
    deploy = deployer or deploy_preview
    teardown = teardowner or teardown_preview
    runner = shot_runner or _default_shot_runner(settings)
    if not verify.runner_path(settings).is_file() and shot_runner is None:
        return None                                            # runner Node absent → pas de capture (honnête)
    try:
        preview = deploy(conn, settings, slug=project, feature=feature)
    except ValueError:
        return None                                            # type non hébergeable → pas de preuve visuelle
    try:
        verify._wait_http_ready(preview["url"])
        contract = verify.read_verify_contract(Path(preview["workdir"]))
        route = contract["path"]
        target_url = preview["url"].rstrip("/") + route
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"url": target_url, "markers": [], "screenshot": str(out_path),
                   "full_page": True, "timeout_ms": SHOT_TIMEOUT_MS}
        runner(payload)
        return route if out_path.is_file() else None
    except (OSError, run.RunTimeout):
        return None
    finally:
        teardown(conn, settings, slug=project, feature=feature)


# -- dispatch du juge (mirror reviewer.dispatch_reviewer, advisory) -----------------------------------------

def dispatch_woaw(conn, settings: Settings, *, feature_ref: str, git: InternalGit | None = None,
                  runner: Runner | None = None, deployer=None, teardowner=None,
                  shot_runner: Runner | None = None) -> dict:
    """Dispatche le juge woaw sur `feature_ref` (`"projet/feature"`) **si le travail est complet ET touche une
    surface UI** + écrit un verdict advisory SHA-bound. Retourne `{judged: bool, reason, verdict?, counts?}`.
    **Readiness-gate** (hold si tasks inachevées / feature jamais dispatchée) ; **N/A hors UI** (rien de
    rendu à juger) ; **idempotent** (verdict woaw déjà frais → skip) ; **best-effort** (capture ou juge
    échoué → pas de verdict, jamais un blocage : l'axe est advisory). `runner` injecté = juge ; les autres
    injectables couvrent la capture."""
    git = git or InternalGit()
    project, feature = feature_ref.split("/", 1)
    feat = model.resolve_feature(conn, feature_ref)            # KeyError/ValueError si absent

    ok, reason = reviewer._readiness(conn, feat["id"])         # même autorité de complétude que le reviewer
    if not ok:
        return {"judged": False, "reason": reason}

    sot = sot_path_for(settings, project)
    try:
        head_sha = git.feature_sha(sot, feat["branch"])
        diff_files = git.diff_names(sot, base=WOAW_BASE, head=feat["branch"])
        diff_text = git.diff_text(sot, base=WOAW_BASE, head=feat["branch"])
    except GitOpError:
        return {"judged": False, "reason": f"branche {feat['branch']} absente — feature jamais dispatchée"}
    if not verify.has_visual_change(diff_files, diff_text):
        return {"judged": False, "reason": "aucune surface UI touchée — axe woaw N/A"}
    if woaw.is_fresh(woaw.read_verdict(settings, project, feature), current_sha=head_sha):
        return {"judged": False, "reason": "verdict woaw déjà frais sur ce HEAD (idempotent)"}

    wt = worktree_mod.worktree_path_for(settings, project, feature)
    if not wt.is_dir():
        return {"judged": False, "reason": f"worktree absent : {wt} — la feature doit être vivante"}

    shot_path = woaw.state_path(settings, project, feature).with_name("woaw-shot.png")
    route = capture_route_screenshot(conn, settings, project=project, feature=feature, out_path=shot_path,
                                     deployer=deployer, teardowner=teardowner, shot_runner=shot_runner)
    if route is None:
        return {"judged": False, "reason": "capture du screenshot impossible (runner/preview KO) — pas de "
                                           "verdict (advisory : aucun blocage)"}

    prompt = build_woaw_prompt(str(shot_path), feat, route)
    session_id = ids.new_id()
    argv = worker.build_headless_argv(session_id=session_id, work=False,
                                      output_format="json", allowed_tools=WOAW_TOOLS)
    env = tools_env(settings)
    try:                                                       # preflight = PRÉ-run → un échec ici = non-run
        preflight_tools(wt, settings, env=env)
        auth.trust_workspace(sot)
        if shutil.which("claude", path=env.get("PATH")) is None:
            raise ToolPreflightError("claude introuvable sur le PATH du juge — installe Claude Code.")
    except (ToolPreflightError, run.RunTimeout) as exc:
        return {"judged": False, "reason": f"juge non lançable : {exc}"}

    active_runner = runner or run.run
    try:
        proc = active_runner(argv, cwd=wt, input_text=prompt, timeout=WOAW_TIMEOUT, env=env)
    except run.RunTimeout as exc:
        return {"judged": False, "reason": f"juge non lançable : {exc}"}
    parsed = worker.parse_headless_result(proc.stdout, proc.returncode)
    if not parsed.get("ok"):
        return {"judged": False, "reason": f"juge échoué : {parsed.get('error') or 'sortie illisible'}"}

    payload = _extract_payload(parsed.get("result"))
    payload.setdefault("route", route)
    verdict = woaw.write_verdict(settings, project, feature, payload, sha=head_sha)
    return {"judged": True, "reason": "verdict woaw écrit (advisory)", "verdict": verdict,
            "counts": verdict["counts"], "flat": verdict["flat"]}


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `forgemaster gate woaw-dispatch <feature>` : dispatche le juge woaw (si prêt + UI) → verdict
    advisory SHA-bound. **Gate d'auth** (jamais de spawn silencieux). Exit 0 même si non jugé (advisory : ne
    fait jamais échouer un pipeline)."""
    from forgemaster.db import store

    if not auth.claude_auth_status()["authenticated"]:
        print(f"erreur : {auth.AUTH_HINT}")
        return 2
    conn = store.open_db(settings)
    try:
        report = dispatch_woaw(conn, settings, feature_ref=args.feature)
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    if not report["judged"]:
        print(f"juge woaw non dispatché : {report['reason']}")
        return 0                                               # advisory : non-jugé ≠ échec de gate
    c = report["counts"]
    flat = " · SEUIL DE PLAT" if report.get("flat") else ""
    print(f"verdict woaw écrit (advisory) : 🔴 {c['red']} · 🟡 {c['yellow']} · 🟣 {c['purple']}{flat}")
    return 0
