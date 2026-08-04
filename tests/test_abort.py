"""Tests de l'abort de run (`forgemaster abort` / bouton UI « Arrêter le run »). L'arrêt cible chaque worker
par
son **pgid persisté** (handle explicite posé au spawn — fin du `pgrep`/`kill -0` fragile), le job passe
`killed`, la task revient `todo` (re-runnable), et une **sentinelle** joue le signal cross-process que la
boucle de drain lit pour s'arrêter. Killer **INJECTÉ** → aucun vrai process n'est tué."""
from __future__ import annotations

import signal

import pytest

from forgemaster.config import Settings
from forgemaster.db import store
from forgemaster.dispatch import abort, jobs
from forgemaster.projects import registry
from forgemaster.roadmap import model


@pytest.fixture
def ctx(tmp_path):
    settings = Settings.resolve(home=tmp_path / "home", projects_root=tmp_path / "projects")
    conn = store.open_db(settings)          # base FICHIER (abort ouvre au besoin sa propre connexion)
    yield settings, conn
    conn.close()


class _RecordingKiller:
    """Killer injecté : enregistre `(pgid, signal)` au lieu de tuer un vrai groupe de process."""
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, pgid: int, sig: int) -> None:
        self.calls.append((pgid, sig))


def _seed_running_job(conn, settings, *, project="proj", feature="feat", task="t", pid=424242):
    """Un job `running` avec son pgid persisté (comme après un vrai spawn) + sa task `in_progress`."""
    registry.create_project(conn, settings, slug=project)
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")           # board contrôlé (pas la roadmap de lancement semée)
    conn.commit()
    model.add_feature(conn, project_slug=project, slug=feature)
    t = model.add_task(conn, feature_ref=f"{project}/{feature}", slug=task)
    conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (t["id"],))
    conn.commit()
    job_id = jobs.record_start(conn, task_id=t["id"], worktree="/tmp/wt", session_id="s1")
    jobs.record_pid(conn, job_id, pid)
    return job_id, t["id"]


def test_request_abort_kills_worker_and_requeues(ctx):
    settings, conn = ctx
    job_id, task_id = _seed_running_job(conn, settings, pid=424242)
    killer = _RecordingKiller()
    result = abort.request_abort(settings, project="proj", killer=killer, grace_s=0, conn=conn)
    # tué EN GROUPE par son pgid persisté : SIGTERM (grâce) puis SIGKILL — jamais un pgrep aveugle
    assert (424242, signal.SIGTERM) in killer.calls
    assert (424242, signal.SIGKILL) in killer.calls
    # job → killed + intention humaine tracée ; task → todo (re-runnable)
    row = jobs.get_job(conn, job_id)
    assert row["status"] == "killed"
    assert row["error"] == "aborted by human"
    assert conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"] == "todo"
    assert result["aborted"] == 1
    # sentinelle posée → la boucle de drain s'arrêtera à sa prochaine passe
    assert abort.abort_requested(settings, "proj")


def test_abort_finalizes_completed_worker_as_done_not_aborted(ctx):
    """Course `ProcessLookupError` : un worker qui a FINI (émis son `result`) juste avant que le kill ne
    l'atteigne ne doit pas être étiqueté `killed`/« aborted by human ». `mark_job_orphan` le finalise `done`
    depuis son transcript ; l'abort ne pose PAS le marqueur humain sur un run abouti."""
    settings, conn = ctx
    job_id, task_id = _seed_running_job(conn, settings)
    log_path = jobs.dispatch_log_path(settings, "s1")      # le worker a terminé : verdict de succès au log
    log_path.write_text('{"type":"result","is_error":false,"result":"ok","session_id":"s1",'
                        '"num_turns":7,"total_cost_usd":0.4,"duration_ms":90000}\n', encoding="utf-8")
    conn.execute("UPDATE dispatch_jobs SET log_path=? WHERE id=?", (str(log_path), job_id))
    conn.commit()
    abort.request_abort(settings, project="proj", killer=_RecordingKiller(), grace_s=0, conn=conn)
    row = jobs.get_job(conn, job_id)
    assert row["status"] == "done"                          # finalisé depuis le résultat, pas killed
    assert row["error"] is None                             # jamais « aborted by human » sur un run abouti
    assert row["num_turns"] == 7 and row["cost_usd"] == 0.4


def test_request_abort_idempotent(ctx):
    settings, conn = ctx
    _seed_running_job(conn, settings)
    killer = _RecordingKiller()
    abort.request_abort(settings, project="proj", killer=killer, grace_s=0, conn=conn)
    result2 = abort.request_abort(settings, project="proj", killer=killer, grace_s=0, conn=conn)
    assert result2["aborted"] == 0        # plus aucun job `running` → rien à tuer (re-appel sûr)


def test_abort_requested_clear_roundtrip(ctx):
    settings, _ = ctx
    assert not abort.abort_requested(settings, "proj")
    abort.request_abort(settings, project="proj", killer=_RecordingKiller(), grace_s=0)  # pose la sentinelle
    assert abort.abort_requested(settings, "proj")
    abort.clear_abort(settings, "proj")
    assert not abort.abort_requested(settings, "proj")


def test_request_abort_is_feature_scoped(ctx):
    settings, conn = ctx
    registry.create_project(conn, settings, slug="proj")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM features")
    conn.commit()
    for feat, pid in (("fa", 111), ("fb", 222)):
        model.add_feature(conn, project_slug="proj", slug=feat)
        t = model.add_task(conn, feature_ref=f"proj/{feat}", slug="t")
        conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (t["id"],))
        conn.commit()
        jid = jobs.record_start(conn, task_id=t["id"], worktree="/tmp/wt", session_id=f"s-{feat}")
        jobs.record_pid(conn, jid, pid)
    killer = _RecordingKiller()
    abort.request_abort(settings, project="proj", feature="fa", killer=killer, grace_s=0, conn=conn)
    assert {pg for pg, _ in killer.calls} == {111}    # seul le worker de `fa` tué ; `fb` intact
