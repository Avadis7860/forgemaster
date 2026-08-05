"""provision.verify — la preuve qui manquait : les dépendances npm d'un bundle template **s'installent
et tournent**.

Le trou qu'elle referme. Un bundle template est du **capital semé** : chaque projet créé par la forge
hérite de ses dépendances. Une vulnérabilité y est *distribuée*, pas seulement *hébergée*. Or rien ne
prouvait ces manifestes — `npm run gate` juge le `web/` du forgemaster (l'application, pas les templates),
les tests Python qui citent un bundle vérifient le **semis** (il se résout, se copie, se paramètre), et le
crash-test void-runner prouve le **câblage MCP** d'un worker sans installer la moindre dépendance npm.
Conséquence : monter un lockfile de template revenait à le faire sur la seule foi d'un diff petit.

Ce que la preuve vaut, et pourquoi elle part du semis. On ne re-liste pas les fichiers du template « comme
le fait la forge » — on appelle **le même** `load_bundle(type)` que `registry.create_project`, qui écrit son
retour **verbatim** dans le SoT du projet neuf (aucune substitution de jeton au semis). Ce qui est installé
et gaté ici est donc, octet pour octet, ce que reçoit le premier projet créé après. Une preuve qui
reconstruirait son propre échantillon ne prouverait que son échantillon.

Trois refus **fail-closed**, parce qu'un vert obtenu pour une autre raison que « c'est bon » est le défaut
que cet appareil existe pour exclure :

- **install rouge ⇒ le gate n'est pas lancé**, et le verdict est ROUGE. Jamais de vert derrière une
  installation ratée.
- **aucune unité npm ⇒ `sans unité`**, jamais vert. Un bundle sans manifeste n'a pas *passé* sa preuve : il
  n'en a pas eu.
- **`npm` absent ⇒ `non montable`**, jamais vert. Même disposition que le Tier-0 natif (`gate/toolchain`).

Et le repli `npm ci` → `npm install` (lockfile absent) est **dit** dans le verdict, jamais silencieux : sans
verrou, l'installation résout des plages au moment où elle tourne — deux projets semés le même jour peuvent
recevoir deux arbres différents. C'est une preuve plus faible, elle doit se lire comme telle.

I/O injectable (invariant du repo) : `runner` (défaut `core.run`) et `which` sont des paramètres, donc le
comportement se teste sans réseau ni npm.
"""
from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from forgemaster.core.run import RunResult, run
from forgemaster.gate.toolchain import has_gate_script
from forgemaster.provision import load_bundle

DEFAULT_TIMEOUT_S = 900          # `npm ci` + build d'un template peut être long — borné pour ne pas pendre
_DETAIL_CHARS = 400              # queue d'erreur reportée : de quoi diagnostiquer, pas de quoi noyer

# États du verdict. Volontairement TROIS non-verts distincts : « rouge » (la preuve a tourné et a dit non)
# ne se confond pas avec « on n'a pas pu la monter » ni avec « il n'y avait rien à prouver ».
GREEN = "vert"
RED = "rouge"
UNMOUNTABLE = "non montable"
NO_UNIT = "sans unité npm"

Runner = Callable[..., RunResult]


@dataclass(frozen=True)
class UnitVerdict:
    """Le verdict d'UNE unité npm du semis (un dossier portant un `package.json` avec un script `gate`)."""

    rel_dir: str                       # chemin relatif à la racine du semis ("." = racine du bundle)
    locked: bool                       # un `package-lock.json` est présent à côté → `npm ci` possible
    install_argv: tuple[str, ...]
    install_ok: bool
    gate_ok: bool | None               # None = jamais lancé (l'installation a échoué avant)
    seconds: float
    detail: str = ""                   # queue de la sortie en échec

    @property
    def ok(self) -> bool:
        return self.install_ok and self.gate_ok is True


@dataclass(frozen=True)
class BundleVerdict:
    """Le verdict d'un bundle : l'état global + une ligne par unité npm, et la raison quand il n'y a pas
    d'unité (un état non-vert doit toujours porter son motif, jamais un tableau vide muet)."""

    project_type: str
    state: str
    units: tuple[UnitVerdict, ...] = ()
    reason: str = ""
    workdir: str = ""                  # non vide seulement si `keep=True` (semis conservé pour inspection)

    @property
    def ok(self) -> bool:
        return self.state == GREEN

    @property
    def seconds(self) -> float:
        return sum(u.seconds for u in self.units)

    @property
    def exit_code(self) -> int:
        """0 vert · 1 rouge (la preuve a tourné et refuse) · 2 la preuve n'a rien pu prouver."""
        if self.state == GREEN:
            return 0
        return 1 if self.state == RED else 2

    def as_dict(self) -> dict:
        return {
            "project_type": self.project_type,
            "state": self.state,
            "reason": self.reason,
            "seconds": round(self.seconds, 1),
            "workdir": self.workdir,
            "units": [
                {"dir": u.rel_dir, "locked": u.locked, "install": list(u.install_argv),
                 "install_ok": u.install_ok, "gate_ok": u.gate_ok,
                 "seconds": round(u.seconds, 1), "detail": u.detail}
                for u in self.units
            ],
        }


