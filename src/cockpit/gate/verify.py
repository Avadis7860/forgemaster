"""verify — gate **feature-verified** (Tier-1.5) : prouve que le RÉSULTAT métier s'affiche réellement
(marqueurs attendus rendus dans le DOM), pas juste que le déploiement répond 200.

Port de `services/loops/feature_verify.py`. Décisions verrouillées (spec feature-verified-gate) :
- porte **déterministe** (le LLM ne juge/seuil pas) ; scope aux surfaces UI du diff (`has_ui`, heuristique
  UNIQUE partagée avec `gate/merge`) ;
- **fail-CLOSED** : node/browser/timeout/runner-absent → `ok=False`, JAMAIS blanchi (un target non prouvé
  n'est pas un target vert) ; 0 cible → `ok=False` (pas de blanchiment par absence) ;
- ancre = SHA de la feature (tout commit ultérieur périme le verdict) ;
- **N/A-safe** : pas d'UI touchée → aucun blocage ajouté (`compose_merge_decision` ignore le Tier-1.5).

Refactors : **#13** — état sous `settings.home`, clé par (projet, feature) ; le runner Node est résolu par
config (`COCKPIT_VERIFY_RUNNER` ou `<home>/runners/render_check.js`), pas un chemin `.claude/` en dur.
**#10** — le gate lui-même (la preuve e2e est une porte, pas un log). L'override Tier-1.5 est humain et
**Tier-1.5 seulement** (jamais un 🔴 Tier-0/Tier-1), géré par `gate/merge`.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cockpit import tools
from cockpit.config import Settings

if TYPE_CHECKING:
    from cockpit.runtime.backend import ComposeBackend

CONTRACT_VERSION = "feature-verify-v2"  # v2 : preuve deux-temps — cf. docs/specs/feature-verified-gate.md
DEFAULT_TIMEOUT_MS = 15000
ENV_RUNNER = "COCKPIT_VERIFY_RUNNER"

# Détection de surface UI — heuristique UNIQUE (partagée `gate/merge` ↔ `gate/verify`, pas deux copies qui
# dérivent). Un diff qui touche ces chemins/suffixes a une surface visible → la preuve e2e devient
# OBLIGATOIRE ; sinon Tier-1.5 = N/A (non bloquant).
UI_PATH_HINTS = ("/web/src/", "/src/pages/", "/src/components/")
UI_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte")


def has_ui(files: list[str]) -> bool:
    """True ssi au moins un fichier du diff a une surface front (suffixe front ou chemin de page/composant),
    par NOM seul. Prédicat coarse — le trigger du gate est `has_visual_change` (hybride nom + contenu), qui
    distingue un VRAI changement visuel d'un `.tsx` de câblage/type/contrat. Conservé (référence + tests)."""
    return any(f.endswith(UI_SUFFIXES) or any(h in f for h in UI_PATH_HINTS) for f in files)


# Trigger hybride (spec feature-verified rule 2, raffinée) : STYLE et dossiers RENDUS sont visuels par NOM
# (couvre aussi les suppressions) ; un fichier front AILLEURS (App.tsx root, lib/) n'est visuel que si ses
# lignes AJOUTÉES introduisent du MARKUP — un câblage/type/contrat (aucun markup) n'exige PAS de preuve.
STYLE_SUFFIXES = (".css", ".scss", ".sass", ".less")
RENDERED_DIR_HINTS = ("/pages/", "/components/", "/routes/", "/layouts/", "/views/")
# Marqueurs de markup CONSERVATEURS : présents dans du JSX/HTML rendu, absents des génériques TS
# (`useState<Foo>()`, `Map<string, X>`) → pas de faux-positif « visuel » sur du câblage typé.
MARKUP_MARKERS = ("</", "/>", "className=", "class=")


def _is_front_file(f: str) -> bool:
    return f.endswith(UI_SUFFIXES)


def _in_rendered_dir(f: str) -> bool:
    return any(h in f for h in RENDERED_DIR_HINTS)


def _looks_like_markup(line: str) -> bool:
    return any(m in line for m in MARKUP_MARKERS)


