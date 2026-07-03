"""Tests de la couche gate : chaîne d'autorité `compose_merge_decision` (matrice pure), garde
`evidence ⊂ diff` (review Tier-1), verdict feature-verified (Tier-1.5, N/A-safe + fail-closed), identité
writeback, et le cycle `run_merge` de bout en bout sur un **SoT bare réel** (ff feature→dev→main, cleanup
worktree AVANT delete-branch, port relâché, clôture DB)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.config import Settings
from cockpit.db import store
from cockpit.dispatch import ports, worktree
from cockpit.gate import merge, review, toolchain, verify
from cockpit.git.identity import resolve_identity
from cockpit.git.internal import GitOpError, InternalGit
from cockpit.projects import registry
from cockpit.roadmap import model


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)
    yield settings, conn
    conn.close()


# -- chaîne d'autorité (compose_merge_decision, PUR) ------------------------------------------------

_T1_CLEAN = {"present": True, "fresh": True, "counts": {"red": 0, "yellow": 0, "purple": 0}}


def test_compose_clean_gate_green_holds_without_go():
    d = merge.compose_merge_decision({"red": 0, "yellow": 0}, _T1_CLEAN, human_go=False)
    assert d["gate_green"] is True and d["allow"] is False and d["decision"] == "hold"


def test_compose_clean_gate_green_plus_go_allows():
    d = merge.compose_merge_decision({"red": 0, "yellow": 0}, _T1_CLEAN, human_go=True)
    assert d["allow"] is True and d["decision"] == "merge"


def test_compose_tier0_red_blocks_even_with_go():
    d = merge.compose_merge_decision({"red": 1, "yellow": 0}, _T1_CLEAN, human_go=True)
    assert d["allow"] is False and d["gate_green"] is False
    assert any("Tier-0" in b for b in d["blockers"])


def test_compose_tier1_absent_blocks():
    d = merge.compose_merge_decision({"red": 0}, {"present": False}, human_go=True)
    assert d["gate_green"] is False and any("aucune revue" in b for b in d["blockers"])


def test_compose_tier1_stale_blocks():
    stale = {"present": True, "fresh": False, "counts": {"red": 0}}
    d = merge.compose_merge_decision({"red": 0}, stale, human_go=True)
    assert d["gate_green"] is False and any("périmée" in b for b in d["blockers"])


def test_compose_tier1_red_blocks_but_human_override_lifts_it():
    t1_red = {"present": True, "fresh": True, "counts": {"red": 1}}
    blocked = merge.compose_merge_decision({"red": 0}, t1_red, human_go=True)
    assert blocked["allow"] is False
    lifted = merge.compose_merge_decision({"red": 0}, t1_red, human_go=True, t1_override="vérifié à la main")
    assert lifted["allow"] is True and lifted["t1_overridden"] is True


def test_compose_native_broken_is_not_overridable():
    nat = {"applicable": True, "ok": False, "cmd": "pnpm verify", "exit_code": 1}
    d = merge.compose_merge_decision({"red": 0}, _T1_CLEAN, human_go=True, native_status=nat,
                                     t1_override="x", t15_override="y")
    assert d["allow"] is False and any("Tier-0 natif" in b for b in d["blockers"])


def test_compose_ui_touched_requires_proof_but_na_when_not_touched():
    # UI touchée sans preuve → bloqué ; override humain lève.
    blocked = merge.compose_merge_decision({"red": 0}, _T1_CLEAN, human_go=True, ui_touched=True,
                                           t15_status={"present": False})
    assert blocked["allow"] is False
    lifted = merge.compose_merge_decision({"red": 0}, _T1_CLEAN, human_go=True, ui_touched=True,
                                          t15_status={"present": False}, t15_override="déploiement indispo")
    assert lifted["allow"] is True and lifted["t15_overridden"] is True
    # UI touchée + preuve fraîche non bloquante → autorisé sans override.
    proven = merge.compose_merge_decision({"red": 0}, _T1_CLEAN, human_go=True, ui_touched=True,
                                          t15_status={"present": True, "fresh": True, "blocking": False})
    assert proven["allow"] is True


# -- garde déterministe evidence ⊂ diff (review Tier-1, PUR verbatim) --------------------------------

_DIFF = (
    "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
    "@@ -10,2 +10,4 @@\n ctx\n+for i in range(n + 1):\n+    do_work(i)\n"
    "diff --git a/g.py b/g.py\n--- a/g.py\n+++ b/g.py\n@@ -1,1 +1,2 @@\n head\n+    helper()\n"
)
_F_OK = {"severity": "🔴", "file": "f.py", "line": 11, "evidence": "f.py:11 — for i in range(n + 1):"}
_F_ABSENT = {"severity": "🔴", "file": "f.py", "line": 99, "evidence": "f.py:99 — fabricated()"}


def test_partition_findings_keeps_citable_rejects_hallucinated():
    f_nocite = {"severity": "🟡", "file": "g.py", "line": 2, "evidence": "g.py:2"}
    f_ws = {"severity": "🟡", "file": "g.py", "line": 2, "evidence": "g.py:2 — helper()"}  # cité indenté
    kept, rej = review.partition_findings([_F_OK, _F_ABSENT, f_nocite, f_ws], _DIFF)
    reasons = {r["evidence"]: r["reject_reason"] for r in rej}
    assert _F_OK in kept and f_ws in kept               # citable + normalisation whitespace
    assert reasons["f.py:99 — fabricated()"] == "citation-absente-du-diff"
    assert reasons["g.py:2"] == "pas-de-citation"
    assert review.evidence_in_diff(_F_OK, _DIFF) and not review.evidence_in_diff(_F_ABSENT, _DIFF)


def test_build_verdict_is_pure_and_applies_guard():
    v = review.build_verdict({"findings": [_F_OK, _F_ABSENT]}, sha="cafe",
                             ts="2026-07-02T00:00:00+00:00", diff_text=_DIFF)
    assert v["reviewed_sha"] == "cafe" and v["contract_version"] == "review-gate-v2"
    assert v["counts"]["red"] == 1 and len(v["rejected"]) == 1   # halluciné écarté des counts


# -- review : état sous config, clé (projet, feature) -----------------------------------------------

def test_review_write_read_fresh_status(ctx):
    settings, _ = ctx
    v = review.write_verdict(settings, "proj", "feat", {"findings": []}, sha="abc", diff_text="")
    assert v["counts"]["red"] == 0
    assert review.state_path(settings, "proj", "feat") == \
        settings.home / "gate" / "proj" / "feat" / "review.json"
    assert review.read_verdict(settings, "proj", "feat")["reviewed_sha"] == "abc"
    assert review.is_fresh(v, current_sha="abc") and not review.is_fresh(v, current_sha="def")
    st = review.status(settings, "proj", "feat", current_sha="abc")
    assert st["present"] and st["fresh"] and not st["blocking"]
    assert review.status(settings, "proj", "feat", current_sha="def")["fresh"] is False


# -- verify : has_ui, verdict, N/A-safe, fail-closed ------------------------------------------------

def test_verify_has_ui():
    assert verify.has_ui(["web/src/App.tsx"]) and verify.has_ui(["x/src/pages/home.js"])
    assert not verify.has_ui(["lib/core.py", "README.md"])


def test_verify_build_verdict_never_blanched_on_empty():
    assert verify.build_verdict([], sha="s", ts="t")["ok"] is False       # 0 cible → pas de blanchiment
    green = verify.build_verdict([{"ok": True}], sha="s", ts="t")
    red = verify.build_verdict([{"ok": True}, {"ok": False}], sha="s", ts="t")
    assert green["ok"] is True and verify.gate_blocking(green) is False
    assert red["ok"] is False and red["n_failed"] == 1 and verify.gate_blocking(red) is True
    assert verify.gate_blocking(None) is False


def test_verify_target_fails_closed_without_runner(ctx):
    settings, _ = ctx                                    # aucun runner sous <home>/runners, pas d'env
    res = verify.verify_target(settings, "http://x/", ["Accueil"], name="home")
    assert res["ok"] is False and "runner absent" in res["error"]


# -- identité writeback (PUR) -----------------------------------------------------------------------

def test_resolve_identity():
    assert resolve_identity("proj", "dev") == ("proj-dev-writeback", "proj-dev-writeback@worker.local")
    assert resolve_identity("proj", "dev", role="worker") == (
        "proj-dev-worker", "proj-dev-worker@worker.local")
    assert resolve_identity(None, None, role="worker") == ("worker-dev", "dev@worker.local")
    with pytest.raises(ValueError):
        resolve_identity("Bad Slug", "dev")


# -- run_merge de bout en bout (SoT bare réel) ------------------------------------------------------

def _green_toolchain(settings, sha, *, group="backend"):
    """Écrit un verdict Tier-0-natif VERT SHA-bound (1 step ok) — évite que le diff seedé (qui touche une
    toolchain) ne bloque le merge par « toolchain non exécutée »."""
    toolchain.write_verdict(settings, "proj", "feat",
                            [{"group": group, "name": "ruff", "cmd": "ruff check .",
                              "exit_code": 0, "ok": True}], sha=sha)


def _seed_committed_feature(conn, settings, git, *, red_finding=False, stale=False, filename="core.py",
                            content="value = 1\n", toolchain_ok=True):
    """Projet + feature + task in_progress, worktree réservé avec un commit worker, verdict Tier-1 écrit
    (propre, ou 🔴, ou périmé) et — par défaut — un verdict Tier-0-natif vert frais (`toolchain_ok`).
    Retourne (sot, head_sha)."""
    registry.create_project(conn, settings, slug="proj")
    model.add_feature(conn, project_slug="proj", slug="feat")
    model.add_task(conn, feature_ref="proj/feat", slug="schema")
    conn.execute("UPDATE tasks SET status = 'in_progress' WHERE slug = 'schema'")
    conn.commit()
    res = worktree.reserve(conn, settings, git, project="proj", feature="feat", probe=None)
    target = res["path"] / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git.commit_worktree(res["path"], message="feat: work",
                        identity=resolve_identity("proj", "dev", role="worker"))
    sot = registry.sot_path_for(settings, "proj")
    head_sha = git.feature_sha(sot, "feature/feat")
    diff_text = git.diff_text(sot, base="dev", head="feature/feat")
    ev = f"{filename}:1 — {content.strip()}"
    findings = ([{"severity": "🔴", "file": filename, "line": 1, "evidence": ev}] if red_finding else [])
    review.write_verdict(settings, "proj", "feat", {"findings": findings},
                         sha="stale-sha" if stale else head_sha, diff_text=diff_text)
    if toolchain_ok:
        _green_toolchain(settings, head_sha)
    return sot, head_sha


def test_run_merge_holds_without_go_then_merges_and_cleans_up(ctx):
    settings, conn = ctx
    git = InternalGit()
    sot, head_sha = _seed_committed_feature(conn, settings, git)

    hold = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=False, git=git)
    assert hold["merged"] is False and hold["decision"]["gate_green"] is True
    assert git.feature_sha(sot, "dev") != head_sha       # AUCUNE mutation sans go

    done = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=True, git=git)
    assert done["merged"] is True and done["merge_sha"] == head_sha
    assert git.feature_sha(sot, "dev") == head_sha and git.feature_sha(sot, "main") == head_sha  # ff dev+main
    assert not (settings.projects_root / "proj" / "worktrees" / "feat").exists()          # worktree retiré
    with pytest.raises(GitOpError):
        git.feature_sha(sot, "feature/feat")             # branche supprimée APRÈS le worktree
    assert ports.list_reservations(conn) == [] and worktree.audit(conn, settings) == []          # 0 orphelin
    feat = conn.execute("SELECT status FROM features WHERE slug = 'feat'").fetchone()
    task = conn.execute("SELECT status FROM tasks WHERE slug = 'schema'").fetchone()
    assert feat["status"] == "merged" and task["status"] == "done" and done["closed_tasks"] == ["schema"]


def test_run_merge_passes_project_credential_ref_to_writeback(ctx):
    """run_merge lit le `credential_ref` du projet et le passe au writeback (résolu à l'usage). Prouve le
    câblage bout-en-bout : réf opaque en DB → argument de `merge_writeback`, jamais un token en DB."""
    settings, conn = ctx
    seen: dict[str, str | None] = {}

    class RecordingGit(InternalGit):
        def merge_writeback(self, sot, *, creds_ref, identity):   # type: ignore[override]
            seen["creds_ref"] = creds_ref
            return super().merge_writeback(sot, creds_ref=creds_ref, identity=identity)

    git = RecordingGit()
    _seed_committed_feature(conn, settings, git)
    registry.set_credential_ref(conn, "proj", "ref-live")
    done = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=True, git=git)
    assert done["merged"] is True
    assert seen["creds_ref"] == "ref-live"           # la réf DB atteint le writeback (0 token en DB)


def test_run_merge_blocked_by_red_review_mutates_nothing(ctx):
    settings, conn = ctx
    git = InternalGit()
    sot, head_sha = _seed_committed_feature(conn, settings, git, red_finding=True)
    rep = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=True, git=git)
    assert rep["merged"] is False and rep["decision"]["gate_green"] is False
    assert "gate rouge" in rep["reason"]
    assert git.feature_sha(sot, "dev") != head_sha and git.feature_sha(sot, "feature/feat") == head_sha
    assert (settings.projects_root / "proj" / "worktrees" / "feat").exists()   # worktree intact


def test_run_merge_holds_on_stale_review(ctx):
    settings, conn = ctx
    git = InternalGit()
    sot, head_sha = _seed_committed_feature(conn, settings, git, stale=True)
    rep = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=True, git=git)
    assert rep["merged"] is False and any("périmée" in b for b in rep["decision"]["blockers"])
    assert git.feature_sha(sot, "dev") != head_sha       # rien mergé


# -- Tier-0 natif : toolchain (gate/toolchain) ------------------------------------------------------

def test_toolchain_detect_groups_by_convention(tmp_path):
    root = tmp_path / "wt"
    (root / "web").mkdir(parents=True)
    assert toolchain.detect_groups(root) == []                       # ni pyproject ni script gate
    (root / "web" / "package.json").write_text('{"scripts": {"gate": "x"}}', encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert toolchain.detect_groups(root) == ["front", "backend"]
    (root / "web" / "package.json").write_text('{"scripts": {"build": "x"}}', encoding="utf-8")
    assert toolchain.detect_groups(root) == ["backend"]              # pas de script `gate` → pas de front


def test_toolchain_applicable_triggers_from_diff():
    assert toolchain.applicable_triggers(["web/src/App.tsx"]) == ["front"]
    assert toolchain.applicable_triggers(["src/cockpit/x.py"]) == ["backend"]
    assert toolchain.applicable_triggers(["web/vite.config.ts", "src/x.py"]) == ["front", "backend"]
    assert toolchain.applicable_triggers(["README.md", "docs/x.rst"]) == []


def test_toolchain_build_verdict_ok_and_failed_step():
    green = toolchain.build_verdict([{"name": "ruff", "ok": True}, {"name": "mypy", "ok": True}],
                                    sha="abc", ts="t")
    assert green["ok"] is True and green["failed_step"] is None
    red = toolchain.build_verdict([{"name": "ruff", "ok": True}, {"name": "vitest", "ok": False}],
                                  sha="abc", ts="t")
    assert red["ok"] is False and red["failed_step"] == "vitest"
    empty = toolchain.build_verdict([], sha="abc", ts="t")            # 0 step = vacuously vert
    assert empty["ok"] is True and empty["failed_step"] is None


def test_toolchain_status_na_fresh_absent_stale(ctx):
    settings, _ = ctx
    # non applicable (diff ne touche aucune toolchain) → N/A
    assert toolchain.status(settings, "p", "f", current_sha="s1", diff_files=["README.md"]) == {
        "applicable": False}
    front = ["web/x.ts"]
    # applicable mais aucun verdict → bloque (non exécutée)
    absent = toolchain.status(settings, "p", "f", current_sha="s1", diff_files=front)
    assert absent["applicable"] and absent["ok"] is False and "non exécutée" in absent["failed_step"]
    # verdict frais + vert → ok
    toolchain.write_verdict(settings, "p", "f", [{"name": "npm-run-gate", "ok": True}], sha="s1")
    ok = toolchain.status(settings, "p", "f", current_sha="s1", diff_files=front)
    assert ok["applicable"] and ok["ok"] is True
    # même verdict mais HEAD a bougé → périmé → bloque
    stale = toolchain.status(settings, "p", "f", current_sha="s2", diff_files=front)
    assert stale["ok"] is False and "périmé" in stale["failed_step"]


def test_toolchain_status_fresh_red_reports_failed_step(ctx):
    settings, _ = ctx
    toolchain.write_verdict(settings, "p", "f",
                            [{"name": "npm-run-gate", "ok": False, "exit_code": 1}], sha="s1")
    st = toolchain.status(settings, "p", "f", current_sha="s1", diff_files=["web/x.ts"])
    assert st["ok"] is False and st["failed_step"] == "npm-run-gate" and st["exit_code"] == 1


def test_run_toolchain_skips_when_no_group_present(tmp_path):
    """Diff qui déclenche `backend` mais projet SANS `pyproject.toml` (groupe absent) → aucun step, pas de
    subprocess, ne lève pas. (Le RUN croise déclencheurs ET groupes présents.)"""
    root = tmp_path / "wt"
    root.mkdir()
    assert toolchain.run_toolchain(root, ["x.py"], timeout_s=5) == []


def test_run_toolchain_fails_closed_on_missing_binary(tmp_path, monkeypatch):
    """Un binaire introuvable → step rouge (`ok=False`, `error` renseigné), JAMAIS une exception."""
    root = tmp_path / "wt"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(toolchain, "_steps_for",
                        lambda group, wt: [{"name": "ruff", "argv": ["cockpit-no-such-bin-xyz"], "cwd": wt}])
    results = toolchain.run_toolchain(root, ["x.py"], timeout_s=5)
    assert len(results) == 1 and results[0]["ok"] is False and results[0].get("error")


# -- intégration avec compose_merge_decision (les formes de status nourrissent le natif) -------------

def test_compose_native_from_toolchain_absent_blocks_even_with_go():
    nat = {"applicable": True, "ok": False, "failed_step": "toolchain non exécutée sur ce HEAD",
           "cmd": "front", "exit_code": ""}
    d = merge.compose_merge_decision({"red": 0}, _T1_CLEAN, human_go=True, native_status=nat)
    assert d["allow"] is False and any("natif" in b for b in d["blockers"])


def test_compose_native_from_toolchain_green_passes():
    nat = {"applicable": True, "ok": True, "cmd": "front"}
    d = merge.compose_merge_decision({"red": 0}, _T1_CLEAN, human_go=True, native_status=nat)
    assert d["allow"] is True


def test_run_merge_front_change_blocked_without_toolchain_verdict(ctx):
    """Une feature qui touche web/ (mais pas une surface UI) ne merge PAS sans verdict toolchain frais+vert :
    referme « vitest hors gate »."""
    settings, conn = ctx
    git = InternalGit()
    sot, head_sha = _seed_committed_feature(conn, settings, git, filename="web/vite.config.ts",
                                            content="export default {}\n", toolchain_ok=False)
    rep = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=True, git=git)
    assert rep["merged"] is False and rep["decision"]["gate_green"] is False
    assert any("natif" in b and "NON-overridable" in b for b in rep["decision"]["blockers"])
    assert git.feature_sha(sot, "dev") != head_sha            # rien mergé

    toolchain.write_verdict(settings, "proj", "feat", [{"name": "npm-run-gate", "ok": True}], sha=head_sha)
    done = merge.run_merge(conn, settings, feature_ref="proj/feat", human_go=True, git=git)
    assert done["merged"] is True and git.feature_sha(sot, "dev") == head_sha