def materialize(project_type: str, dest: Path) -> Path:
    """Écrit dans `dest` le semis **exact** du type — `load_bundle(type)`, la même composition
    `base ⊕ overlay` que `registry.create_project` écrit verbatim dans le SoT d'un projet neuf.
    Lève `BundleError` si le type est hors registre (fail-closed, avant toute écriture)."""
    for rel, content in load_bundle(project_type).items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return dest


def npm_units(root: Path) -> list[Path]:
    """Les unités npm d'un semis : les dossiers portant un `package.json` avec un script `gate`.

    Découvert par **balayage du semis**, jamais codé en dur — un template qui déplace ou ajoute son
    manifeste reste couvert sans qu'on touche ici. La convention (`gate` = la toolchain du projet, dont la
    composition appartient au projet) est celle de `gate/toolchain.has_gate_script`, réutilisée telle
    quelle : deux définitions de « unité gatable » finiraient par diverger."""
    return [pkg.parent for pkg in sorted(root.rglob("package.json"))
            if "node_modules" not in pkg.parts and has_gate_script(pkg)]


def _tail(res: RunResult) -> str:
    out = (res.stderr or res.stdout or "").strip()
    return out[-_DETAIL_CHARS:]


def _verify_unit(unit: Path, root: Path, npm: str, runner: Runner, timeout: float) -> UnitVerdict:
    """Installe puis gate UNE unité. L'installation d'abord, et son échec **arrête** l'unité : lancer le
    gate sur un `node_modules` absent produirait un rouge dont on ne saurait plus s'il vient du code ou de
    l'installation."""
    rel = str(unit.relative_to(root))
    locked = (unit / "package-lock.json").is_file()
    install: tuple[str, ...] = (npm, "ci") if locked else (npm, "install")
    started = time.monotonic()
    res = runner(list(install), cwd=unit, timeout=timeout)
    if not res.ok:
        return UnitVerdict(rel, locked, install, False, None, time.monotonic() - started, _tail(res))
    gate = runner([npm, "run", "gate"], cwd=unit, timeout=timeout)
    return UnitVerdict(rel, locked, install, True, gate.ok, time.monotonic() - started,
                       "" if gate.ok else _tail(gate))


def verify_bundle(
    project_type: str,
    *,
    runner: Runner = run,
    which: Callable[[str], str | None] = shutil.which,
    timeout: float = DEFAULT_TIMEOUT_S,
    keep: bool = False,
    units_filter: Sequence[str] | None = None,
) -> BundleVerdict:
    """Sème `project_type` dans un dossier **jetable**, y installe les dépendances de chaque unité npm et
    lance son script `gate`. Démontage garanti (`finally`) sauf `keep=True` — un rouge s'inspecte.

    `units_filter` restreint aux chemins relatifs donnés (diagnostic ; par défaut, toutes les unités)."""
    npm = which("npm")
    if not npm:
        return BundleVerdict(project_type, UNMOUNTABLE,
                             reason="npm introuvable sur le PATH — la preuve ne peut pas être montée "
                                    "(installe Node ≥ 18, ou lance-la sur une machine qui l'a)")
    root = Path(mkdtemp(prefix=f"forgemaster-verify-{project_type}-"))
    kept = False
    try:
        materialize(project_type, root)
        units = npm_units(root)
        if units_filter is not None:
            wanted = set(units_filter)
            units = [u for u in units if str(u.relative_to(root)) in wanted]
        if not units:
            return BundleVerdict(project_type, NO_UNIT,
                                 reason="aucun `package.json` portant un script `gate` dans le semis — "
                                        "ce bundle n'a pas de toolchain npm à prouver")
        results = tuple(_verify_unit(u, root, npm, runner, timeout) for u in units)
        state = GREEN if all(u.ok for u in results) else RED
        kept = keep
        return BundleVerdict(project_type, state, results, workdir=str(root) if keep else "")
    finally:
        if not kept:
            shutil.rmtree(root, ignore_errors=True)