def _added_lines_by_file(diff_text: str) -> dict[str, list[str]]:
    """Lignes AJOUTÉES (`+`, hors en-têtes `+++`) d'un diff unifié multi-fichiers, par fichier de destination
    (`+++ b/<path>`). Corpus du volet CONTENU du trigger (lignes introduites, jamais le pré-existant). PUR."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            cur = None if path == "/dev/null" else path
            continue
        if cur is not None and line.startswith("+") and not line.startswith("+++"):
            out.setdefault(cur, []).append(line[1:])
    return out


def has_visual_change(files: list[str], diff_text: str) -> bool:
    """Trigger Tier-1.5 (feature-verified) — hybride NOM + CONTENU. True ssi le diff porte un **vrai
    changement visuel** : (1) STYLE touché (par nom) ; (2) fichier front sous un dossier RENDU
    (`pages/`/`components/`/`routes/`/`layouts/`/`views/`, par nom — couvre aussi les suppressions) ;
    (3) fichier front AILLEURS (App.tsx root, `lib/`) qui INTRODUIT du markup (`</`, `/>`, `className=`).
    Un `.tsx` de câblage/type/contrat (aucun markup ajouté) → NON visuel → Tier-1.5 N/A (review Tier-1 le
    couvre). N/A-safe : aucun front/style touché → False. Déterministe, scope lignes introduites (rule 2)."""
    for f in files:
        if f.endswith(STYLE_SUFFIXES):
            return True
        if _is_front_file(f) and _in_rendered_dir(f):
            return True
    for path, lines in _added_lines_by_file(diff_text).items():
        markup = any(_looks_like_markup(ln) for ln in lines)
        if markup and _is_front_file(path) and not _in_rendered_dir(path):
            return True
    return False


def state_path(settings: Settings, project: str, feature: str) -> Path:
    """Verdict Tier-1.5, clé par (projet, feature) sous `settings.home` (#13)."""
    return settings.home / "gate" / project / feature / "verify.json"


def runner_path(settings: Settings) -> Path:
    """Chemin du runner Node (Playwright) : `$COCKPIT_VERIFY_RUNNER`, sinon `<home>/runners/render_check.js`
    (#13, plus de chemin `.claude/skills/` en dur). Absent → `verify_target` fail-close (pas de faux-vert)."""
    override = os.environ.get(ENV_RUNNER)
    return Path(override) if override else settings.home / "runners" / "render_check.js"


MARKERS_FILE = ".cockpit/verify-markers.json"


def _clean_str_list(value: object) -> list[str]:
    """Chaînes non vides (`.strip()`) d'une liste, robuste aux entrées sales. PUR."""
    if not isinstance(value, list):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]


def _clean_clicks(value: object) -> list[dict]:
    """Gestes valides d'une liste : chaque entrée garde un `text` OU `selector` non vide (les optionnels
    `nth`/`wait_ms`/`exact`/`timeout_ms` passent tels quels au runner). Entrées sales filtrées. PUR. La garde
    **read-only** reste doctrinale (METHOD + commentaire runner), non enforcée runtime : un denylist de labels
    ferait des faux-négatifs sur des libellés légitimes (« Envoyer un tour » lisible mais read-only)."""
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text, selector = item.get("text"), item.get("selector")
        has_text = isinstance(text, str) and bool(text.strip())
        has_sel = isinstance(selector, str) and bool(selector.strip())
        if has_text or has_sel:
            out.append(item)
    return out


def _clean_path(value: object) -> str:
    """Route (chemin URL) où sonder la preuve, **normalisée avec un `/` de tête**. Défaut `/` (racine) — un
    contrat legacy sans `path` sonde exactement comme avant (rétro-compat). Robuste aux entrées sales → `/`.
    PUR. Ne couvre qu'UNE route (une feature = une surface de preuve) ; multi-route = `targets:[]` futur."""
    if not isinstance(value, str) or not value.strip():
        return "/"
    p = value.strip()
    return p if p.startswith("/") else "/" + p


