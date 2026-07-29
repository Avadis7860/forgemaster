"""Dimension de gate type-agnostique : un artefact servi ne doit jamais advertise une autorité absolue ≠ celle
atteinte. Repro-driven — le cas racine (nginx `absolute_redirect on` fuit le port interne 8000) est le premier
test : la dimension l'aurait attrapé en 🔴 AVANT le fix, verte APRÈS (`off` → Location relatif)."""
from __future__ import annotations

from cockpit.gate import advertised_authority as aa

# -- authority() : normalisation PUR ---------------------------------------------------------------

def test_authority_relative_url_is_none():
    assert aa.authority("/design-system/") is None            # relatif = aucune autorité advertisée (bon cas)


def test_authority_explicits_default_port_from_scheme():
    assert aa.authority("http://127.0.0.1/x") == ("127.0.0.1", "80")
    assert aa.authority("https://example.com/x") == ("example.com", "443")


def test_authority_reads_explicit_port_and_lowercases_host():
    assert aa.authority("http://127.0.0.1:8000/x/") == ("127.0.0.1", "8000")
    assert aa.authority("http://LocalHost:5251/") == ("localhost", "5251")


# -- analyze_location() : cœur positif-par-catégorie -----------------------------------------------

def test_repro_nginx_internal_port_leak_is_flagged():
    """LE cas racine : requête sur le port publié 5251, redirect vers le port INTERNE 8000 → fuite."""
    f = aa.analyze_location("http://127.0.0.1:5251/design-system", 301,
                            "http://127.0.0.1:8000/design-system/")
    assert f is not None
    assert f["kind"] == "cross_authority_redirect"
    assert f["advertised"] == "127.0.0.1:8000"
    assert f["reached"] == "127.0.0.1:5251"


def test_fix_relative_location_is_clean():
    """APRÈS `absolute_redirect off` : le 301 émet un Location RELATIF → aucune fuite."""
    assert aa.analyze_location("http://127.0.0.1:5251/design-system", 301, "/design-system/") is None


def test_invented_absolute_host_is_flagged_not_only_loopback():
    """Positif par catégorie (pas de denylist) : un host inventé ≠ atteint échoue AUSSI — l'évasion
    `128.0.0.X` / hostname que bosse redoutait est couverte."""
    assert aa.analyze_location("http://127.0.0.1:5251/x", 302, "http://128.0.0.5/x/") is not None
    assert aa.analyze_location("http://127.0.0.1:5251/x", 302, "http://evil.example/x/") is not None


def test_same_authority_self_redirect_is_legit():
    """Un self-redirect canonical (même host:port) est légitime → pas de finding."""
    assert aa.analyze_location("http://127.0.0.1:5251/x", 301, "http://127.0.0.1:5251/x/") is None


def test_implicit_vs_explicit_default_port_equal():
    """`:80` implicite (schéma) == `:80` explicite → pas de faux-positif sur un port par défaut."""
    assert aa.analyze_location("http://host/x", 301, "http://host:80/x/") is None


def test_non_3xx_is_ignored():
    assert aa.analyze_location("http://127.0.0.1:5251/x", 200, None) is None
    assert aa.analyze_location("http://127.0.0.1:5251/x", 404, "http://127.0.0.1:8000/x/") is None


def test_3xx_without_location_is_ignored():
    assert aa.analyze_location("http://127.0.0.1:5251/x", 301, None) is None


# -- check_served_authority() : orchestration avec sonde injectée ----------------------------------

def test_check_served_probes_slashless_variant_and_flags_leak():
    """La requête SANS slash final déclenche le 301 de dossier fui : la sonde doit couvrir `/design-system`
    même quand le contrat déclare `/design-system/`."""
    seen: list[str] = []

    def fake_probe(url: str) -> tuple[int, str | None]:
        seen.append(url)
        if url.endswith("/design-system"):               # la variante sans slash → 301 fuité
            return (301, "http://127.0.0.1:8000/design-system/")
        return (200, None)

    leaks = aa.check_served_authority("http://127.0.0.1:5251/", ["/design-system/", "/"], probe=fake_probe)
    assert "http://127.0.0.1:5251/design-system" in seen  # variante sans slash sondée
    assert len(leaks) == 1
    assert leaks[0]["advertised"] == "127.0.0.1:8000"


def test_check_served_clean_site_has_no_findings():
    """Un site sain (relatif / 200 partout) → aucun finding."""
    def clean_probe(url: str) -> tuple[int, str | None]:
        if url.rstrip("/").endswith(("/design-system", "/x")):
            return (301, "/design-system/")              # redirect RELATIF = conforme
        return (200, None)

    out = aa.check_served_authority("http://127.0.0.1:5251", ["/design-system/", "/"], probe=clean_probe)
    assert out == []


def test_check_served_network_error_fails_open():
    """Sonde échoue-ouvert (réseau ko → (0, None)) : aucun finding (readiness gardée en amont)."""
    assert aa.check_served_authority("http://127.0.0.1:5251", ["/"], probe=lambda _u: (0, None)) == []
