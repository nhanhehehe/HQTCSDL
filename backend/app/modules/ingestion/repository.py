from datetime import datetime

from sqlalchemy import text

from app.modules.ingestion.validator import METRIC_ORDER, ValidatedRecord


def resolve_node_for_ingestion(session, device_code: str) -> dict | None:
    row = (
        session.execute(
            text(
                "SELECT n.node_id, n.device_code, n.zone_id, z.farm_id AS zone_farm_id, "
                "n.gateway_id "
                "FROM dbo.Node n "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "WHERE n.device_code = :code "
                "AND DATALENGTH(n.device_code) = DATALENGTH(CAST(:code AS VARCHAR(128)))"
            ),
            {"code": device_code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def seek_measurement_locked(session, *, node_id: int, boot_id: int, seq: int) -> dict | None:
    row = (
        session.execute(
            text(
                "SELECT measurement_id, node_id, gateway_id, boot_id, seq, t_node, time_status, "
                "recv_time, rssi, backend_received_at, persisted_at, canonical_version, "
                "original_hash "
                "FROM dbo.Measurement WITH (UPDLOCK, HOLDLOCK) "
                "WHERE node_id = :node_id AND boot_id = :boot_id AND seq = :seq"
            ),
            {"node_id": node_id, "boot_id": boot_id, "seq": seq},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_measurement_values(session, measurement_id: int) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT metric_code, value, read_status "
                "FROM dbo.MeasurementValue "
                "WHERE measurement_id = :measurement_id"
            ),
            {"measurement_id": measurement_id},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def get_max_event_time(session, node_id: int) -> int | None:
    value = session.execute(
        text(
            "SELECT MAX(t_node) FROM dbo.Measurement "
            "WHERE node_id = :node_id AND t_node IS NOT NULL"
        ),
        {"node_id": node_id},
    ).scalar()
    return None if value is None else int(value)


def insert_measurement(
    session,
    *,
    node_id: int,
    gateway_id: int,
    record: ValidatedRecord,
    backend_received_at: datetime,
    canonical_version: int,
    original_hash: bytes,
) -> dict:
    row = (
        session.execute(
            text(
                "INSERT INTO dbo.Measurement ("
                "node_id, gateway_id, boot_id, seq, t_node, time_status, "
                "recv_time, rssi, backend_received_at, persisted_at, "
                "canonical_version, original_hash"
                ") "
                "OUTPUT inserted.measurement_id, inserted.persisted_at "
                "VALUES ("
                ":node_id, :gateway_id, :boot_id, :seq, :t_node, :time_status, "
                ":recv_time, :rssi, :backend_received_at, SYSUTCDATETIME(), "
                ":canonical_version, :original_hash"
                ")"
            ),
            {
                "node_id": node_id,
                "gateway_id": gateway_id,
                "boot_id": record.boot_id,
                "seq": record.seq,
                "t_node": record.t_node,
                "time_status": record.time_status,
                "recv_time": record.recv_time,
                "rssi": record.rssi,
                "backend_received_at": backend_received_at,
                "canonical_version": canonical_version,
                "original_hash": original_hash,
            },
        )
        .mappings()
        .one()
    )
    return dict(row)


def insert_measurement_values(session, measurement_id: int, record: ValidatedRecord) -> int:
    params: dict = {"mid": measurement_id}
    tuples = []
    for idx, code in enumerate(METRIC_ORDER):
        metric = record.measurements[code]
        params[f"code_{idx}"] = code
        params[f"val_{idx}"] = metric.value
        params[f"st_{idx}"] = metric.status
        tuples.append(f"(:mid, :code_{idx}, :val_{idx}, :st_{idx})")
    result = session.execute(
        text(
            "INSERT INTO dbo.MeasurementValue (measurement_id, metric_code, value, read_status) "
            "VALUES " + ", ".join(tuples)
        ),
        params,
    )
    return int(result.rowcount)


def update_node_runtime(
    session, *, node_id: int, measurement_id: int, persisted_at: datetime
) -> int:
    result = session.execute(
        text(
            "UPDATE dbo.NodeRuntime "
            "SET last_measurement_id = :measurement_id, "
            "last_new_persisted_at = :persisted_at "
            "WHERE node_id = :node_id"
        ),
        {
            "node_id": node_id,
            "measurement_id": measurement_id,
            "persisted_at": persisted_at,
        },
    )
    return int(result.rowcount)


def update_evaluation_state(
    session,
    *,
    node_id: int,
    time_status: str,
    t_node: int | None,
    prev_max_t_node: int | None,
) -> int:
    if time_status == "VALID" and t_node is not None:
        if prev_max_t_node is not None and t_node <= prev_max_t_node:
            result = session.execute(
                text(
                    "UPDATE dbo.EvaluationState "
                    "SET desired_generation = desired_generation + 1, "
                    "full_replay_required = 1, "
                    "replay_from_t_node = CASE "
                    "WHEN replay_from_t_node IS NULL OR :t_node < replay_from_t_node "
                    "THEN :t_node ELSE replay_from_t_node END "
                    "WHERE node_id = :node_id"
                ),
                {"node_id": node_id, "t_node": t_node},
            )
        else:
            result = session.execute(
                text(
                    "UPDATE dbo.EvaluationState "
                    "SET desired_generation = desired_generation + 1, "
                    "full_replay_required = 1 "
                    "WHERE node_id = :node_id"
                ),
                {"node_id": node_id},
            )
    else:
        result = session.execute(
            text(
                "UPDATE dbo.EvaluationState "
                "SET desired_generation = desired_generation + 1 "
                "WHERE node_id = :node_id"
            ),
            {"node_id": node_id},
        )
    return int(result.rowcount)