def _clean_canvas(value: object) -> dict:
    """Contrat **plancher canvas** : `{"selector": "<css>", "non_blank": true}` — prouve qu'un rendu
    `<canvas>`/WebGL (innerText vide, invisible au match textuel) a réellement peint. Robuste aux entrées
    sales → `{}` (pas de plancher). `selector` défaut `"canvas"`. Le canvas ne prouve PAS le bon état (ça =
    sidecar texte sr-only lu par `markers`) — juste qu'il n'est ni vide ni cassé. PUR."""
    if not isinstance(value, dict) or not value.get("non_blank"):
        return {}
    sel = value.get("selector")
    selector = sel.strip() if isinstance(sel, str) and sel.strip() else "canvas"
    return {"selector": selector, "non_blank": True}


def read_verify_contract(workdir: Path) -> dict:
    """Contrat de preuve **déclaré par le worker** dans `<workdir>/.cockpit/verify-markers.json`. Deux formes,
    la seconde ADDITIVE (rétro-compatible) :

    - at-rest seul (legacy) : `{"markers": ["<chaîne FR rendue>", ...]}` ;
    - **jalon jouable** (deux-temps) : idem + `"interaction": {"clicks": [{"text": "..."}], "after_markers":
      ["<chaîne qui n'existe qu'APRÈS le geste>"], "wait_for_text": "...", "wait_timeout_ms": 8000}`. Prouve
      une TRANSITION : le runner joue `clicks`, exige `after_markers` APRÈS le geste ET assert qu'ils étaient
      ABSENTS at-rest (`pre_present` non vide ⇒ transition non prouvée ⇒ 🔴).

    Champ **optionnel `"path"`** (ADDITIF, défaut `/`) : la route où sonder la preuve quand l'écran vit sous
    un sous-chemin (ex. un showcase sur `/design-system`). Absent ⇒ racine `/`, exactement comme avant.

    PUR, ne lève JAMAIS. Absent / JSON invalide / forme inattendue → tout vide (dégrade honnête ; `markers`
    vide reste fail-closed en aval via `build_verdict`, jamais blanchi par absence de cible)."""
    empty: dict = {"markers": [], "clicks": [], "after_markers": [], "wait_for_text": None,
                   "wait_timeout_ms": None, "canvas": {}, "path": "/"}
    path = workdir / MARKERS_FILE
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    contract: dict = dict(empty)
    contract["markers"] = _clean_str_list(data.get("markers"))
    contract["canvas"] = _clean_canvas(data.get("canvas"))
    contract["path"] = _clean_path(data.get("path"))
    interaction = data.get("interaction")
    if isinstance(interaction, dict):
        contract["clicks"] = _clean_clicks(interaction.get("clicks"))
        contract["after_markers"] = _clean_str_list(interaction.get("after_markers"))
        wft = interaction.get("wait_for_text")
        contract["wait_for_text"] = wft.strip() if isinstance(wft, str) and wft.strip() else None
        wtm = interaction.get("wait_timeout_ms")
        valid_wtm = isinstance(wtm, int) and not isinstance(wtm, bool) and wtm > 0
        contract["wait_timeout_ms"] = wtm if valid_wtm else None
    return contract


def read_declared_markers(workdir: Path) -> list[str]:
    """Markers at-rest déclarés (`{"markers": [...]}`). Wrapper mince sur `read_verify_contract` — conserve
    sa signature et ses appelants ; toute la nouveauté (interaction deux-temps) via `read_verify_contract`."""
    return read_verify_contract(workdir)["markers"]


# -- runner (PUR sauf le subprocess node) -----------------------------------------------------------

def build_payload(url: str, markers: list[str], *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                  screenshot: str | None = None, cookies: list[dict] | None = None,
                  clicks: list[dict] | None = None, after_markers: list[str] | None = None,
                  wait_for_text: str | None = None, wait_timeout_ms: int | None = None,
                  canvas: dict | None = None) -> dict:
    """Payload passé au runner Node. PUR. Champs at-rest toujours ; champs d'interaction (deux-temps) et
    `canvas` (plancher) inclus **ssi non vides** → rétro-compatible (un contrat legacy produit exactement
    l'ancien payload). `cookies` ssi non vide (deep-link authentifié) ; sinon contexte anonyme."""
    payload: dict = {"url": url, "markers": markers, "timeout_ms": timeout_ms}
    if screenshot:
        payload["screenshot"] = screenshot
    if cookies:
        payload["cookies"] = cookies
    if clicks:
        payload["clicks"] = clicks
    if after_markers:
        payload["after_markers"] = after_markers
    if wait_for_text:
        payload["wait_for_text"] = wait_for_text
    if wait_timeout_ms:
        payload["wait_timeout_ms"] = wait_timeout_ms
    if canvas:
        payload["canvas"] = canvas
    return payload


