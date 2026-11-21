from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.core.web import serialize_utc
from app.modules.alerts import repository as alerts_repo
from app.modules.alerts.timeout_engine import build_evaluation_view
from app.modules.auth.service import storage_error
from app.modules.monitoring_config.schemas import METRIC_ORDER


def _format_bound(value: Any) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def get_node_context(session, node_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT n.node_id, n.device_code, n.zone_id, n.gateway_id, n.receiving_enabled, "
                "n.row_ver, z.farm_id, z.archived_at AS zone_archived_at, "
                "f.archived_at AS farm_archived_at, "
                "g.farm_id AS gateway_farm_id, g.receiving_enabled AS gateway_receiving_enabled "
                "FROM dbo.Node n "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "JOIN dbo.Farm f ON f.farm_id = z.farm_id "
                "LEFT JOIN dbo.Gateway g ON g.gateway_id = n.gateway_id "
                "WHERE n.node_id = :node_id"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def resolve_gateway_context(session, gateway_id: int) -> dict[str, Any] | None:
    gw = (
        session.execute(
            text("SELECT gateway_id, farm_id FROM dbo.Gateway WHERE gateway_id = :gid"),
            {"gid": int(gateway_id)},
        )
        .mappings()
        .first()
    )
    if gw is None:
        return None
    linked_rows = (
        session.execute(
            text(
                "SELECT DISTINCT n.zone_id, z.farm_id "
                "FROM dbo.Node n "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "WHERE n.gateway_id = :gid"
            ),
            {"gid": int(gateway_id)},
        )
        .mappings()
        .all()
    )
    farm_ids = sorted({int(gw["farm_id"]), *(int(r["farm_id"]) for r in linked_rows)})
    zone_ids = sorted({int(r["zone_id"]) for r in linked_rows})
    return {
        "gateway_id": int(gw["gateway_id"]),
        "farm_id": int(gw["farm_id"]),
        "farm_ids": farm_ids,
        "zone_ids": zone_ids,
    }


def lock_farms_ordered(session, farm_ids: list[int]) -> dict[int, dict[str, Any]]:
    locked: dict[int, dict[str, Any]] = {}
    for fid in sorted(set(farm_ids)):
        row = (
            session.execute(
                text(
                    "SELECT farm_id, archived_at FROM dbo.Farm WITH (UPDLOCK, HOLDLOCK) "
                    "WHERE farm_id = :id"
                ),
                {"id": int(fid)},
            )
            .mappings()
            .first()
        )
        if row is not None:
            locked[int(fid)] = dict(row)
    return locked


def lock_zones_ordered(session, zone_ids: list[int]) -> dict[int, dict[str, Any]]:
    locked: dict[int, dict[str, Any]] = {}
    for zid in sorted(set(zone_ids)):
        row = (
            session.execute(
                text(
                    "SELECT zone_id, farm_id, archived_at FROM dbo.Zone WITH (UPDLOCK, HOLDLOCK) "
                    "WHERE zone_id = :id"
                ),
                {"id": int(zid)},
            )
            .mappings()
            .first()
        )
        if row is not None:
            locked[int(zid)] = dict(row)
    return locked


def lock_gateway_nodes(session, gateway_id: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT node_id, device_code, zone_id, gateway_id, receiving_enabled, row_ver "
                "FROM dbo.Node WITH (UPDLOCK, HOLDLOCK) "
                "WHERE gateway_id = :gid "
                "ORDER BY node_id ASC"
            ),
            {"gid": int(gateway_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def update_node_receiving(
    session, *, node_id: int, expected_version: bytes, enabled: bool
) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "UPDATE dbo.Node SET receiving_enabled = :enabled "
                "OUTPUT inserted.node_id, inserted.device_code, inserted.zone_id, "
                "inserted.gateway_id, inserted.receiving_enabled, inserted.row_ver "
                "WHERE node_id = :node_id AND row_ver = :version"
            ),
            {"node_id": int(node_id), "version": expected_version, "enabled": 1 if enabled else 0},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def update_gateway_receiving(
    session, *, gateway_id: int, expected_version: bytes, enabled: bool
) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "UPDATE dbo.Gateway SET receiving_enabled = :enabled "
                "OUTPUT inserted.gateway_id, inserted.farm_id, inserted.receiving_enabled, "
                "inserted.row_ver "
                "WHERE gateway_id = :gid AND row_ver = :version"
            ),
            {"gid": int(gateway_id), "version": expected_version, "enabled": 1 if enabled else 0},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_current_config_version(
    session, *, node_id: int, config_kind: str, lock: bool = False
) -> dict[str, Any] | None:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    row = (
        session.execute(
            text(
                "SELECT config_id, node_id, config_kind, config_no, timeout_minutes, "
                "effective_from, effective_to, created_by, change_reason "
                f"FROM dbo.NodeConfigVersion{hint} "
                "WHERE node_id = :node_id AND config_kind = :kind AND effective_to IS NULL"
            ),
            {"node_id": int(node_id), "kind": config_kind},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_threshold_settings(session, config_id: int) -> dict[str, dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "SELECT metric_code, enabled, lower_value, upper_value "
                "FROM dbo.ThresholdSetting "
                "WHERE config_id = :config_id"
            ),
            {"config_id": int(config_id)},
        )
        .mappings()
        .all()
    )
    by_metric = {r["metric_code"]: dict(r) for r in rows}
    if len(by_metric) != len(METRIC_ORDER) or set(by_metric.keys()) != set(METRIC_ORDER):
        raise storage_error()
    return by_metric


def get_open_threshold_periods(
    session, node_id: int, *, lock: bool = False
) -> dict[str, dict[str, Any]]:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    rows = (
        session.execute(
            text(
                "SELECT period_id, node_id, monitoring_kind, metric_code, started_at, "
                "ended_at, start_config_id, start_reason, end_reason "
                f"FROM dbo.MonitoringPeriod{hint} "
                "WHERE node_id = :node_id AND monitoring_kind = 'THRESHOLD' AND ended_at IS NULL"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .all()
    )
    by_metric: dict[str, dict[str, Any]] = {}
    for r in rows:
        code = r["metric_code"]
        if code is None or code in by_metric:
            raise storage_error()
        by_metric[code] = dict(r)
    return by_metric


def close_current_config_version(
    session, *, config_id: int, node_id: int, config_kind: str, effective_to: datetime
) -> int:
    result = session.execute(
        text(
            "UPDATE dbo.NodeConfigVersion "
            "SET effective_to = :effective_to "
            "WHERE config_id = :config_id AND node_id = :node_id "
            "AND config_kind = :kind AND effective_to IS NULL"
        ),
        {
            "config_id": int(config_id),
            "node_id": int(node_id),
            "kind": config_kind,
            "effective_to": effective_to,
        },
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def get_max_config_no(session, *, node_id: int, config_kind: str) -> int:
    val = session.execute(
        text(
            "SELECT ISNULL(MAX(config_no), 0) "
            "FROM dbo.NodeConfigVersion "
            "WHERE node_id = :node_id AND config_kind = :kind"
        ),
        {"node_id": int(node_id), "kind": config_kind},
    ).scalar_one()
    return int(val)


def insert_config_version(
    session,
    *,
    node_id: int,
    config_kind: str,
    config_no: int,
    timeout_minutes: int | None,
    effective_from: datetime,
    created_by: int,
    change_reason: str,
) -> int:
    config_id = session.execute(
        text(
            "INSERT INTO dbo.NodeConfigVersion ("
            "node_id, config_kind, config_no, timeout_minutes, effective_from, "
            "effective_to, created_by, change_reason"
            ") "
            "OUTPUT inserted.config_id "
            "VALUES ("
            ":node_id, :config_kind, :config_no, :timeout_minutes, :effective_from, "
            "NULL, :created_by, :change_reason"
            ")"
        ),
        {
            "node_id": int(node_id),
            "config_kind": config_kind,
            "config_no": int(config_no),
            "timeout_minutes": int(timeout_minutes) if timeout_minutes is not None else None,
            "effective_from": effective_from,
            "created_by": int(created_by),
            "change_reason": change_reason,
        },
    ).scalar_one()
    return int(config_id)


def insert_threshold_settings(
    session, *, config_id: int, settings_by_metric: dict[str, dict[str, Any]]
) -> int:
    params: dict[str, Any] = {"cid": int(config_id)}
    tuples: list[str] = []
    for idx, metric_code in enumerate(METRIC_ORDER):
        row = settings_by_metric[metric_code]
        params[f"m_{idx}"] = metric_code
        params[f"en_{idx}"] = 1 if row["enabled"] else 0
        params[f"lo_{idx}"] = row["lower_value"]
        params[f"up_{idx}"] = row["upper_value"]
        tuples.append(f"(:cid, :m_{idx}, :en_{idx}, :lo_{idx}, :up_{idx})")
    result = session.execute(
        text(
            "INSERT INTO dbo.ThresholdSetting "
            "(config_id, metric_code, enabled, lower_value, upper_value) "
            "VALUES " + ", ".join(tuples)
        ),
        params,
    )
    if result.rowcount != len(METRIC_ORDER):
        raise storage_error()
    return int(result.rowcount)


def update_node_runtime_config_pointer(
    session, *, node_id: int, column: str, config_id: int
) -> int:
    if column not in {"current_config_id", "current_timeout_config_id"}:
        raise ValueError(f"Unsupported NodeRuntime pointer column: {column}")
    result = session.execute(
        text(f"UPDATE dbo.NodeRuntime SET {column} = :config_id WHERE node_id = :node_id"),
        {"config_id": int(config_id), "node_id": int(node_id)},
    )
    if result.rowcount != 1:
        raise storage_error()
    return int(result.rowcount)


def bump_evaluation_generation(session, *, node_id: int, full_replay: bool) -> int:
    replay_clause = ", full_replay_required = 1" if full_replay else ""
    new_gen = session.execute(
        text(
            "UPDATE dbo.EvaluationState "
            f"SET desired_generation = desired_generation + 1{replay_clause} "
            "OUTPUT inserted.desired_generation "
            "WHERE node_id = :node_id"
        ),
        {"node_id": int(node_id)},
    ).scalar()
    if new_gen is None:
        raise storage_error()
    return int(new_gen)


def build_timeout_validation_view(
    *, device_code: str, timeout_minutes: int | None, settings
) -> dict[str, Any]:
    profile = settings.node_time_profiles.get(device_code)
    if profile is None:
        return {
            "state": "PROFILE_MISSING",
            "sampling_interval_seconds": None,
            "normal_delivery_budget_seconds": None,
            "min_timeout_minutes": None,
        }
    min_timeout = (profile.stale_after_seconds // 60) + 1
    state = (
        "TIMEOUT_PROFILE_INCOMPATIBLE"
        if timeout_minutes is not None and int(timeout_minutes) < min_timeout
        else "READY"
    )
    return {
        "state": state,
        "sampling_interval_seconds": int(profile.sampling_interval_seconds),
        "normal_delivery_budget_seconds": int(profile.normal_delivery_budget_seconds),
        "min_timeout_minutes": min_timeout,
    }


def build_monitoring_state(
    session,
    *,
    node_id: int,
    device_code: str,
    settings,
    lock: bool = False,
) -> dict[str, Any]:
    runtime = (
        alerts_repo.lock_node_runtime(session, node_id)
        if lock
        else alerts_repo.get_node_runtime(session, node_id)
    )
    eval_state = (
        alerts_repo.lock_evaluation_state(session, node_id)
        if lock
        else alerts_repo.get_evaluation_state(session, node_id)
    )
    if runtime is None or eval_state is None:
        raise storage_error()

    threshold_cfg = get_current_config_version(
        session, node_id=node_id, config_kind="THRESHOLD", lock=lock
    )
    timeout_cfg = get_current_config_version(
        session, node_id=node_id, config_kind="TIMEOUT", lock=lock
    )
    if (threshold_cfg is None) != (runtime.get("current_config_id") is None) or (
        threshold_cfg is not None
        and int(threshold_cfg["config_id"]) != int(runtime["current_config_id"])
    ):
        raise storage_error()
    if (timeout_cfg is None) != (runtime.get("current_timeout_config_id") is None) or (
        timeout_cfg is not None
        and int(timeout_cfg["config_id"]) != int(runtime["current_timeout_config_id"])
    ):
        raise storage_error()

    threshold_settings = (
        get_threshold_settings(session, int(threshold_cfg["config_id"]))
        if threshold_cfg is not None
        else None
    )
    open_threshold_periods = get_open_threshold_periods(session, node_id, lock=lock)
    open_timeout_period = alerts_repo.get_open_timeout_period(session, node_id, lock=lock)
    if (open_timeout_period is None) != (runtime.get("active_period_id") is None) or (
        open_timeout_period is not None
        and int(open_timeout_period["period_id"]) != int(runtime["active_period_id"])
    ):
        raise storage_error()

    thresholds_dto: list[dict[str, Any]] = []
    for metric_code in METRIC_ORDER:
        if threshold_settings is None:
            thresholds_dto.append(
                {
                    "metric_code": metric_code,
                    "enabled": False,
                    "lower": None,
                    "upper": None,
                    "state": "UNCONFIGURED",
                }
            )
            continue
        row = threshold_settings[metric_code]
        enabled = bool(row["enabled"])
        lower_str = _format_bound(row["lower_value"])
        upper_str = _format_bound(row["upper_value"])
        if lower_str is None and upper_str is None:
            if enabled:
                raise storage_error()
            metric_state = "UNCONFIGURED"
        elif not enabled:
            metric_state = "DISABLED"
        elif metric_code in open_threshold_periods:
            metric_state = "ACTIVE"
        else:
            metric_state = "PAUSED"
        thresholds_dto.append(
            {
                "metric_code": metric_code,
                "enabled": enabled,
                "lower": lower_str,
                "upper": upper_str,
                "state": metric_state,
            }
        )

    timeout_minutes = int(timeout_cfg["timeout_minutes"]) if timeout_cfg is not None else None
    if timeout_cfg is None:
        timeout_state = "UNCONFIGURED"
    elif open_timeout_period is not None:
        timeout_state = "ACTIVE"
    else:
        timeout_state = "PAUSED"

    return {
        "node_id": str(node_id),
        "threshold_config_id": (
            str(threshold_cfg["config_id"]) if threshold_cfg is not None else None
        ),
        "timeout_config_id": (str(timeout_cfg["config_id"]) if timeout_cfg is not None else None),
        "thresholds": thresholds_dto,
        "timeout_minutes": timeout_minutes,
        "timeout_state": timeout_state,
        "S": serialize_utc(open_timeout_period["started_at"]) if open_timeout_period else None,
        "L": serialize_utc(runtime["last_new_persisted_at"]),
        "active_timeout_period_id": (
            str(runtime["active_period_id"]) if runtime["active_period_id"] is not None else None
        ),
        "evaluation": build_evaluation_view(eval_state),
        "timeout_validation": build_timeout_validation_view(
            device_code=device_code,
            timeout_minutes=timeout_minutes,
            settings=settings,
        ),
    }
