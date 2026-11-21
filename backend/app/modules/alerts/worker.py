import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.alerts import repository as repo
from app.modules.alerts.threshold_codec import ThresholdStateError
from app.modules.alerts.threshold_engine import evaluate_threshold_node
from app.modules.alerts.timeout_engine import TimeoutStateError, evaluate_timeout_node

logger = logging.getLogger("uvicorn.error.hqtcsdl.worker")

APPLOCK_RESOURCE = "hqtcsdl:monitoring_worker"
PUBLIC_STATES = frozenset(
    {"disabled", "starting", "running", "lock_unavailable", "schema_missing", "dead"}
)


@dataclass(frozen=True)
class WorkerTickStats:
    executed: bool
    nodes_scanned: int
    threshold_runs: int
    timeout_runs: int
    finalized: int
    errors: int


def _safe_error_code(exc: Exception, *, default: str) -> str:
    if isinstance(exc, (ThresholdStateError, TimeoutStateError)):
        return exc.code[:64]
    return default


class MonitoringWorker:
    """Single-process background scheduler for FL-09 threshold and FL-10 timeout engines."""

    def __init__(
        self,
        database,
        settings,
        *,
        monotonic_fn: Callable[[], float] | None = None,
        applock_resource: str = APPLOCK_RESOURCE,
    ):
        self.database = database
        self.settings = settings
        self._monotonic = monotonic_fn or time.monotonic
        self._applock_resource = applock_resource
        self._enabled = bool(getattr(settings, "monitoring_enabled", False))
        self._tick_seconds = int(getattr(settings, "monitoring_tick_seconds", 5))
        self._retry_seconds = int(getattr(settings, "monitoring_retry_seconds", 15))
        self._shutdown_timeout_seconds = int(
            getattr(settings, "monitoring_shutdown_timeout_seconds", 30)
        )
        self._command_timeout_seconds = int(
            getattr(settings, "monitoring_command_timeout_seconds", 15)
        )
        self._lock_timeout_ms = int(getattr(settings, "monitoring_lock_timeout_ms", 5000))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._shutting_down = False
        self._state = "starting" if self._enabled else "disabled"
        self._last_progress_mono: float | None = None
        self._conn = None
        self._lock_held = False

    def is_shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down

    def health_status(self) -> str:
        with self._lock:
            if not self._enabled:
                return "disabled"
            state = self._state
            last_progress = self._last_progress_mono
            thread = self._thread
        if thread is not None and not thread.is_alive():
            return "dead"
        if state == "running":
            deadline = max(60.0, 3.0 * float(self._tick_seconds))
            if last_progress is None or (self._monotonic() - last_progress) > deadline:
                return "dead"
        return state

    def _set_state(self, state: str) -> None:
        if state not in PUBLIC_STATES:
            raise ValueError(f"Invalid monitoring state: {state}")
        with self._lock:
            self._state = state

    def _mark_progress(self) -> None:
        now_m = self._monotonic()
        with self._lock:
            self._last_progress_mono = now_m

    def start(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._shutting_down = False
            self._state = "starting"
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="hqtcsdl-monitoring-worker",
                daemon=False,
            )
            self._thread.start()

    def stop(self) -> bool:
        with self._lock:
            self._shutting_down = True
            thread = self._thread
        self._stop_event.set()
        if thread is None:
            return True
        thread.join(timeout=float(self._shutdown_timeout_seconds))
        if thread.is_alive():
            logger.error("monitoring_worker_shutdown_timeout")
            return False
        return True

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_tick()
                except Exception:
                    logger.warning("monitoring_worker_tick_failed")
                self._stop_event.wait(timeout=float(self._tick_seconds))
        finally:
            self._close_connection_on_worker_thread()

    def _close_connection_on_worker_thread(self) -> None:
        conn = self._conn
        held = self._lock_held
        self._conn = None
        self._lock_held = False
        if conn is None:
            return
        if held:
            try:
                if conn.in_transaction():
                    conn.rollback()
                with conn.begin():
                    repo.release_worker_applock(conn, self._applock_resource)
            except Exception:
                logger.warning("monitoring_worker_applock_release_failed")
        try:
            conn.close()
        except Exception:
            try:
                conn.invalidate()
            except Exception:
                pass

    def _drop_connection(self) -> None:
        conn = self._conn
        self._conn = None
        self._lock_held = False
        if conn is not None:
            try:
                conn.invalidate()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def _open_and_lock_connection(self) -> bool:
        engine = getattr(self.database, "engine", None)
        if engine is None:
            self._set_state("lock_unavailable")
            return False
        conn = None
        try:
            conn = engine.connect().execution_options(isolation_level="READ COMMITTED")
            raw_conn = getattr(conn, "connection", None)
            driver_conn = getattr(raw_conn, "driver_connection", None)
            if driver_conn is not None and hasattr(driver_conn, "timeout"):
                driver_conn.timeout = self._command_timeout_seconds
            with conn.begin():
                conn.execute(
                    text(f"SET XACT_ABORT ON; SET LOCK_TIMEOUT {int(self._lock_timeout_ms)};")
                )
                if not repo.check_m3_schema_ready(conn):
                    self._set_state("schema_missing")
                    conn.close()
                    return False
                rc = repo.acquire_worker_applock(conn, self._applock_resource)
                if rc < 0:
                    self._set_state("lock_unavailable")
                    conn.close()
                    return False
            self._conn = conn
            self._lock_held = True
            return True
        except Exception:
            logger.warning("monitoring_worker_connect_or_lock_failed")
            if conn is not None:
                try:
                    conn.invalidate()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            self._set_state("lock_unavailable")
            return False

    def _ensure_ready_and_locked(self) -> bool:
        if self._conn is None:
            return self._open_and_lock_connection()
        try:
            if self._conn.in_transaction():
                self._conn.rollback()
            with self._conn.begin():
                if not repo.check_m3_schema_ready(self._conn):
                    self._drop_connection()
                    self._set_state("schema_missing")
                    return False
                mode = repo.check_worker_applock_mode(self._conn, self._applock_resource)
                if mode != "Exclusive":
                    self._drop_connection()
                    self._set_state("lock_unavailable")
                    return False
            return True
        except Exception:
            logger.warning("monitoring_worker_session_lost")
            self._drop_connection()
            self._set_state("lock_unavailable")
            return False

    def _record_error_tx(
        self,
        *,
        node_id: int,
        engine_kind: str,
        error_code: str,
        failed_at: Any,
    ) -> None:
        assert self._conn is not None
        try:
            if self._conn.in_transaction():
                self._conn.rollback()
            with self._conn.begin(), Session(bind=self._conn) as err_session:
                err_now = repo.get_db_utc_now(err_session)
                retry_at = err_now + timedelta(seconds=self._retry_seconds)
                repo.record_engine_error(
                    err_session,
                    node_id=node_id,
                    engine_kind=engine_kind,
                    error_code=error_code,
                    retry_after=retry_at,
                    failed_at=failed_at,
                )
        except Exception:
            logger.warning(
                "monitoring_error_record_failed node_id=%s engine=%s",
                node_id,
                engine_kind,
            )
            self._drop_connection()
            self._set_state("lock_unavailable")

    def _process_node(self, node_id: int) -> tuple[int, int, int, int]:
        """Run THRESHOLD, TIMEOUT, and conditional finalization for one Node."""
        assert self._conn is not None
        threshold_runs = 0
        timeout_runs = 0
        finalized = 0
        errors = 0

        with self._conn.begin(), Session(bind=self._conn) as snap_session:
            eval_state = repo.get_evaluation_state(snap_session, node_id)
            if eval_state is None:
                return 0, 0, 0, 0
            db_now = repo.get_db_utc_now(snap_session)

        target_gen = int(eval_state["desired_generation"])
        completed_gen = int(eval_state["completed_generation"])
        full_replay = bool(eval_state["full_replay_required"])
        th_error = eval_state.get("last_error_code")
        th_retry_after = eval_state.get("retry_after")
        to_retry_after = eval_state.get("timeout_retry_after")

        # 1. THRESHOLD engine (FL-09)
        needs_th = (completed_gen < target_gen) or full_replay or (th_error is not None)
        if not needs_th:
            threshold_handled = True
        elif th_retry_after is not None and db_now < th_retry_after:
            threshold_handled = False
        elif self._stop_event.is_set():
            return threshold_runs, timeout_runs, finalized, errors
        else:
            threshold_runs = 1
            try:
                with self._conn.begin(), Session(bind=self._conn) as th_session:
                    evaluate_threshold_node(th_session, node_id=node_id)
                threshold_handled = True
                self._mark_progress()
            except Exception as exc:
                errors += 1
                threshold_handled = False
                err_code = _safe_error_code(exc, default="THRESHOLD_EVALUATION_FAILED")
                logger.warning("threshold_engine_failed node_id=%s code=%s", node_id, err_code)
                self._record_error_tx(
                    node_id=node_id,
                    engine_kind="THRESHOLD",
                    error_code=err_code,
                    failed_at=db_now,
                )
                if self._conn is None:
                    return threshold_runs, timeout_runs, finalized, errors

        if self._stop_event.is_set() or self._conn is None:
            return threshold_runs, timeout_runs, finalized, errors

        # 2. TIMEOUT engine (FL-10) — runs independently including when desired == completed.
        if to_retry_after is not None and db_now < to_retry_after:
            timeout_handled = False
        else:
            timeout_runs = 1
            try:
                with self._conn.begin(), Session(bind=self._conn) as to_session:
                    evaluate_timeout_node(to_session, node_id=node_id, settings=self.settings)
                    repo.clear_timeout_error(to_session, node_id=node_id)
                timeout_handled = True
                self._mark_progress()
            except Exception as exc:
                errors += 1
                timeout_handled = False
                err_code = _safe_error_code(exc, default="TIMEOUT_EVALUATION_FAILED")
                logger.warning("timeout_engine_failed node_id=%s code=%s", node_id, err_code)
                self._record_error_tx(
                    node_id=node_id,
                    engine_kind="TIMEOUT",
                    error_code=err_code,
                    failed_at=db_now,
                )
                if self._conn is None:
                    return threshold_runs, timeout_runs, finalized, errors

        if self._stop_event.is_set() or self._conn is None:
            return threshold_runs, timeout_runs, finalized, errors

        # 3. Shared completion marker (FL-09 §6.11, G4-D11): only if BOTH engines handled target.
        if threshold_handled and timeout_handled:
            with self._conn.begin(), Session(bind=self._conn) as fin_session:
                fin_now = repo.get_db_utc_now(fin_session)
                if repo.finalize_node_evaluation(
                    fin_session,
                    node_id=node_id,
                    target_generation=target_gen,
                    completed_at=fin_now,
                ):
                    finalized = 1
            self._mark_progress()

        return threshold_runs, timeout_runs, finalized, errors

    def run_tick(self) -> WorkerTickStats:
        """Execute a single scheduler tick synchronously (used by loop and direct tests)."""
        if not self._enabled or self.is_shutting_down() or self._stop_event.is_set():
            return WorkerTickStats(
                executed=False,
                nodes_scanned=0,
                threshold_runs=0,
                timeout_runs=0,
                finalized=0,
                errors=0,
            )

        if not self._ensure_ready_and_locked():
            return WorkerTickStats(
                executed=False,
                nodes_scanned=0,
                threshold_runs=0,
                timeout_runs=0,
                finalized=0,
                errors=0,
            )

        self._mark_progress()
        self._set_state("running")

        assert self._conn is not None
        try:
            with self._conn.begin(), Session(bind=self._conn) as list_session:
                node_ids = repo.list_all_node_ids(list_session)
        except SQLAlchemyError:
            logger.warning("monitoring_worker_list_nodes_failed")
            self._drop_connection()
            self._set_state("lock_unavailable")
            return WorkerTickStats(
                executed=False,
                nodes_scanned=0,
                threshold_runs=0,
                timeout_runs=0,
                finalized=0,
                errors=1,
            )

        scanned = 0
        th_total = 0
        to_total = 0
        fin_total = 0
        err_total = 0
        for nid in node_ids:
            if self._stop_event.is_set() or self._conn is None:
                break
            scanned += 1
            try:
                th_r, to_r, fin_r, err_r = self._process_node(nid)
                th_total += th_r
                to_total += to_r
                fin_total += fin_r
                err_total += err_r
            except Exception:
                err_total += 1
                logger.warning("monitoring_worker_node_isolated_error node_id=%s", nid)
                if self._conn is not None:
                    try:
                        if self._conn.in_transaction():
                            self._conn.rollback()
                    except Exception:
                        self._drop_connection()
                        self._set_state("lock_unavailable")
                        break
            self._mark_progress()

        return WorkerTickStats(
            executed=True,
            nodes_scanned=scanned,
            threshold_runs=th_total,
            timeout_runs=to_total,
            finalized=fin_total,
            errors=err_total,
        )
