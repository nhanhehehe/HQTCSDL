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
