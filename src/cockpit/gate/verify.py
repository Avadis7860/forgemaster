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
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cockpit.config import Settings

CONTRACT_VERSION = "feature-verify-v1"
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


def read_declared_markers(workdir: Path) -> list[str]:
    """Markers de rendu **déclarés par le worker** dans `<workdir>/.cockpit/verify-markers.json`
    (`{"markers": ["<chaîne FR rendue>", ...]}`). Source autonome des cibles Tier-1.5 : le worker déclare
    ce que SON UI rend, `verify_target` prouve qu'ils sont vraiment dans le DOM (il ne peut pas mentir).

    PUR, ne lève JAMAIS. Absent / JSON invalide / forme inattendue → `[]` (dégrade honnête = smoke-render ;
    `[]` reste fail-closed en aval, cf. `build_verdict` — jamais blanchi par absence de marker). Ne garde que
    les chaînes non vides (`.strip()`), robuste aux entrées sales."""
    path = workdir / MARKERS_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("markers"), list):
        return []
    return [s.strip() for s in data["markers"] if isinstance(s, str) and s.strip()]


# -- runner (PUR sauf le subprocess node) -----------------------------------------------------------

def build_payload(url: str, markers: list[str], *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                  screenshot: str | None = None, cookies: list[dict] | None = None) -> dict:
    """Payload passé au runner Node. PUR. `cookies` inclus ssi non vide (deep-link authentifié) ; sinon
    contexte anonyme (rétro-compatible)."""
    payload: dict = {"url": url, "markers": markers, "timeout_ms": timeout_ms}
    if screenshot:
        payload["screenshot"] = screenshot
    if cookies:
        payload["cookies"] = cookies
    return payload


def verify_target(settings: Settings, url: str, markers: list[str], *, name: str | None = None,
                  timeout_ms: int = DEFAULT_TIMEOUT_MS, cookies: list[dict] | None = None) -> dict:
    """Vérifie une cible via le runner Node. Ne lève JAMAIS : runner absent / node ko / browser ko / timeout
    → `{ok: False, error: ...}` (fail-closed — un target non prouvé n'est pas vert)."""
    payload = build_payload(url, markers, timeout_ms=timeout_ms, cookies=cookies)
    res: dict = {"name": name, "url": url, "ok": False, "found": [], "missing": list(markers),
                 "title": None}
    runner = runner_path(settings)
    if not runner.is_file():
        res["error"] = f"runner absent : {runner} (configure {ENV_RUNNER})"
        return res
    try:
        p = subprocess.run(["node", str(runner), json.dumps(payload)],
                           capture_output=True, text=True, timeout=timeout_ms / 1000 + 30)
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
    """True ssi un verdict existe ET porte le SHA courant de la feature. `current_sha` injecté → PUR."""
    if not verdict:
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


def cli_dispatch(settings: Settings, args: argparse.Namespace) -> int:
    """Route `cockpit gate verify <feature>` : lit un manifeste `[{name,url,markers}]` sur **stdin**, prouve
    chaque cible via le runner Node, écrit le verdict Tier-1.5 SHA-bound (ancré sur la branche de feature)."""
    import sys

    from cockpit.db import store
    from cockpit.git.internal import InternalGit
    from cockpit.projects.registry import get_project
    from cockpit.roadmap.model import resolve_feature

    conn = store.open_db(settings)
    try:
        project_slug, feature_slug = args.feature.split("/", 1) if "/" in args.feature else ("", "")
        feature = resolve_feature(conn, args.feature)
        sot = Path(get_project(conn, project_slug)["sot_path"])
    except (ValueError, KeyError) as exc:
        print(f"erreur : {exc}")
        return 1
    finally:
        conn.close()
    try:
        data = json.loads(sys.stdin.read())
    except ValueError as exc:
        print(f"manifeste JSON invalide : {exc}", file=sys.stderr)
        return 2
    targets = data.get("targets", data) if isinstance(data, dict) else data
    if not isinstance(targets, list) or not all(isinstance(t, dict) and t.get("url") for t in targets):
        print("manifeste invalide : attendu une liste de {name, url, markers[]}", file=sys.stderr)
        return 2
    sha = InternalGit().feature_sha(sot, feature["branch"])
    results = [verify_target(settings, t["url"], t.get("markers", []), name=t.get("name"),
                             timeout_ms=t.get("timeout_ms", DEFAULT_TIMEOUT_MS),
                             cookies=t.get("cookies")) for t in targets]
    verdict = write_verdict(settings, project_slug, feature_slug, results, sha=sha)
    for t in results:
        mark = "🟢" if t.get("ok") else "🔴"
        extra = f" manquants={t['missing']}" if t.get("missing") else ""
        extra += f" ({t['error']})" if t.get("error") else ""
        print(f"  {mark} {t.get('name') or t['url']}{extra}")
    print(f"feature-verify : {'🟢 vert' if verdict['ok'] else '🔴 échec'} "
          f"({verdict['n_targets'] - verdict['n_failed']}/{verdict['n_targets']} cibles, sha {str(sha)[:8]})")
    return 0 if verdict["ok"] else 1
