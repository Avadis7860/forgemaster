"""webbuild — build de la SPA (Vite) depuis un checkout source. Réutilisé par `cockpit setup` (chemin
from-clone) et par le hook de packaging (`hatch_build.py`). **Stdlib-pur** (subprocess) : aucune dép, le
module s'importe sans le serveur.

Modèle de distribution turnkey (décision 2026-07-03) : **l'utilisateur final n'installe que Python** — la
dist voyage dans le wheel (buildée au packaging, chez le mainteneur/CI où Node est présent). `cockpit setup`
couvre le cas où l'on travaille **depuis les sources** : builder le front sur place quand Node est là. On ne
re-committe JAMAIS la dist dans git (respecte `docs/specs/web-cockpit-spa.md`).
"""
from __future__ import annotations

import shutil
import subprocess
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
