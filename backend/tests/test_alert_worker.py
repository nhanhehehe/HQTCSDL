import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.modules.alerts import worker as worker_mod
from app.modules.alerts.timeout_engine import TimeoutStateError, build_evaluation_view
from app.modules.alerts.worker import MonitoringWorker

BASE = datetime(2026, 11, 20, 12, 0, 0)


def _settings(**overrides):
    defaults = {
        "_env_file": None,
        "env": "test",
        "monitoring_enabled": True,
        "monitoring_tick_seconds": 5,
        "monitoring_retry_seconds": 15,
        "monitoring_shutdown_timeout_seconds": 30,
        "monitoring_command_timeout_seconds": 15,
        "monitoring_lock_timeout_ms": 5000,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class _FakeConn:
    def __init__(self, store: "_FakeWorkerStore"):
        self.store = store
        self.closed = False
        self.invalidated = False
        self._in_tx = False
        self.connection = SimpleNamespace(driver_connection=SimpleNamespace(timeout=0))

    def execution_options(self, **_kwargs):
        return self

    def in_transaction(self):
        return self._in_tx

    def execute(self, *_args, **_kwargs):
        return None

    @contextmanager
    def begin(self):
        assert not self._in_tx
        self._in_tx = True
        try:
            yield self
        except Exception:
            self._in_tx = False
            raise
        else:
            self._in_tx = False

    def rollback(self):
        self._in_tx = False

    def close(self):
        self.closed = True

    def invalidate(self):
        self.invalidated = True


class _FakeSession:
    def __init__(self, conn: _FakeConn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeWorkerStore:
    def __init__(self):
        self.now = BASE
        self.mono = 1000.0
        self.schema_ready = True
        self.applock_rc = 0
        self.applock_mode = "Exclusive"
        self.release_calls = 0
        self.node_ids = [101]
        self.eval_states: dict[int, dict] = {
            101: {
                "node_id": 101,
                "desired_generation": 1,
                "completed_generation": 0,
                "replay_from_t_node": 100,
                "full_replay_required": True,
                "last_success_at": None,
                "last_error_code": None,
                "retry_after": None,
                "last_threshold_measurement_id": None,
                "last_threshold_event_at": None,
                "threshold_checkpoint_json": None,
                "timeout_last_error_code": None,
                "timeout_retry_after": None,
            }
        }
        self.threshold_behavior: dict[int, Exception | str] = {}
        self.timeout_behavior: dict[int, Exception | str] = {}
        self.calls: list[tuple[str, int]] = []
        self.on_threshold_enter = None
        self.on_timeout_enter = None

    def connect(self):
        return _FakeConn(self)


def _patch_worker_env(monkeypatch, store: _FakeWorkerStore):
    monkeypatch.setattr(worker_mod, "Session", lambda bind: _FakeSession(bind))
    monkeypatch.setattr(worker_mod.repo, "check_m3_schema_ready", lambda _c: store.schema_ready)
    monkeypatch.setattr(worker_mod.repo, "acquire_worker_applock", lambda _c, _r: store.applock_rc)
    monkeypatch.setattr(
        worker_mod.repo, "check_worker_applock_mode", lambda _c, _r: store.applock_mode
    )

    def _release(_c, _r):
        store.release_calls += 1
        return 0

    monkeypatch.setattr(worker_mod.repo, "release_worker_applock", _release)
    monkeypatch.setattr(worker_mod.repo, "list_all_node_ids", lambda _s: list(store.node_ids))
    monkeypatch.setattr(
        worker_mod.repo,
        "get_evaluation_state",
        lambda _s, nid: dict(store.eval_states[nid]) if nid in store.eval_states else None,
    )
    monkeypatch.setattr(
        worker_mod.repo,
        "lock_evaluation_state",
        lambda _s, nid: dict(store.eval_states[nid]) if nid in store.eval_states else None,
    )
    monkeypatch.setattr(worker_mod.repo, "get_db_utc_now", lambda _s: store.now)

    def _run_th(_s, *, node_id):
        store.calls.append(("THRESHOLD", node_id))
        if store.on_threshold_enter is not None:
            store.on_threshold_enter(node_id)
        beh = store.threshold_behavior.get(node_id)
        if isinstance(beh, Exception):
            raise beh
        st = store.eval_states[node_id]
        st["last_error_code"] = None
        st["retry_after"] = None

    def _run_to(_s, *, node_id, settings):
        store.calls.append(("TIMEOUT", node_id))
        if store.on_timeout_enter is not None:
            store.on_timeout_enter(node_id)
        beh = store.timeout_behavior.get(node_id)
        if isinstance(beh, Exception):
            raise beh

    def _clear_to_err(_s, *, node_id):
        st = store.eval_states[node_id]
        st["timeout_last_error_code"] = None
        st["timeout_retry_after"] = None
        return 1

    def _record_err(_s, *, node_id, engine_kind, error_code, retry_after, failed_at):
        st = store.eval_states[node_id]
        if st["last_success_at"] is not None and st["last_success_at"] >= failed_at:
            return False
        if engine_kind == "THRESHOLD":
            st["last_error_code"] = error_code
            st["retry_after"] = retry_after
        else:
            st["timeout_last_error_code"] = error_code
            st["timeout_retry_after"] = retry_after
        return True

    def _finalize(_s, *, node_id, target_generation, completed_at):
        st = store.eval_states[node_id]
        if (
            int(st["desired_generation"]) != int(target_generation)
            or st["last_error_code"] is not None
            or st["timeout_last_error_code"] is not None
        ):
            return False
        st["completed_generation"] = target_generation
        st["full_replay_required"] = False
        st["replay_from_t_node"] = None
        st["last_success_at"] = completed_at
        return True

    monkeypatch.setattr(worker_mod, "evaluate_threshold_node", _run_th)
    monkeypatch.setattr(worker_mod, "evaluate_timeout_node", _run_to)
    monkeypatch.setattr(worker_mod.repo, "clear_timeout_error", _clear_to_err)
    monkeypatch.setattr(worker_mod.repo, "record_engine_error", _record_err)
    monkeypatch.setattr(worker_mod.repo, "finalize_node_evaluation", _finalize)


def test_monitoring_settings_validation():
    for field in (
        "monitoring_tick_seconds",
        "monitoring_retry_seconds",
        "monitoring_shutdown_timeout_seconds",
        "monitoring_command_timeout_seconds",
        "monitoring_lock_timeout_ms",
    ):
        for bad in (0, -1, True, "abc"):
            with pytest.raises(ValidationError):
                _settings(**{field: bad})


def test_independent_engine_retry_timeout_error_when_desired_equals_completed(monkeypatch):
    store = _FakeWorkerStore()
    _patch_worker_env(monkeypatch, store)
    db = SimpleNamespace(engine=SimpleNamespace(connect=store.connect))
    w = MonitoringWorker(db, _settings(), monotonic_fn=lambda: store.mono)

    # Tick 1: THRESHOLD succeeds, TIMEOUT fails -> TIMEOUT error recorded, markers NOT cleared!
    store.timeout_behavior[101] = TimeoutStateError("TIMEOUT_STATE_INCONSISTENT", "bad period")
    stats1 = w.run_tick()
    assert stats1.executed and stats1.threshold_runs == 1 and stats1.timeout_runs == 1
    assert stats1.finalized == 0
    st = store.eval_states[101]
    assert st["last_error_code"] is None
    assert st["timeout_last_error_code"] == "TIMEOUT_STATE_INCONSISTENT"
    assert st["timeout_retry_after"] == BASE + timedelta(seconds=15)
    assert st["completed_generation"] == 0
    assert st["full_replay_required"] is True
    assert st["last_success_at"] is None
    assert build_evaluation_view(st)["status"] == "ERROR"

    # Tick 2 (+5s): New generation arrives while TIMEOUT is still in backoff!
    # THRESHOLD runs and succeeds, TIMEOUT is skipped -> timeout error stays, markers NOT cleared!
    store.now = BASE + timedelta(seconds=5)
    st["desired_generation"] = 2
    st["full_replay_required"] = True
    store.calls.clear()
    stats2 = w.run_tick()
    assert store.calls == [("THRESHOLD", 101)]
    assert stats2.finalized == 0
    assert st["timeout_last_error_code"] == "TIMEOUT_STATE_INCONSISTENT"
    assert st["completed_generation"] == 0
    assert st["full_replay_required"] is True

    # Tick 3 (+16s): TIMEOUT backoff expires and succeeds -> both engines healthy -> finalized!
    store.now = BASE + timedelta(seconds=16)
    store.timeout_behavior.pop(101)
    store.calls.clear()
    stats3 = w.run_tick()
    assert store.calls == [("THRESHOLD", 101), ("TIMEOUT", 101)]
    assert stats3.finalized == 1
    assert st["completed_generation"] == 2
    assert st["full_replay_required"] is False
    assert st["replay_from_t_node"] is None
    assert st["timeout_last_error_code"] is None
    assert st["last_success_at"] == store.now
    assert build_evaluation_view(st)["status"] == "UP_TO_DATE"

    # Tick 4 (+21s): desired == completed == 2, TIMEOUT fails -> ERROR persists even at same gen!
    store.now = BASE + timedelta(seconds=21)
    store.timeout_behavior[101] = TimeoutStateError("TIMEOUT_CLOCK_INVALID", "clock skew")
    stats4 = w.run_tick()
    assert stats4.finalized == 0
    assert st["timeout_last_error_code"] == "TIMEOUT_CLOCK_INVALID"
    assert st["last_success_at"] == BASE + timedelta(seconds=16)
    assert build_evaluation_view(st)["status"] == "ERROR"

    # Tick 5 (+37s): TIMEOUT recovers while desired == completed == 2 -> last_success_at updates!
    store.now = BASE + timedelta(seconds=37)
    store.timeout_behavior.pop(101)
    stats5 = w.run_tick()
    assert stats5.finalized == 1
    assert st["timeout_last_error_code"] is None
    assert st["last_success_at"] == store.now
    assert build_evaluation_view(st)["status"] == "UP_TO_DATE"


def test_skipped_threshold_backoff_blocks_completion_and_concurrent_gen_bump_keeps_markers(
    monkeypatch,
):
    store = _FakeWorkerStore()
    _patch_worker_env(monkeypatch, store)
    db = SimpleNamespace(engine=SimpleNamespace(connect=store.connect))
    w = MonitoringWorker(db, _settings(), monotonic_fn=lambda: store.mono)

    # THRESHOLD fails on Tick 1; TIMEOUT succeeds.
    store.threshold_behavior[101] = RuntimeError("db glitch")
    w.run_tick()
    st = store.eval_states[101]
    assert st["last_error_code"] == "THRESHOLD_EVALUATION_FAILED"
    assert st["completed_generation"] == 0

    # Tick 2 (+5s): THRESHOLD is in backoff (skipped), TIMEOUT runs -> must NOT finalize!
    store.now = BASE + timedelta(seconds=5)
    store.calls.clear()
    stats2 = w.run_tick()
    assert store.calls == [("TIMEOUT", 101)]
    assert stats2.finalized == 0
    assert st["completed_generation"] == 0
    assert st["full_replay_required"] is True

    # Tick 3 (+16s): THRESHOLD succeeds, but desired_generation bumps during TIMEOUT!
    # Finalize must NOT clear markers or set completed_generation = 1 -> 2.
    store.now = BASE + timedelta(seconds=16)
    store.threshold_behavior.pop(101)
    store.on_timeout_enter = lambda nid: store.eval_states[nid].__setitem__("desired_generation", 2)
    stats3 = w.run_tick()
    assert stats3.finalized == 0
    assert st["completed_generation"] == 0
    assert st["full_replay_required"] is True


def test_node_isolation_and_error_never_overwrites_newer_success(monkeypatch):
    store = _FakeWorkerStore()
    store.node_ids = [101, 102]
    store.eval_states[102] = {**store.eval_states[101], "node_id": 102}
    _patch_worker_env(monkeypatch, store)
    db = SimpleNamespace(engine=SimpleNamespace(connect=store.connect))
    w = MonitoringWorker(db, _settings(), monotonic_fn=lambda: store.mono)

    store.threshold_behavior[101] = RuntimeError("node 101 failed")
    stats = w.run_tick()
    assert stats.nodes_scanned == 2
    assert stats.finalized == 1
    assert store.eval_states[101]["completed_generation"] == 0
    assert store.eval_states[102]["completed_generation"] == 1

    # Verify record_engine_error guard against older failed_at.
    assert (
        worker_mod.repo.record_engine_error(
            None,
            node_id=102,
            engine_kind="TIMEOUT",
            error_code="STALE_ERR",
            retry_after=BASE + timedelta(seconds=30),
            failed_at=BASE - timedelta(seconds=1),
        )
        is False
    )
    assert store.eval_states[102]["timeout_last_error_code"] is None


def test_heartbeat_dead_boundary_and_health_readiness(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html>ok</html>")
    store = _FakeWorkerStore()
    _patch_worker_env(monkeypatch, store)
    cfg = _settings(monitoring_enabled=True, monitoring_tick_seconds=5)
    app = create_app(cfg, tmp_path)
    worker = app.state.monitoring_worker
    worker._monotonic = lambda: store.mono
    monkeypatch.setattr(
        app.state.database, "engine", SimpleNamespace(connect=store.connect, dispose=lambda: None)
    )
    monkeypatch.setattr(app.state.database, "probe", lambda: None)

    monkeypatch.setattr(worker, "start", lambda: None)

    with TestClient(app) as client:
        assert client.get("/health/live").json()["monitoring"] == "starting"
        assert client.get("/health/ready").status_code == 503

        # Successful tick transitions to running -> ready 200.
        worker.run_tick()
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health/ready").json()["monitoring"] == "running"

        # Exact boundary max(60, 3*5) = 60s -> still running; 60.001s -> dead (ready 503, live 200).
        store.mono = 1060.0
        assert client.get("/health/ready").status_code == 200
        store.mono = 1060.001
        assert worker.health_status() == "dead"
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/live").json()["monitoring"] == "dead"
        assert client.get("/health/ready").status_code == 503

        # Recovery tick brings status back to running -> ready 200.
        worker.run_tick()
        assert client.get("/health/ready").status_code == 200

        # Schema missing & lock unavailable states keep DB probe ok but readiness 503.
        store.schema_ready = False
        worker.run_tick()
        assert worker.health_status() == "schema_missing"
        assert client.get("/health/ready").status_code == 503

        store.schema_ready = True
        store.applock_rc = -1
        worker.run_tick()
        assert worker.health_status() == "lock_unavailable"
        assert client.get("/health/ready").status_code == 503

        # Internal shutting_down forces 503 without leaking non-G4-D10 monitoring enum.
        store.applock_rc = 0
        worker.run_tick()
        assert client.get("/health/ready").status_code == 200
        with worker._lock:
            worker._shutting_down = True
        r_shut = client.get("/health/ready")
        assert r_shut.status_code == 503
        assert r_shut.json()["monitoring"] == "running"


def test_shutdown_interrupts_node_loop_and_releases_on_worker_thread(monkeypatch):
    store = _FakeWorkerStore()
    store.node_ids = [101, 102]
    store.eval_states[102] = {**store.eval_states[101], "node_id": 102}
    _patch_worker_env(monkeypatch, store)
    db = SimpleNamespace(engine=SimpleNamespace(connect=store.connect))
    w = MonitoringWorker(
        db,
        _settings(
            monitoring_enabled=True,
            monitoring_tick_seconds=60,
            monitoring_shutdown_timeout_seconds=5,
        ),
        monotonic_fn=lambda: store.mono,
    )

    entered = threading.Event()
    release_node = threading.Event()

    def _block_on_first_node(nid):
        if nid == 101:
            entered.set()
            release_node.wait(timeout=5)

    store.on_timeout_enter = _block_on_first_node
    w.start()
    assert entered.wait(timeout=5)

    stopper_done = threading.Event()
    stop_ok: list[bool] = []

    def _stop_worker():
        stop_ok.append(w.stop())
        stopper_done.set()

    t = threading.Thread(target=_stop_worker)
    t.start()
    # While stop() is waiting, worker is shutting down (readiness false) and holds connection.
    while not w.is_shutting_down():
        threading.Event().wait(0.01)
    assert w.is_shutting_down() is True
    assert w.health_status() in {"starting", "running"}

    release_node.set()
    assert stopper_done.wait(timeout=5)
    t.join(timeout=5)
    assert stop_ok == [True]
    # Node 102 was not started after stop_event was set, and applock was released on worker thread.
    assert all(nid == 101 for _, nid in store.calls)
    assert store.release_calls == 1
