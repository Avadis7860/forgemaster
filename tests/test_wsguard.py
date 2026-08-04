"""Tests de la garde WS (CSWSH) : cœur pur `origin_allowed` / `match_token_subprotocol` + minting du token
par-instance `ensure_ws_token`. La glue async `authorize_ws` est couverte par les tests de route
(`test_daemon.py::test_ws_*`)."""
from __future__ import annotations

import stat

from forgemaster.config import Settings
from forgemaster.daemon.ws_token import ensure_ws_token
from forgemaster.daemon.wsguard import (
    DEV_ORIGINS,
    match_token_subprotocol,
    origin_allowed,
)

# -- origin_allowed : Origin absent toléré, same-origin par comparaison à Host, dev + allowlist -----

def test_origin_absent_is_tolerated():
    """Client non-navigateur (sonde E2E, void-runner) : pas d'Origin → toléré (le token gate en aval).
    Ce n'est PAS le vecteur CSWSH (le vecteur, c'est une PAGE dans un navigateur, qui envoie un Origin)."""
    assert origin_allowed(None, "host:8700", ()) is True


def test_same_origin_matches_host_zero_config():
    """L'autorité de l'Origin == l'en-tête Host → same-origin, autorisé sans configuration (couvre l'hôte
    public réel de l'instance, LAN inclus)."""
    assert origin_allowed("http://192.168.0.59:8700", "192.168.0.59:8700", ()) is True
    assert origin_allowed("https://forgemaster.lan:8700", "forgemaster.lan:8700", ()) is True


def test_cross_origin_refused_even_when_host_present():
    """Page tierce : Origin != Host, hors dev et hors allowlist → refusé (c'est le blocage anti-CSWSH)."""
    assert origin_allowed("http://evil.example", "192.168.0.59:8700", ()) is False


def test_dev_origins_allowed():
    """Les origines Vite (:5173 → daemon :8700) sont cross-origin mais légitimes en dev."""
    for dev in DEV_ORIGINS:
        assert origin_allowed(dev, "localhost:8700", ()) is True


def test_configured_allowlist_allows_reverse_proxy_origin():
    """Cas reverse-proxy à nom public différent : une origine de `ws_allowed_origins` est autorisée."""
    assert origin_allowed("https://forgemaster.example", "internal:8700",
                          ("https://forgemaster.example",)) is True


def test_null_origin_refused():
    """Origin `null` (iframe sandbox, file://) : ni same-origin, ni listé → refusé."""
    assert origin_allowed("null", "host:8700", ()) is False


# -- match_token_subprotocol : extraction + comparaison temps-constant ------------------------------

def test_matches_correct_token_and_echoes_exact_subprotocol():
    """Un `forgemaster.token.<valeur>` offert dont la valeur == attendu → retourne le sous-protocole EXACT à
    echo à l'accept (obligation RFC : le serveur sélectionne un des offerts)."""
    assert match_token_subprotocol(["forgemaster.token.abc123"], "abc123") == "forgemaster.token.abc123"


def test_picks_token_among_several_offered():
    assert match_token_subprotocol(["chat", "forgemaster.token.xyz", "v2"], "xyz") == "forgemaster.token.xyz"


def test_wrong_token_value_rejected():
    assert match_token_subprotocol(["forgemaster.token.wrong"], "right") is None


def test_no_token_subprotocol_rejected():
    assert match_token_subprotocol(["chat", "superchat"], "abc") is None
    assert match_token_subprotocol([], "abc") is None


# -- ensure_ws_token : minte une fois, 600, idempotent ---------------------------------------------

def _settings(tmp_path) -> Settings:
    return Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")


def test_ws_token_minted_once_and_persisted_600(tmp_path):
    s = _settings(tmp_path)
    token = ensure_ws_token(s)
    assert token and isinstance(token, str)
    path = s.home / "ws_token"
    assert path.read_text(encoding="utf-8").strip() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600      # lisible du seul propriétaire


def test_ws_token_is_idempotent(tmp_path):
    s = _settings(tmp_path)
    first = ensure_ws_token(s)
    second = ensure_ws_token(s)                             # relance = même token (persisté)
    assert first == second


def test_ws_token_reads_existing_file(tmp_path):
    s = _settings(tmp_path)
    s.home.mkdir(parents=True, exist_ok=True)
    (s.home / "ws_token").write_text("preexisting-token\n", encoding="utf-8")
    assert ensure_ws_token(s) == "preexisting-token"       # strip du trailing newline