def verify_target(settings: Settings, url: str, markers: list[str], *, name: str | None = None,
                  timeout_ms: int = DEFAULT_TIMEOUT_MS, cookies: list[dict] | None = None,
                  clicks: list[dict] | None = None, after_markers: list[str] | None = None,
                  wait_for_text: str | None = None, wait_timeout_ms: int | None = None,
                  canvas: dict | None = None) -> dict:
    """Vérifie une cible via le runner Node. Ne lève JAMAIS : runner absent / node ko / browser ko / timeout
    → `{ok: False, error: ...}` (fail-closed — un target non prouvé n'est pas vert). Si `after_markers` est
    déclaré (jalon jouable), le runner joue `clicks` puis exige ces markers APRÈS le geste ET assert qu'ils
    étaient ABSENTS at-rest (`pre_present` non vide = transition non prouvée = 🔴). Si `canvas` est déclaré
    (plancher), le runner exige que l'élément canvas ait peint (pixels non-uniformes) — replié dans `ok`."""
    payload = build_payload(url, markers, timeout_ms=timeout_ms, cookies=cookies, clicks=clicks,
                            after_markers=after_markers, wait_for_text=wait_for_text,
                            wait_timeout_ms=wait_timeout_ms, canvas=canvas)
    res: dict = {"name": name, "url": url, "ok": False, "found": [], "missing": list(markers),
                 "after_found": [], "after_missing": list(after_markers or []), "pre_present": [],
                 "title": None}
    runner = runner_path(settings)
    if not runner.is_file():
        res["error"] = f"runner absent : {runner} (configure {ENV_RUNNER})"
        return res
    try:
        # `env=tools_env` : le PATH systemd minimal du daemon (`/usr/bin:/usr/sbin:…`) n'expose PAS
        # `node` (nodeenv sous `tools/bin`). Sans ce préfixe, `subprocess.run(["node", …])` lève
        # FileNotFoundError sur toute install réelle → runner mort-né. Même résolution que le worker de
        # dispatch et le gate toolchain (cf. `tools.tools_env`).
        p = subprocess.run(["node", str(runner), json.dumps(payload)],
                           capture_output=True, text=True, timeout=timeout_ms / 1000 + 30,
                           env=tools.tools_env(settings))
    except (subprocess.TimeoutExpired, OSError) as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res
    if p.returncode != 0 and not p.stdout.strip():
        res["error"] = f"runner rc={p.returncode}: {p.stderr.strip()[:200]}"
        return res
    try:
        out = json.loads(p.stdout)
    except ValueError:
        res["error"] = f"sortie runner non-JSON : {p.stdout.strip()[:200]}"
        return res
    out["name"] = name
    out.setdefault("found", [])
    out.setdefault("missing", list(markers))
    out.setdefault("after_found", [])
    out.setdefault("after_missing", list(after_markers or []))
    out.setdefault("pre_present", [])
    return out


# -- verdict lié au SHA (Tier-1.5) ------------------------------------------------------------------

def build_verdict(targets_results: list[dict], *, sha: str | None, ts: str) -> dict:
    """PUR. Assemble le verdict Tier-1.5. `ok=True` ssi ≥1 cible ET toutes ok (jamais blanchi par 0 cible).
    `sha`/`ts` injectés → pas de git/horloge implicite ici."""
    ok = bool(targets_results) and all(t.get("ok") for t in targets_results)
    return {
        "contract_version": CONTRACT_VERSION,
        "reviewed_sha": sha,
        "ts": ts,
        "reviewer": "feature-verify",
        "ok": ok,
        "n_targets": len(targets_results),
        "n_failed": sum(1 for t in targets_results if not t.get("ok")),
        "targets": targets_results,
    }


