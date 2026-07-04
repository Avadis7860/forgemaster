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


def ensure_codemap() -> str:
    """Garantit que `python -m codemap` marche dans le venv courant — requis par l'onglet **Flow**
    (`src/cockpit/codemap/index.py` invoque `sys.executable -m codemap`). En install **wheel**, code-map est
    déjà empaqueté (rien à faire). En **from-clone**, code-map n'est PAS dans les sources cockpit : on
    l'installe depuis un checkout sibling `../code-map` s'il existe. **Jamais fatal** — Flow est une surface,
    pas le cœur CLI ; on rend un message d'état actionnable."""
    if importlib.util.find_spec("codemap") is not None:
        return "code-map déjà disponible (`python -m codemap`)."
    src = find_codemap_src()
    if src is None:
        return ("code-map introuvable → l'onglet Flow restera indisponible. Clone-le en sibling du cockpit "
                "(`git clone …/code-map` à côté) puis relance `cockpit setup`, ou installe le wheel packagé "
                "(code-map y est inclus).")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", str(src)], check=True)
    except subprocess.CalledProcessError as exc:
        return f"install code-map échouée ({' '.join(exc.cmd)} → code {exc.returncode}) — Flow indisponible."
    return f"code-map installé depuis {src} (`python -m codemap`)."
