from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.modules.auth.service import storage_error


def get_db_utc_now(session) -> datetime:
    return session.execute(text("SELECT CAST(SYSUTCDATETIME() AS DATETIME2(3))")).scalar_one()


def lock_node_runtime(session, node_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT node_id, current_config_id, current_timeout_config_id, "
                "active_period_id, last_measurement_id, last_new_persisted_at "
                "FROM dbo.NodeRuntime WITH (UPDLOCK, HOLDLOCK) "
                "WHERE node_id = :node_id"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def lock_evaluation_state(session, node_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT node_id, desired_generation, completed_generation, "
                "replay_from_t_node, full_replay_required, last_success_at, "
                "last_error_code, retry_after, last_threshold_measurement_id, "
                "last_threshold_event_at, threshold_checkpoint_json, "
                "timeout_last_error_code, timeout_retry_after "
                "FROM dbo.EvaluationState WITH (UPDLOCK, HOLDLOCK) "
                "WHERE node_id = :node_id"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_node_runtime(session, node_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT node_id, current_config_id, current_timeout_config_id, "
                "active_period_id, last_measurement_id, last_new_persisted_at "
                "FROM dbo.NodeRuntime "
                "WHERE node_id = :node_id"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_evaluation_state(session, node_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT node_id, desired_generation, completed_generation, "
                "replay_from_t_node, full_replay_required, last_success_at, "
                "last_error_code, retry_after, last_threshold_measurement_id, "
                "last_threshold_event_at, threshold_checkpoint_json, "
                "timeout_last_error_code, timeout_retry_after "
                "FROM dbo.EvaluationState "
                "WHERE node_id = :node_id"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def lock_open_timeout_episode(session, node_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT episode_id, node_id, alert_kind, metric_code, lifecycle_status, "
                "is_current, current_revision_no, first_detected_at, acknowledgement_version "
                "FROM dbo.AlertEpisode WITH (UPDLOCK, HOLDLOCK) "
                "WHERE node_id = :node_id AND alert_kind = 'TIMEOUT' "
                "AND is_current = 1 AND lifecycle_status = 'OPEN'"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def lock_open_threshold_episode(session, node_id: int, metric_code: str) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT episode_id, node_id, alert_kind, metric_code, lifecycle_status, "
                "is_current, current_revision_no, first_detected_at, acknowledgement_version "
                "FROM dbo.AlertEpisode WITH (UPDLOCK, HOLDLOCK) "
                "WHERE node_id = :node_id AND alert_kind = 'THRESHOLD' "
                "AND metric_code = :metric_code AND is_current = 1 AND lifecycle_status = 'OPEN'"
            ),
            {"node_id": int(node_id), "metric_code": metric_code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_alert_revision(session, episode_id: int, revision_no: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT episode_id, revision_no, config_id, period_id, direction, "
                "start_event_time, end_event_time, start_backend_at, end_backend_at, "
                "closed_at, end_reason, start_measurement_id, end_measurement_id, "
                "lower_snapshot, upper_snapshot, timeout_snapshot, evaluated_at, "
                "evaluation_generation, acknowledgement_version, threshold_metadata_json "
                "FROM dbo.AlertRevision "
                "WHERE episode_id = :episode_id AND revision_no = :revision_no"
            ),
            {"episode_id": int(episode_id), "revision_no": int(revision_no)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def find_first_measurement_after(
    session, node_id: int, opening_last_measurement_id: int | None
) -> dict[str, Any] | None:
    baseline = 0 if opening_last_measurement_id is None else int(opening_last_measurement_id)
    row = (
        session.execute(
            text(
                "SELECT TOP (1) measurement_id, persisted_at "
                "FROM dbo.Measurement "
                "WHERE node_id = :node_id AND measurement_id > :baseline "
                "ORDER BY measurement_id ASC"
            ),
            {"node_id": int(node_id), "baseline": baseline},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def insert_alert_episode(
    session,
    *,
    node_id: int,
    alert_kind: str,
    metric_code: str | None,
    lifecycle_status: str,
    is_current: bool,
    first_detected_at: datetime,
    acknowledgement_version: int = 1,
) -> int:
    episode_id = session.execute(
        text(
            "INSERT INTO dbo.AlertEpisode ("
            "node_id, alert_kind, metric_code, lifecycle_status, is_current, "
            "current_revision_no, first_detected_at, acknowledgement_version"
            ") "
            "OUTPUT inserted.episode_id "
            "VALUES ("
            ":node_id, :alert_kind, :metric_code, :lifecycle_status, :is_current, "
            "NULL, :first_detected_at, :acknowledgement_version"
            ")"
        ),
        {
            "node_id": int(node_id),
            "alert_kind": alert_kind,
            "metric_code": metric_code,
            "lifecycle_status": lifecycle_status,
            "is_current": 1 if is_current else 0,
            "first_detected_at": first_detected_at,
            "acknowledgement_version": int(acknowledgement_version),
        },
    ).scalar_one()
    return int(episode_id)


def insert_alert_revision(
    session,
    *,
    episode_id: int,
    revision_no: int,
    config_id: int,
    period_id: int | None,
    direction: str | None,
    start_event_time: int | None,
    end_event_time: int | None,
    start_backend_at: datetime | None,
    end_backend_at: datetime | None,
    closed_at: datetime | None,
    end_reason: str | None,
    start_measurement_id: int | None,
    end_measurement_id: int | None,
    lower_snapshot: Decimal | None,
    upper_snapshot: Decimal | None,
    timeout_snapshot: int | None,
    evaluated_at: datetime,
    evaluation_generation: int,
    acknowledgement_version: int,
    threshold_metadata_json: str | None,
) -> None:
    result = session.execute(
        text(
            "INSERT INTO dbo.AlertRevision ("
            "episode_id, revision_no, config_id, period_id, direction, "
            "start_event_time, end_event_time, start_backend_at, end_backend_at, "
            "closed_at, end_reason, start_measurement_id, end_measurement_id, "
            "lower_snapshot, upper_snapshot, timeout_snapshot, evaluated_at, "
            "evaluation_generation, acknowledgement_version, threshold_metadata_json"
            ") VALUES ("
            ":episode_id, :revision_no, :config_id, :period_id, :direction, "
            ":start_event_time, :end_event_time, :start_backend_at, :end_backend_at, "
            ":closed_at, :end_reason, :start_measurement_id, :end_measurement_id, "
            ":lower_snapshot, :upper_snapshot, :timeout_snapshot, :evaluated_at, "
            ":evaluation_generation, :acknowledgement_version, :threshold_metadata_json"
            ")"
        ),
        {
            "episode_id": int(episode_id),
            "revision_no": int(revision_no),
            "config_id": int(config_id),
            "period_id": int(period_id) if period_id is not None else None,
            "direction": direction,
            "start_event_time": int(start_event_time) if start_event_time is not None else None,
            "end_event_time": int(end_event_time) if end_event_time is not None else None,
            "start_backend_at": start_backend_at,
            "end_backend_at": end_backend_at,
            "closed_at": closed_at,
            "end_reason": end_reason,
            "start_measurement_id": (
                int(start_measurement_id) if start_measurement_id is not None else None
            ),
            "end_measurement_id": (
                int(end_measurement_id) if end_measurement_id is not None else None
            ),
            "lower_snapshot": lower_snapshot,
            "upper_snapshot": upper_snapshot,
            "timeout_snapshot": int(timeout_snapshot) if timeout_snapshot is not None else None,
            "evaluated_at": evaluated_at,
            "evaluation_generation": int(evaluation_generation),
            "acknowledgement_version": int(acknowledgement_version),
            "threshold_metadata_json": threshold_metadata_json,
        },
    )
    if result.rowcount != 1:
        raise storage_error()


def update_alert_episode_pointer(
    session,
    *,
    episode_id: int,
    current_revision_no: int,
    lifecycle_status: str | None = None,
    is_current: bool | None = None,
    acknowledgement_version: int | None = None,
) -> int:
    sets = ["current_revision_no = :current_revision_no"]
    params: dict[str, Any] = {
        "episode_id": int(episode_id),
        "current_revision_no": int(current_revision_no),
    }
    if lifecycle_status is not None:
        sets.append("lifecycle_status = :lifecycle_status")
        params["lifecycle_status"] = lifecycle_status
    if is_current is not None:
        sets.append("is_current = :is_current")
        params["is_current"] = 1 if is_current else 0
    if acknowledgement_version is not None:
        sets.append("acknowledgement_version = :acknowledgement_version")
        params["acknowledgement_version"] = int(acknowledgement_version)
    result = session.execute(
        text(f"UPDATE dbo.AlertEpisode SET {', '.join(sets)} WHERE episode_id = :episode_id"),
        params,
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def get_open_timeout_period(session, node_id: int, *, lock: bool = False) -> dict[str, Any] | None:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    row = (
        session.execute(
            text(
                "SELECT period_id, node_id, monitoring_kind, metric_code, started_at, "
                "ended_at, start_config_id, start_reason, end_reason "
                f"FROM dbo.MonitoringPeriod{hint} "
                "WHERE node_id = :node_id AND monitoring_kind = 'TIMEOUT' AND ended_at IS NULL"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_timeout_config_by_id(session, node_id: int, config_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT config_id, node_id, config_kind, config_no, timeout_minutes, "
                "effective_from, effective_to, created_by, change_reason "
                "FROM dbo.NodeConfigVersion "
                "WHERE node_id = :node_id AND config_id = :config_id AND config_kind = 'TIMEOUT'"
            ),
            {"node_id": int(node_id), "config_id": int(config_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def insert_monitoring_period(
    session,
    *,
    node_id: int,
    monitoring_kind: str,
    metric_code: str | None,
    started_at: datetime,
    start_config_id: int,
    start_reason: str,
) -> int:
    period_id = session.execute(
        text(
            "INSERT INTO dbo.MonitoringPeriod ("
            "node_id, monitoring_kind, metric_code, started_at, ended_at, "
            "start_config_id, start_reason, end_reason"
            ") "
            "OUTPUT inserted.period_id "
            "VALUES ("
            ":node_id, :monitoring_kind, :metric_code, :started_at, NULL, "
            ":start_config_id, :start_reason, NULL"
            ")"
        ),
        {
            "node_id": int(node_id),
            "monitoring_kind": monitoring_kind,
            "metric_code": metric_code,
            "started_at": started_at,
            "start_config_id": int(start_config_id),
            "start_reason": start_reason,
        },
    ).scalar_one()
    return int(period_id)


def close_monitoring_period(
    session, *, period_id: int, node_id: int, ended_at: datetime, end_reason: str
) -> int:
    result = session.execute(
        text(
            "UPDATE dbo.MonitoringPeriod "
            "SET ended_at = :ended_at, end_reason = :end_reason "
            "WHERE period_id = :period_id AND node_id = :node_id AND ended_at IS NULL"
        ),
        {
            "period_id": int(period_id),
            "node_id": int(node_id),
            "ended_at": ended_at,
            "end_reason": end_reason,
        },
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def update_node_runtime_active_period(
    session, *, node_id: int, active_period_id: int | None
) -> int:
    result = session.execute(
        text(
            "UPDATE dbo.NodeRuntime SET active_period_id = :active_period_id "
            "WHERE node_id = :node_id"
        ),
        {
            "node_id": int(node_id),
            "active_period_id": int(active_period_id) if active_period_id is not None else None,
        },
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def list_threshold_configs_with_settings(session, node_id: int) -> list[dict[str, Any]]:
    cfg_rows = (
        session.execute(
            text(
                "SELECT config_id, node_id, config_kind, config_no, effective_from, "
                "effective_to, created_by, change_reason "
                "FROM dbo.NodeConfigVersion "
                "WHERE node_id = :node_id AND config_kind = 'THRESHOLD' "
                "ORDER BY config_no ASC"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    if not cfg_rows:
        return []
    setting_rows = (
        session.execute(
            text(
                "SELECT ts.config_id, ts.metric_code, ts.enabled, ts.lower_value, ts.upper_value "
                "FROM dbo.ThresholdSetting ts "
                "JOIN dbo.NodeConfigVersion c ON c.config_id = ts.config_id "
                "WHERE c.node_id = :node_id AND c.config_kind = 'THRESHOLD'"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    by_cfg: dict[int, dict[str, dict[str, Any]]] = {}
    for row in setting_rows:
        cid = int(row["config_id"])
        by_cfg.setdefault(cid, {})[row["metric_code"]] = dict(row)
    result: list[dict[str, Any]] = []
    for cfg in cfg_rows:
        item = dict(cfg)
        item["settings"] = by_cfg.get(int(cfg["config_id"]), {})
        result.append(item)
    return result


def list_threshold_periods(session, node_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT period_id, node_id, monitoring_kind, metric_code, started_at, "
                "ended_at, start_config_id, start_reason, end_reason "
                "FROM dbo.MonitoringPeriod "
                "WHERE node_id = :node_id AND monitoring_kind = 'THRESHOLD' "
                "ORDER BY period_id ASC"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def get_max_raw_measurement_id(session, node_id: int) -> int | None:
    val = session.execute(
        text("SELECT MAX(measurement_id) FROM dbo.Measurement WHERE node_id = :node_id"),
        {"node_id": int(node_id)},
    ).scalar_one()
    return int(val) if val is not None else None


def list_valid_measurements_with_values(session, node_id: int) -> list[dict[str, Any]]:
    meas_rows = (
        session.execute(
            text(
                "SELECT measurement_id, node_id, boot_id, seq, t_node, time_status, persisted_at "
                "FROM dbo.Measurement "
                "WHERE node_id = :node_id AND time_status = 'VALID' AND t_node IS NOT NULL "
                "ORDER BY measurement_id ASC"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    if not meas_rows:
        return []
    val_rows = (
        session.execute(
            text(
                "SELECT mv.measurement_id, mv.metric_code, mv.value, mv.read_status "
                "FROM dbo.MeasurementValue mv "
                "JOIN dbo.Measurement m ON m.measurement_id = mv.measurement_id "
                "WHERE m.node_id = :node_id AND m.time_status = 'VALID' AND m.t_node IS NOT NULL"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    values_by_mid: dict[int, dict[str, dict[str, Any]]] = {}
    for row in val_rows:
        mid = int(row["measurement_id"])
        values_by_mid.setdefault(mid, {})[row["metric_code"]] = dict(row)
    result: list[dict[str, Any]] = []
    for meas in meas_rows:
        item = dict(meas)
        item["values"] = values_by_mid.get(int(meas["measurement_id"]), {})
        result.append(item)
    return result


def has_any_threshold_episode(session, node_id: int) -> bool:
    val = session.execute(
        text(
            "SELECT TOP (1) 1 FROM dbo.AlertEpisode "
            "WHERE node_id = :node_id AND alert_kind = 'THRESHOLD'"
        ),
        {"node_id": int(node_id)},
    ).scalar()
    return val is not None


def lock_current_threshold_episodes_with_revisions(session, node_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT e.episode_id, e.node_id, e.alert_kind, e.metric_code, "
                "e.lifecycle_status, e.is_current, e.current_revision_no, "
                "e.first_detected_at, e.acknowledgement_version, "
                "r.revision_no, r.config_id, r.period_id, r.direction, "
                "r.start_event_time, r.end_event_time, r.start_backend_at, r.end_backend_at, "
                "r.closed_at, r.end_reason, r.start_measurement_id, r.end_measurement_id, "
                "r.lower_snapshot, r.upper_snapshot, r.evaluated_at, r.evaluation_generation, "
                "r.threshold_metadata_json "
                "FROM dbo.AlertEpisode e WITH (UPDLOCK, HOLDLOCK) "
                "LEFT JOIN dbo.AlertRevision r "
                "ON r.episode_id = e.episode_id AND r.revision_no = e.current_revision_no "
                "WHERE e.node_id = :node_id AND e.alert_kind = 'THRESHOLD' AND e.is_current = 1 "
                "ORDER BY e.episode_id ASC"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def insert_alert_replacement(
    session,
    *,
    old_episode_id: int,
    old_revision_no: int,
    new_episode_id: int,
    new_revision_no: int,
    created_at: datetime,
    evaluation_generation: int,
    reason: str,
) -> None:
    result = session.execute(
        text(
            "INSERT INTO dbo.AlertReplacement ("
            "old_episode_id, old_revision_no, new_episode_id, new_revision_no, "
            "created_at, evaluation_generation, reason"
            ") VALUES ("
            ":old_episode_id, :old_revision_no, :new_episode_id, :new_revision_no, "
            ":created_at, :evaluation_generation, :reason"
            ")"
        ),
        {
            "old_episode_id": int(old_episode_id),
            "old_revision_no": int(old_revision_no),
            "new_episode_id": int(new_episode_id),
            "new_revision_no": int(new_revision_no),
            "created_at": created_at,
            "evaluation_generation": int(evaluation_generation),
            "reason": reason,
        },
    )
    if result.rowcount != 1:
        raise storage_error()


def update_threshold_checkpoint(
    session,
    *,
    node_id: int,
    last_threshold_measurement_id: int | None,
    last_threshold_event_at: datetime | None,
    threshold_checkpoint_json: str,
) -> int:
    result = session.execute(
        text(
            "UPDATE dbo.EvaluationState "
            "SET last_threshold_measurement_id = :last_mid, "
            "last_threshold_event_at = :last_event_at, "
            "threshold_checkpoint_json = :checkpoint_json, "
            "last_error_code = NULL, "
            "retry_after = NULL "
            "WHERE node_id = :node_id"
        ),
        {
            "node_id": int(node_id),
            "last_mid": (
                int(last_threshold_measurement_id)
                if last_threshold_measurement_id is not None
                else None
            ),
            "last_event_at": last_threshold_event_at,
            "checkpoint_json": threshold_checkpoint_json,
        },
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def list_all_node_ids(session) -> list[int]:
    rows = (
        session.execute(text("SELECT node_id FROM dbo.Node ORDER BY node_id ASC")).scalars().all()
    )
    return [int(r) for r in rows]


def clear_timeout_error(session, *, node_id: int) -> int:
    result = session.execute(
        text(
            "UPDATE dbo.EvaluationState "
            "SET timeout_last_error_code = NULL, timeout_retry_after = NULL "
            "WHERE node_id = :node_id"
        ),
        {"node_id": int(node_id)},
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def record_engine_error(
    session,
    *,
    node_id: int,
    engine_kind: str,
    error_code: str,
    retry_after: datetime,
    failed_at: datetime,
) -> bool:
    """Persist engine-specific error/backoff only if no newer success superseded failed_at."""
    if engine_kind not in {"THRESHOLD", "TIMEOUT"}:
        raise ValueError(f"Unsupported engine_kind: {engine_kind}")
    state = lock_evaluation_state(session, node_id)
    if state is None:
        return False
    last_success = state.get("last_success_at")
    if last_success is not None and last_success >= failed_at:
        return False
    safe_code = (error_code or "EVALUATION_FAILED")[:64]
    if engine_kind == "THRESHOLD":
        sql = (
            "UPDATE dbo.EvaluationState "
            "SET last_error_code = :code, retry_after = :retry_after "
            "WHERE node_id = :node_id"
        )
    else:
        sql = (
            "UPDATE dbo.EvaluationState "
            "SET timeout_last_error_code = :code, timeout_retry_after = :retry_after "
            "WHERE node_id = :node_id"
        )
    result = session.execute(
        text(sql),
        {"node_id": int(node_id), "code": safe_code, "retry_after": retry_after},
    )
    return int(result.rowcount) == 1


def finalize_node_evaluation(
    session,
    *,
    node_id: int,
    target_generation: int,
    completed_at: datetime,
) -> bool:
    """Clear replay markers and advance completed_generation iff target_gen matches."""
    state = lock_evaluation_state(session, node_id)
    if state is None:
        return False
    if (
        int(state["desired_generation"]) != int(target_generation)
        or state.get("last_error_code") is not None
        or state.get("timeout_last_error_code") is not None
    ):
        return False
    result = session.execute(
        text(
            "UPDATE dbo.EvaluationState "
            "SET completed_generation = :target_gen, "
            "full_replay_required = 0, "
            "replay_from_t_node = NULL, "
            "last_success_at = :completed_at "
            "WHERE node_id = :node_id"
        ),
        {
            "node_id": int(node_id),
            "target_gen": int(target_generation),
            "completed_at": completed_at,
        },
    )
    return int(result.rowcount) == 1


def check_m3_schema_ready(conn) -> bool:
    table_count = conn.execute(
        text(
            "SELECT COUNT(*) FROM sys.tables t "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "WHERE s.name = 'dbo' "
            "AND t.name IN ('AlertEpisode', 'AlertRevision', "
            "'AlertAcknowledgement', 'AlertReplacement')"
        )
    ).scalar_one()
    if int(table_count) != 4:
        return False
    col_count = conn.execute(
        text(
            "SELECT COUNT(*) FROM sys.columns "
            "WHERE object_id = OBJECT_ID('dbo.EvaluationState') "
            "AND name IN ('timeout_last_error_code', 'timeout_retry_after')"
        )
    ).scalar_one()
    if int(col_count) != 2:
        return False
    ver = conn.execute(text("SELECT version_num FROM dbo.alembic_version")).scalar()
    return ver == "e7c4b6a10d53"


def acquire_worker_applock(conn, resource: str) -> int:
    rc = conn.execute(
        text(
            "DECLARE @rc INT; "
            "EXEC @rc = sys.sp_getapplock "
            "@Resource = :res, @LockMode = 'Exclusive', "
            "@LockOwner = 'Session', @LockTimeout = 0; "
            "SELECT @rc;"
        ),
        {"res": resource},
    ).scalar_one()
    return int(rc)


def check_worker_applock_mode(conn, resource: str) -> str | None:
    val = conn.execute(
        text("SELECT APPLOCK_MODE('public', :res, 'Session')"),
        {"res": resource},
    ).scalar()
    return str(val) if val is not None else None


def release_worker_applock(conn, resource: str) -> int:
    rc = conn.execute(
        text(
            "DECLARE @rc INT; "
            "EXEC @rc = sys.sp_releaseapplock @Resource = :res, @LockOwner = 'Session'; "
            "SELECT @rc;"
        ),
        {"res": resource},
    ).scalar_one()
    return int(rc)


def get_alert_episode_context(
    session, episode_id: int, *, lock_episode: bool = False
) -> dict[str, Any] | None:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock_episode else ""
    row = (
        session.execute(
            text(
                "SELECT e.episode_id, e.node_id, e.alert_kind, e.metric_code, "
                "e.lifecycle_status, e.is_current, e.current_revision_no, "
                "e.first_detected_at, e.acknowledgement_version, "
                "n.device_code, n.name AS node_name, n.zone_id, n.gateway_id, "
                "z.farm_id, z.zone_code, z.name AS zone_name, z.archived_at AS zone_archived_at, "
                "f.farm_code, f.name AS farm_name, f.archived_at AS farm_archived_at, "
                "r.revision_no, r.config_id, r.period_id, r.direction, "
                "r.start_event_time, r.end_event_time, r.start_backend_at, r.end_backend_at, "
                "r.closed_at, r.end_reason, r.start_measurement_id, r.end_measurement_id, "
                "r.lower_snapshot, r.upper_snapshot, r.timeout_snapshot, "
                "r.evaluated_at, r.evaluation_generation, r.threshold_metadata_json, "
                "aa.acknowledged_revision_no, aa.admin_id AS ack_admin_id, "
                "aa.acknowledged_at, aa.note AS ack_note, adm.username AS ack_username "
                f"FROM dbo.AlertEpisode e{hint} "
                "JOIN dbo.Node n ON n.node_id = e.node_id "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "JOIN dbo.Farm f ON f.farm_id = z.farm_id "
                "LEFT JOIN dbo.AlertRevision r "
                "ON r.episode_id = e.episode_id AND r.revision_no = e.current_revision_no "
                "LEFT JOIN dbo.AlertAcknowledgement aa "
                "ON aa.episode_id = e.episode_id "
                "AND aa.acknowledgement_version = e.acknowledgement_version "
                "LEFT JOIN dbo.AdminAccount adm ON adm.admin_id = aa.admin_id "
                "WHERE e.episode_id = :episode_id"
            ),
            {"episode_id": int(episode_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def resolve_episode_node_id(session, episode_id: int) -> int | None:
    val = session.execute(
        text("SELECT node_id FROM dbo.AlertEpisode WHERE episode_id = :eid"),
        {"eid": int(episode_id)},
    ).scalar()
    return int(val) if val is not None else None


def lock_alert_episode_by_id(session, episode_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT episode_id, node_id, alert_kind, metric_code, lifecycle_status, "
                "is_current, current_revision_no, first_detected_at, acknowledgement_version "
                "FROM dbo.AlertEpisode WITH (UPDLOCK, HOLDLOCK) "
                "WHERE episode_id = :eid"
            ),
            {"eid": int(episode_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_alert_acknowledgement(
    session, episode_id: int, acknowledgement_version: int
) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT aa.episode_id, aa.acknowledgement_version, aa.acknowledged_revision_no, "
                "aa.admin_id, aa.acknowledged_at, aa.note, adm.username "
                "FROM dbo.AlertAcknowledgement aa "
                "JOIN dbo.AdminAccount adm ON adm.admin_id = aa.admin_id "
                "WHERE aa.episode_id = :eid AND aa.acknowledgement_version = :ver"
            ),
            {"eid": int(episode_id), "ver": int(acknowledgement_version)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def insert_alert_acknowledgement(
    session,
    *,
    episode_id: int,
    acknowledgement_version: int,
    acknowledged_revision_no: int,
    admin_id: int,
    acknowledged_at: datetime,
    note: str | None,
) -> None:
    result = session.execute(
        text(
            "INSERT INTO dbo.AlertAcknowledgement ("
            "episode_id, acknowledgement_version, acknowledged_revision_no, "
            "admin_id, acknowledged_at, note"
            ") VALUES ("
            ":eid, :ver, :rev_no, :admin_id, :ack_at, :note"
            ")"
        ),
        {
            "eid": int(episode_id),
            "ver": int(acknowledgement_version),
            "rev_no": int(acknowledged_revision_no),
            "admin_id": int(admin_id),
            "ack_at": acknowledged_at,
            "note": note,
        },
    )
    if result.rowcount != 1:
        raise storage_error()


def list_alerts_page(
    session,
    *,
    farm_id: int,
    node_id: int | None,
    view: str,
    kind: str | None,
    lifecycle: str | None,
    acknowledged: bool | None,
    recovery_proven: bool | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    boundary_detected_at: datetime | None,
    boundary_episode_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["z.farm_id = :farm_id", "e.is_current = 1"]
    params: dict[str, Any] = {"farm_id": int(farm_id), "limit": int(limit)}
    if node_id is not None:
        clauses.append("e.node_id = :node_id")
        params["node_id"] = int(node_id)
    if view == "active":
        clauses.append("e.lifecycle_status = 'OPEN'")
    elif lifecycle is not None:
        clauses.append("e.lifecycle_status = :lifecycle")
        params["lifecycle"] = lifecycle
    if kind is not None:
        clauses.append("e.alert_kind = :kind")
        params["kind"] = kind
    if acknowledged is True:
        clauses.append("aa.episode_id IS NOT NULL")
    elif acknowledged is False:
        clauses.append("aa.episode_id IS NULL")
    if recovery_proven is True:
        clauses.append(
            "e.alert_kind = 'THRESHOLD' AND e.lifecycle_status = 'CLOSED' "
            "AND r.end_reason = 'RECOVERED'"
        )
    elif recovery_proven is False:
        clauses.append(
            "e.alert_kind = 'THRESHOLD' AND NOT ("
            "e.lifecycle_status = 'CLOSED' AND r.end_reason = 'RECOVERED'"
            ")"
        )
    if from_dt is not None:
        clauses.append("e.first_detected_at >= :from_dt")
        params["from_dt"] = from_dt
    if to_dt is not None:
        clauses.append("e.first_detected_at <= :to_dt")
        params["to_dt"] = to_dt
    if boundary_detected_at is not None and boundary_episode_id is not None:
        clauses.append(
            "(e.first_detected_at < :b_dt "
            "OR (e.first_detected_at = :b_dt AND e.episode_id < :b_id))"
        )
        params["b_dt"] = boundary_detected_at
        params["b_id"] = int(boundary_episode_id)

    where_sql = " AND ".join(clauses)
    rows = (
        session.execute(
            text(
                "SELECT TOP (:limit) "
                "e.episode_id, e.node_id, e.alert_kind, e.metric_code, "
                "e.lifecycle_status, e.is_current, e.current_revision_no, "
                "e.first_detected_at, e.acknowledgement_version, "
                "n.device_code, n.name AS node_name, n.zone_id, "
                "z.farm_id, z.zone_code, z.name AS zone_name, "
                "r.revision_no, r.config_id, r.period_id, r.direction, "
                "r.start_event_time, r.end_event_time, r.start_backend_at, r.end_backend_at, "
                "r.closed_at, r.end_reason, r.start_measurement_id, r.end_measurement_id, "
                "r.lower_snapshot, r.upper_snapshot, r.timeout_snapshot, "
                "r.evaluated_at, r.evaluation_generation, r.threshold_metadata_json, "
                "aa.acknowledged_revision_no, aa.admin_id AS ack_admin_id, "
                "aa.acknowledged_at, aa.note AS ack_note, adm.username AS ack_username "
                "FROM dbo.AlertEpisode e "
                "JOIN dbo.Node n ON n.node_id = e.node_id "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "LEFT JOIN dbo.AlertRevision r "
                "ON r.episode_id = e.episode_id AND r.revision_no = e.current_revision_no "
                "LEFT JOIN dbo.AlertAcknowledgement aa "
                "ON aa.episode_id = e.episode_id "
                "AND aa.acknowledgement_version = e.acknowledgement_version "
                "LEFT JOIN dbo.AdminAccount adm ON adm.admin_id = aa.admin_id "
                f"WHERE {where_sql} "
                "ORDER BY e.first_detected_at DESC, e.episode_id DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def list_active_open_alerts_for_nodes(session, node_ids: list[int]) -> list[dict[str, Any]]:
    if not node_ids:
        return []
    placeholders = ", ".join(f":nid_{i}" for i in range(len(node_ids)))
    params = {f"nid_{i}": int(nid) for i, nid in enumerate(node_ids)}
    rows = (
        session.execute(
            text(
                "SELECT "
                "e.episode_id, e.node_id, e.alert_kind, e.metric_code, "
                "e.lifecycle_status, e.is_current, e.current_revision_no, "
                "e.first_detected_at, e.acknowledgement_version, "
                "n.device_code, n.name AS node_name, n.zone_id, "
                "z.farm_id, z.zone_code, z.name AS zone_name, "
                "r.revision_no, r.config_id, r.period_id, r.direction, "
                "r.start_event_time, r.end_event_time, r.start_backend_at, r.end_backend_at, "
                "r.closed_at, r.end_reason, r.start_measurement_id, r.end_measurement_id, "
                "r.lower_snapshot, r.upper_snapshot, r.timeout_snapshot, "
                "r.evaluated_at, r.evaluation_generation, r.threshold_metadata_json, "
                "aa.acknowledged_revision_no, aa.admin_id AS ack_admin_id, "
                "aa.acknowledged_at, aa.note AS ack_note, adm.username AS ack_username "
                "FROM dbo.AlertEpisode e "
                "JOIN dbo.Node n ON n.node_id = e.node_id "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "LEFT JOIN dbo.AlertRevision r "
                "ON r.episode_id = e.episode_id AND r.revision_no = e.current_revision_no "
                "LEFT JOIN dbo.AlertAcknowledgement aa "
                "ON aa.episode_id = e.episode_id "
                "AND aa.acknowledgement_version = e.acknowledgement_version "
                "LEFT JOIN dbo.AdminAccount adm ON adm.admin_id = aa.admin_id "
                f"WHERE e.node_id IN ({placeholders}) "
                "AND e.is_current = 1 AND e.lifecycle_status = 'OPEN' "
                "ORDER BY e.first_detected_at DESC, e.episode_id DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def list_evaluation_states_for_nodes(session, node_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not node_ids:
        return {}
    placeholders = ", ".join(f":nid_{i}" for i in range(len(node_ids)))
    params = {f"nid_{i}": int(nid) for i, nid in enumerate(node_ids)}
    rows = (
        session.execute(
            text(
                "SELECT node_id, desired_generation, completed_generation, "
                "replay_from_t_node, full_replay_required, last_success_at, "
                "last_error_code, retry_after, last_threshold_measurement_id, "
                "last_threshold_event_at, threshold_checkpoint_json, "
                "timeout_last_error_code, timeout_retry_after "
                "FROM dbo.EvaluationState "
                f"WHERE node_id IN ({placeholders})"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return {int(r["node_id"]): dict(r) for r in rows}


def list_audit_revisions(
    session, *, episode_id: int, boundary_rev_no: int | None, limit: int
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"eid": int(episode_id), "limit": int(limit)}
    extra = ""
    if boundary_rev_no is not None:
        extra = " AND revision_no < :b_rev"
        params["b_rev"] = int(boundary_rev_no)
    rows = (
        session.execute(
            text(
                "SELECT TOP (:limit) episode_id, revision_no, config_id, period_id, direction, "
                "start_event_time, end_event_time, start_backend_at, end_backend_at, "
                "closed_at, end_reason, start_measurement_id, end_measurement_id, "
                "lower_snapshot, upper_snapshot, timeout_snapshot, evaluated_at, "
                "evaluation_generation, acknowledgement_version, threshold_metadata_json "
                "FROM dbo.AlertRevision "
                f"WHERE episode_id = :eid{extra} "
                "ORDER BY revision_no DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def list_audit_acknowledgements(
    session,
    *,
    episode_id: int,
    exact_version: int | None,
    boundary_ack_ver: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"eid": int(episode_id), "limit": int(limit)}
    extra = ""
    if exact_version is not None:
        extra = " AND aa.acknowledgement_version = :exact_ver"
        params["exact_ver"] = int(exact_version)
    elif boundary_ack_ver is not None:
        extra = " AND aa.acknowledgement_version < :b_ver"
        params["b_ver"] = int(boundary_ack_ver)
    rows = (
        session.execute(
            text(
                "SELECT TOP (:limit) aa.episode_id, aa.acknowledgement_version, "
                "aa.acknowledged_revision_no, aa.admin_id, aa.acknowledged_at, aa.note, "
                "adm.username "
                "FROM dbo.AlertAcknowledgement aa "
                "JOIN dbo.AdminAccount adm ON adm.admin_id = aa.admin_id "
                f"WHERE aa.episode_id = :eid{extra} "
                "ORDER BY aa.acknowledgement_version DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def list_audit_replacements(
    session,
    *,
    episode_id: int,
    boundary: tuple[datetime, int, int, int, int] | None,
    limit: int,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"eid": int(episode_id), "limit": int(limit)}
    extra = ""
    if boundary is not None:
        b_dt, b_oep, b_orev, b_nep, b_nrev = boundary
        extra = (
            " AND (created_at < :b_dt "
            "OR (created_at = :b_dt AND old_episode_id < :b_oep) "
            "OR (created_at = :b_dt AND old_episode_id = :b_oep AND old_revision_no < :b_orev) "
            "OR (created_at = :b_dt AND old_episode_id = :b_oep AND old_revision_no = :b_orev "
            "AND new_episode_id < :b_nep) "
            "OR (created_at = :b_dt AND old_episode_id = :b_oep AND old_revision_no = :b_orev "
            "AND new_episode_id = :b_nep AND new_revision_no < :b_nrev))"
        )
        params.update(
            {
                "b_dt": b_dt,
                "b_oep": int(b_oep),
                "b_orev": int(b_orev),
                "b_nep": int(b_nep),
                "b_nrev": int(b_nrev),
            }
        )
    rows = (
        session.execute(
            text(
                "SELECT TOP (:limit) old_episode_id, old_revision_no, "
                "new_episode_id, new_revision_no, created_at, evaluation_generation, reason "
                "FROM dbo.AlertReplacement "
                f"WHERE (old_episode_id = :eid OR new_episode_id = :eid){extra} "
                "ORDER BY created_at DESC, old_episode_id DESC, old_revision_no DESC, "
                "new_episode_id DESC, new_revision_no DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