def write_verdict(settings: Settings, project: str, feature: str, targets_results: list[dict], *,
                  sha: str | None, ts: str | None = None) -> dict:
    """Persiste le verdict Tier-1.5 sous `state_path`. `sha` injecté (SHA de la feature) ; `ts` défaut UTC."""
    verdict = build_verdict(targets_results, sha=sha,
                            ts=ts or datetime.now(UTC).isoformat(timespec="seconds"))
    sp = state_path(settings, project, feature)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verdict


def read_verdict(settings: Settings, project: str, feature: str) -> dict | None:
    sp = state_path(settings, project, feature)
    if not sp.is_file():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except ValueError:
        return None


def is_fresh(verdict: dict | None, *, current_sha: str | None) -> bool:
    """True ssi un verdict existe, porte le SHA courant de la feature, ET le `CONTRACT_VERSION` courant. Un
    verdict d'un contrat antérieur (`feature-verify-v1`, preuve at-rest seule) est traité **non frais** → il
    force un re-gate sous la sémantique deux-temps (durcissement du bump v2). `current_sha` injecté → PUR."""
    if not verdict:
        return False
    if verdict.get("contract_version") != CONTRACT_VERSION:
        return False
    return bool(current_sha) and verdict.get("reviewed_sha") == current_sha


def gate_blocking(verdict: dict | None) -> bool:
    """True ssi le verdict existe et porte au moins un target non prouvé (ou `ok=False`)."""
    if not verdict:
        return False
    return bool(verdict.get("n_failed", 0) > 0) or not verdict.get("ok", False)


def status(settings: Settings, project: str, feature: str, *, current_sha: str | None) -> dict:
    """Synthèse pour `gate/merge`. `present=False` = aucune cible UI prouvée → le gate traite ça en N/A."""
    v = read_verdict(settings, project, feature)
    return {"present": v is not None, "fresh": is_fresh(v, current_sha=current_sha),
            "ok": v.get("ok") if v else None,
            "blocking": gate_blocking(v) if v else False,
            "n_targets": v.get("n_targets") if v else None,
            "n_failed": v.get("n_failed") if v else None,
            "reviewed_sha": v.get("reviewed_sha") if v else None}


