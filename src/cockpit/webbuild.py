"""webbuild — build de la SPA (Vite) depuis un checkout source. Réutilisé par `cockpit setup` (chemin
from-clone) et par le hook de packaging (`hatch_build.py`). **Stdlib-pur** (subprocess) : aucune dép, le
module s'importe sans le serveur.

Modèle de distribution turnkey (décision 2026-07-03) : **l'utilisateur final n'installe que Python** — la
dist voyage dans le wheel (buildée au packaging, chez le mainteneur/CI où Node est présent). `cockpit setup`
couvre le cas où l'on travaille **depuis les sources** : builder le front sur place quand Node est là. On ne
re-committe JAMAIS la dist dans git (respecte `docs/specs/web-cockpit-spa.md`).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


class FrontBuildError(RuntimeError):
    """Build front impossible (Node absent, `web/` absent, ou npm en échec) — message actionnable."""


def find_web_dir(start: Path | None = None) -> Path | None:
    """Localise le dossier `web/` du checkout (celui qui contient `package.json`) en remontant depuis
    `start` (défaut : ce module). `None` en install wheel pure : pas de sources front → la dist est déjà
    empaquetée, rien à builder."""
    here = (start or Path(__file__)).resolve()
    for base in (here, *here.parents):
        cand = base / "web"
        if (cand / "package.json").is_file():
            return cand
    return None


def build_front(web_dir: Path, *, clean_install: bool = True) -> Path:
    """Build la SPA dans `web_dir` → retourne `web_dir/dist`. Lève `FrontBuildError` (message actionnable)
    si Node/npm est absent ou si npm échoue. `clean_install` → `npm ci` (reproductible, exige un lockfile)
    sinon `npm install`. La sortie npm est héritée (l'utilisateur voit la progression)."""
    npm = shutil.which("npm")
    if not npm:
        raise FrontBuildError(
            "Node.js / npm introuvable. L'UI se build depuis les sources avec Node ≥ 18 :\n"
            "  • installe Node (https://nodejs.org) puis relance `cockpit setup`, ou\n"
            "  • installe le wheel packagé — l'UI y est déjà incluse, aucun Node requis."
        )
    use_ci = clean_install and (web_dir / "package-lock.json").is_file()
    install = [npm, "ci"] if use_ci else [npm, "install"]
    try:
        subprocess.run(install, cwd=web_dir, check=True)
        subprocess.run([npm, "run", "build"], cwd=web_dir, check=True)
    except subprocess.CalledProcessError as exc:
        raise FrontBuildError(f"npm a échoué ({' '.join(exc.cmd)} → code {exc.returncode}).") from exc
    dist = web_dir / "dist"
    if not (dist / "index.html").is_file():
        raise FrontBuildError(f"build terminé mais {dist / 'index.html'} absent — build front cassé ?")
    return dist


def find_codemap_src(start: Path | None = None) -> Path | None:
    """Localise un checkout **code-map** sibling (`…/code-map` portant `src/codemap/__main__.py`) en remontant
    depuis le checkout cockpit. `None` si absent. Sert au chemin from-clone : en install wheel code-map est
    déjà empaqueté, mais un clone des sources ne l'a pas."""
    here = (start or Path(__file__)).resolve()
    for base in here.parents:
        cand = base / "code-map" / "src" / "codemap" / "__main__.py"
        if cand.is_file():
            return base / "code-map"
    return None


def served_from(module: str, src: Path) -> bool:
    """Le `module` importable vient-il DÉJÀ des sources de `src` (install éditable) ?

    Le discriminant entre « à jour par construction » et « copie figée ». Une copie installée dans
    `site-packages` répond `False` même si le module s'importe parfaitement — c'est tout l'objet du
    correctif (cf. `_install_from_sibling`)."""
    spec = importlib.util.find_spec(module)
    origin = getattr(spec, "origin", None)
    if origin is None:
        return False
    try:
        return Path(origin).resolve().is_relative_to(Path(src).resolve())
    except OSError:                                    # chemin invalide/inaccessible → on ne suppose rien
        return False


def _install_from_sibling(module: str, src: Path) -> str | None:
    """Installe `src` en **ÉDITABLE** dans le venv courant. `None` = succès (ou déjà éditable), sinon le
    message d'échec.

    DÉFAUT CORRIGÉ (mesuré le 2026-08-01) : ce câblage court-circuitait sur `find_spec(...) is not None`
    et installait une **copie**. Une carte installée n'était donc **jamais** remise à jour — le venv de ce
    repo servait un `codemap` sans le verbe `check`, livré chez code-map le jour même, et **les deux
    annonçaient la même version** (`0.1.0`, schéma `1.6.0`) : rien ne permettait de les distinguer. Une
    session qui obéissait à `CLAUDE.md` et tapait `codemap check` recevait `invalid choice`.

    L'éditable est la réponse durable : la carte suit le `git pull` de son sibling, sans entretien ni
    fenêtre de dérive. Écarté — une copie ré-installée avec `--upgrade` : elle re-fige au commit du jour,
    on paie le même défaut plus tard.

    Idempotent SANS relancer pip : si le module vient déjà de `src`, il n'y a rien à faire (`pip install -e`
    reconstruit un wheel éditable à chaque appel, et `cockpit setup` le paierait × 4 pour rien).
    """
    if served_from(module, src):
        return None
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(src)], check=True)
    except subprocess.CalledProcessError as exc:
        return f"code {exc.returncode}"
    return None


