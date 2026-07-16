"""conftest partagé — fixtures transverses aux tests.

`fake_tools` simule un hôte **provisionné** (`cockpit tools install` a tourné) : de faux exécutables pour
chaque binaire du framework sous `tools_bin(settings)`, afin que le **preflight de dispatch** (P1 : tout
`allowedTools` de la facette doit résoudre sur le PATH du worker) passe dans les tests qui injectent un
runner fake — aucun vrai outil n'est lancé, seule leur RÉSOLUTION (`shutil.which`) compte.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

from cockpit.config import Settings
from cockpit.tools import _NODE_BINS, _VENV_BINS, tools_bin


@pytest.fixture
def fake_tools() -> Callable[[Settings], None]:
    def _seed(settings: Settings) -> None:
        bindir = tools_bin(settings)
        bindir.mkdir(parents=True, exist_ok=True)
        for name in (*_VENV_BINS, *_NODE_BINS, "claude"):   # `claude` = moteur du worker (résolu au dispatch)
            exe = bindir / name
            exe.write_text("#!/bin/sh\n:\n")
            exe.chmod(0o755)
    return _seed