def _wait_http_ready(url: str, *, timeout_s: float = 90.0, interval_s: float = 1.0) -> bool:
    """Attend (poll BORNÉ) que le service écoute sur `url`. Toute réponse HTTP — même 4xx/5xx — prouve que le
    serveur écoute → prêt. Refus de connexion / DNS / socket → retente jusqu'à `timeout_s`. Épuisé → False
    (l'appelant tente quand même : `verify_target` fail-close si le rendu n'arrive pas). PUR sauf socket."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=5)  # noqa: S310 — URL locale de preview, pas d'entrée externe
            return True
        except urllib.error.HTTPError:
            return True                              # 4xx/5xx = le serveur répond → il écoute
        except (urllib.error.URLError, OSError):
            time.sleep(interval_s)                   # connexion refusée / pas encore up → retente
    return False


def autoverify_feature(conn: sqlite3.Connection, settings: Settings, *, project: str, feature: str,
                       sha: str | None, backend: ComposeBackend | None = None) -> dict:
    """Preuve de rendu Tier-1.5 **auto-suffisante** (source UNIQUE, CLI + boucle) : preview-déploie le
    worktree de la feature, attend qu'il réponde, lit les markers déclarés par le worker, prouve le rendu,
    écrit le verdict SHA-bound, **démonte TOUJOURS** (finally). `deploy_preview` lève `ValueError`
    (worktree/compose absent = type non hébergeable) → propagé : pas de preuve possible, mais aucun faux-vert.
    Imports paresseux de `runtime.engine` → coupe le cycle gate↔runtime."""
    from cockpit.runtime.engine import deploy_preview, teardown_preview

    preview = deploy_preview(conn, settings, slug=project, feature=feature, backend=backend)
    try:
        _wait_http_ready(preview["url"])                       # readiness sur la racine (toute réponse = up)
        contract = read_verify_contract(Path(preview["workdir"]))
        target_url = preview["url"].rstrip("/") + contract["path"]   # sonde la route déclarée (défaut `/`)
        res = verify_target(settings, target_url, contract["markers"], name=feature,
                            clicks=contract["clicks"], after_markers=contract["after_markers"],
                            wait_for_text=contract["wait_for_text"],
                            wait_timeout_ms=contract["wait_timeout_ms"], canvas=contract["canvas"])
        return write_verdict(settings, project, feature, [res], sha=sha)
    finally:
        teardown_preview(conn, settings, slug=project, feature=feature, backend=backend)


def _print_verdict(results: list[dict], verdict: dict, sha: str | None) -> None:
    for t in results:
        mark = "🟢" if t.get("ok") else "🔴"
        extra = f" manquants={t['missing']}" if t.get("missing") else ""
        if t.get("after_missing"):
            extra += f" post-geste manquants={t['after_missing']}"
        if t.get("pre_present"):
            extra += f" (déjà présents at-rest → transition non prouvée : {t['pre_present']})"
        if isinstance(t.get("canvas"), dict) and not t["canvas"].get("non_blank_ok"):
            extra += f" canvas vide/cassé ({t['canvas'].get('reason', 'non peint')})"
        failed_clicks = [c.get("step") for c in t.get("clicks", []) if not c.get("ok")]
        if failed_clicks:
            extra += f" clics échoués={failed_clicks}"
        extra += f" ({t['error']})" if t.get("error") else ""
        print(f"  {mark} {t.get('name') or t['url']}{extra}")
    print(f"feature-verify : {'🟢 vert' if verdict['ok'] else '🔴 échec'} "
          f"({verdict['n_targets'] - verdict['n_failed']}/{verdict['n_targets']} cibles, sha {str(sha)[:8]})")


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate verify <feature>` : **sans manifeste stdin** (tty/vide) → self-deploying
    (`autoverify_feature` : preview-déploie le worktree, prouve les markers déclarés, démonte). **Avec** un
    manifeste `[{name,url,markers}]` sur stdin → mode URL externe (rétrocompat). Verdict SHA-bound."""
    import sys

    from cockpit.db import store
    from cockpit.git.internal import InternalGit
    from cockpit.projects.registry import get_project
    from cockpit.roadmap.model import resolve_feature

    conn = store.open_db(settings)
    try:
        project_slug, feature_slug = args.feature.split("/", 1) if "/" in args.feature else ("", "")
        try:
            feature = resolve_feature(conn, args.feature)
            sot = Path(get_project(conn, project_slug)["sot_path"])
        except (ValueError, KeyError) as exc:
            print(f"erreur : {exc}")
            return 1
        sha = InternalGit().feature_sha(sot, feature["branch"])

        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        if not raw.strip():                          # aucun manifeste → self-deploying
            try:
                verdict = autoverify_feature(conn, settings, project=project_slug,
                                             feature=feature_slug, sha=sha)
            except ValueError as exc:
                print(f"preview-deploy impossible : {exc}", file=sys.stderr)
                return 1
            _print_verdict(verdict["targets"], verdict, sha)
            return 0 if verdict["ok"] else 1

        try:                                         # manifeste stdin → URL externe (rétrocompat)
            data = json.loads(raw)
        except ValueError as exc:
            print(f"manifeste JSON invalide : {exc}", file=sys.stderr)
            return 2
        targets = data.get("targets", data) if isinstance(data, dict) else data
        if not isinstance(targets, list) or not all(isinstance(t, dict) and t.get("url") for t in targets):
            print("manifeste invalide : attendu une liste de {name, url, markers[]}", file=sys.stderr)
            return 2
        results = [verify_target(settings, t["url"], t.get("markers", []), name=t.get("name"),
                                 timeout_ms=t.get("timeout_ms", DEFAULT_TIMEOUT_MS),
                                 cookies=t.get("cookies"), clicks=t.get("clicks"),
                                 after_markers=t.get("after_markers"),
                                 wait_for_text=t.get("wait_for_text"),
                                 wait_timeout_ms=t.get("wait_timeout_ms"),
                                 canvas=t.get("canvas")) for t in targets]
        verdict = write_verdict(settings, project_slug, feature_slug, results, sha=sha)
        _print_verdict(results, verdict, sha)
        return 0 if verdict["ok"] else 1
    finally:
        conn.close()