def ensure_codemap() -> str:
    """Garantit que `python -m codemap` marche dans le venv courant — requis par l'onglet **Flow**
    (`src/cockpit/codemap/index.py` invoque `sys.executable -m codemap`). En install **wheel**, code-map est
    déjà empaqueté et aucun sibling n'existe (rien à faire). En **from-clone**, code-map n'est PAS dans les
    sources cockpit : on l'installe **en éditable** depuis le checkout sibling `../code-map`. **Jamais
    fatal** — Flow est une surface, pas le cœur CLI ; on rend un message d'état actionnable.

    L'ordre est load-bearing : on cherche le sibling **avant** de se satisfaire d'un module importable.
    L'inverse figeait la carte à sa première install (cf. `_install_from_sibling`)."""
    src = find_codemap_src()
    if src is None:
        if importlib.util.find_spec("codemap") is not None:
            return "code-map déjà disponible (`python -m codemap`)."
        return ("code-map introuvable → l'onglet Flow restera indisponible. Clone-le en sibling du cockpit "
                "(`git clone …/code-map` à côté) puis relance `cockpit setup`, ou installe le wheel packagé "
                "(code-map y est inclus).")
    if served_from("codemap", src):
        return f"code-map déjà éditable depuis {src} (suit son repo)."
    if err := _install_from_sibling("codemap", src):
        return f"install code-map échouée ({err}) — Flow indisponible."
    return f"code-map installé (éditable) depuis {src} (`python -m codemap`)."


# Les 3 AUTRES cartes du framework (code-map a son propre chemin Flow ci-dessus). Cartes par-projet déclarées
# au contrat (`bundles/base/CLAUDE.md`) : le cockpit étant lui-même un projet, ses sessions/worktrees doivent
# pouvoir les interroger. En install wheel les CLIs viennent du provisioning (`tools.MAP_REPOS`) ; ce câblage
# sert le chemin **from-clone** (dev) depuis les siblings. Trou historique : `cockpit setup` ne câblait QUE
# code-map → frontmap/docsmap/taskmap absents du venv de dev (anti-archéologie front/docs cassée).
_SIBLING_MAPS: dict[str, str] = {"docs-map": "docsmap", "front-map": "frontmap", "task-map": "taskmap"}


def find_map_src(repo: str, module: str, start: Path | None = None) -> Path | None:
    """Localise un checkout **sibling** `…/<repo>` portant `src/<module>/__init__.py`, en remontant depuis le
    checkout cockpit. `None` si absent."""
    here = (start or Path(__file__)).resolve()
    for base in here.parents:
        if (base / repo / "src" / module / "__init__.py").is_file():
            return base / repo
    return None


def ensure_map(repo: str, module: str) -> str:
    """Garantit la carte `<module>` dans le venv courant (CLI `<module>` + `python -m <module>`), **en
    éditable** depuis le sibling `../<repo>` quand il existe — sinon on se contente de ce qui est importable.
    **Jamais fatal** — une carte absente dégrade l'anti-archéologie, pas le démarrage ; message actionnable.

    Même ordre que `ensure_codemap`, et pour la même raison : un module importable ne prouve pas qu'il est
    à jour. Une carte servie depuis `site-packages` est une photo, pas un miroir."""
    src = find_map_src(repo, module)
    if src is None:
        if importlib.util.find_spec(module) is not None:
            return f"{module} déjà disponible."
        return (f"{module} introuvable → clone {repo} en sibling du cockpit puis relance `cockpit setup`, "
                f"ou installe le wheel packagé.")
    if served_from(module, src):
        return f"{module} déjà éditable depuis {src} (suit son repo)."
    if err := _install_from_sibling(module, src):
        return f"install {module} échouée ({err})."
    return f"{module} installé (éditable) depuis {src}."


def ensure_maps() -> list[str]:
    """Câble les **4 cartes** du framework dans le venv du checkout cockpit (from-clone) : code-map (Flow) +
    les 3 siblings (docs-map/front-map/task-map). Rapport par carte, best-effort. Corrige le trou où seul
    code-map était câblé → frontmap absent du venv de dev (cf. cockpit-frontmap-cli-absent-from-venv)."""
    return [ensure_codemap(), *(ensure_map(repo, mod) for repo, mod in _SIBLING_MAPS.items())]
