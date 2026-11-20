from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.core.web import format_decimal_clean, serialize_utc
from app.modules.areas import repository as areas_repo
from app.modules.auth.service import storage_error
from app.modules.devices import repository as devices_repo

METRIC_ORDER = ("temperature", "airHumidity", "pressure", "light", "soilMoisture")
METRIC_UNITS: dict[str, str] = {
    "temperature": "°C",
    "airHumidity": "%RH",
    "pressure": "hPa",
    "light": "lux",
    "soilMoisture": "% tương đối",
}
METRIC_ALLOWED_UNITS: dict[str, frozenset[str]] = {
    "temperature": frozenset({"°C"}),
    "airHumidity": frozenset({"%RH"}),
    "pressure": frozenset({"hPa"}),
    "light": frozenset({"lux"}),
    "soilMoisture": frozenset({"%", "% tương đối"}),
}
VALID_READ_STATUSES = frozenset({"OK", "READ_ERROR", "INVALID_VALUE"})
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)
_CHUNK_SIZE = 500


def serialize_epoch_sec_utc(epoch_sec: int) -> str:
    dt = _EPOCH_UTC + timedelta(seconds=int(epoch_sec))
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_node_meta(session, node_id: str) -> dict | None:
    row = (
        session.execute(
            text(
                "SELECT n.node_id, n.device_code, n.zone_id, z.farm_id, n.gateway_id "
                "FROM dbo.Node n "
                "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "WHERE n.node_id = :id"
            ),
            {"id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def capture_node_measurement_cap(session, node_id: str) -> tuple[int, datetime] | None:
    """Capture MAX(measurement_id) under canonical writer lock order Farm->Zone->Gateway->Node."""
    parents = get_node_meta(session, node_id)
    if parents is None:
        return None
    farm = areas_repo.get_farm(session, str(parents["farm_id"]), lock=True)
    zone = areas_repo.get_zone(session, str(parents["zone_id"]), lock=True)
    if parents["gateway_id"] is not None:
        gateway = devices_repo.lock_gateway(session, parents["gateway_id"])
        if gateway is None:
            raise storage_error()
    node = devices_repo.lock_node(session, node_id)
    if farm is None or zone is None or node is None:
        return None
    if (
        int(node["zone_id"]) != int(parents["zone_id"])
        or node["gateway_id"] != parents["gateway_id"]
    ):
        raise storage_error()
    row = (
        session.execute(
            text(
                "SELECT ISNULL(MAX(measurement_id), 0) AS cap_id, "
                "CAST(SYSUTCDATETIME() AS DATETIME2(3)) AS as_of_utc "
                "FROM dbo.Measurement WHERE node_id = :node_id"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .one()
    )
    return int(row["cap_id"]), row["as_of_utc"]


def load_measurement_values_map(
    session, measurement_ids: list[int]
) -> dict[int, dict[str, dict[str, Any]]]:
    """Load exact 5 metric values per measurement header; raise 503 if any invariant is broken."""
    if not measurement_ids:
        return {}
    unique_ids = list(dict.fromkeys(int(mid) for mid in measurement_ids))
    raw_by_mid: dict[int, dict[str, dict[str, Any]]] = {mid: {} for mid in unique_ids}

    for offset in range(0, len(unique_ids), _CHUNK_SIZE):
        chunk = unique_ids[offset : offset + _CHUNK_SIZE]
        params = {f"mid_{idx}": mid for idx, mid in enumerate(chunk)}
        placeholders = ", ".join(f":mid_{idx}" for idx in range(len(chunk)))
        rows = (
            session.execute(
                text(
                    "SELECT v.measurement_id, v.metric_code, v.value, v.read_status, d.unit "
                    "FROM dbo.MeasurementValue AS v "
                    "JOIN dbo.MetricDefinition AS d ON d.metric_code = v.metric_code "
                    f"WHERE v.measurement_id IN ({placeholders})"
                ),
                params,
            )
            .mappings()
            .all()
        )
        for row in rows:
            mid = int(row["measurement_id"])
            code = row["metric_code"]
            if code not in METRIC_UNITS or mid not in raw_by_mid:
                raise storage_error()
            status = row["read_status"]
            value = row["value"]
            if status not in VALID_READ_STATUSES:
                raise storage_error()
            if status == "OK" and value is None:
                raise storage_error()
            if status != "OK" and value is not None:
                raise storage_error()
            if code in raw_by_mid[mid]:
                raise storage_error()
            raw_by_mid[mid][code] = {
                "value": format_decimal_clean(value) if value is not None else None,
                "status": status,
                "unit": row["unit"],
            }

    ordered_result: dict[int, dict[str, dict[str, Any]]] = {}
    for mid in unique_ids:
        found = raw_by_mid[mid]
        if set(found.keys()) != set(METRIC_ORDER):
            raise storage_error()
        ordered_result[mid] = {code: found[code] for code in METRIC_ORDER}
    return ordered_result


def build_measurement_view(
    header: dict, values_by_metric: dict[str, dict[str, Any]], *, device_code: str
) -> dict[str, Any]:
    t_node = header["t_node"]
    t_node_int = int(t_node) if t_node is not None else None
    return {
        "id": str(header["measurement_id"]),
        "node_id": str(header["node_id"]),
        "identity": {
            "device_id": device_code,
            "boot_id": str(header["boot_id"]),
            "seq": str(header["seq"]),
        },
        "t_node": str(t_node_int) if t_node_int is not None else None,
        "event_time": serialize_epoch_sec_utc(t_node_int) if t_node_int is not None else None,
        "time_status": header["time_status"],
        "measurements": {code: values_by_metric[code] for code in METRIC_ORDER},
        "gateway_received_at_epoch": str(header["recv_time"]),
        "persisted_at": serialize_utc(header["persisted_at"]),
    }


def build_chart_point(
    header: dict, values_by_metric: dict[str, dict[str, Any]], *, device_code: str
) -> dict[str, Any]:
    t_node_int = int(header["t_node"])
    return {
        "identity": {
            "device_id": device_code,
            "boot_id": str(header["boot_id"]),
            "seq": str(header["seq"]),
        },
        "event_time": serialize_epoch_sec_utc(t_node_int),
        "measurements": {code: values_by_metric[code] for code in METRIC_ORDER},
    }


def read_node_current(session, *, node_id: int, device_code: str, as_of_iso: str) -> dict[str, Any]:
    """Shared FL-07 / FL-12 helper reading latest VALID event-time header + all 5 metrics."""
    header = (
        session.execute(
            text(
                "SELECT TOP (1) measurement_id, node_id, boot_id, seq, "
                "t_node, time_status, recv_time, persisted_at "
                "FROM dbo.Measurement "
                "WHERE node_id = :node_id AND t_node IS NOT NULL "
                "ORDER BY t_node DESC, boot_id DESC, seq DESC"
            ),
            {"node_id": int(node_id)},
        )
        .mappings()
        .first()
    )
    if header is not None:
        header_dict = dict(header)
        values_map = load_measurement_values_map(session, [int(header_dict["measurement_id"])])
        view = build_measurement_view(
            header_dict,
            values_map[int(header_dict["measurement_id"])],
            device_code=device_code,
        )
        return {"state": "AVAILABLE", "measurement": view, "as_of": as_of_iso}

    has_any = session.execute(
        text("SELECT TOP (1) 1 FROM dbo.Measurement WHERE node_id = :node_id"),
        {"node_id": int(node_id)},
    ).scalar()
    state = "NO_EVENT_TIME" if has_any is not None else "NO_DATA"
    return {"state": state, "measurement": None, "as_of": as_of_iso}


def list_known_headers(
    session,
    *,
    node_id: int,
    lo: int,
    hi: int,
    cap: int,
    ascending: bool,
    boundary: dict[str, int] | None,
    limit: int,
) -> list[dict]:
    if lo > hi or cap == 0:
        return []
    op = ">" if ascending else "<"
    direction = "ASC" if ascending else "DESC"
    params: dict[str, Any] = {
        "limit": limit,
        "node_id": int(node_id),
        "lo": int(lo),
        "hi": int(hi),
        "cap": int(cap),
    }
    keyset_sql = ""
    if boundary is not None:
        keyset_sql = (
            f"AND (t_node {op} :b_t "
            f"OR (t_node = :b_t AND boot_id {op} :b_boot) "
            f"OR (t_node = :b_t AND boot_id = :b_boot AND seq {op} :b_seq)) "
        )
        params["b_t"] = int(boundary["t_node"])
        params["b_boot"] = int(boundary["boot_id"])
        params["b_seq"] = int(boundary["seq"])
    rows = (
        session.execute(
            text(
                "SELECT TOP (:limit) measurement_id, node_id, boot_id, seq, "
                "t_node, time_status, recv_time, persisted_at "
                "FROM dbo.Measurement "
                "WHERE node_id = :node_id AND t_node IS NOT NULL "
                "AND t_node >= :lo AND t_node <= :hi "
                "AND measurement_id <= :cap "
                + keyset_sql
                + f"ORDER BY t_node {direction}, boot_id {direction}, seq {direction}"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def list_unknown_headers(
    session,
    *,
    node_id: int,
    cap: int,
    boundary_persisted_at: datetime | None,
    boundary_measurement_id: int | None,
    limit: int,
) -> list[dict]:
    if cap == 0:
        return []
    params: dict[str, Any] = {"limit": limit, "node_id": int(node_id), "cap": int(cap)}
    keyset_sql = ""
    if boundary_persisted_at is not None and boundary_measurement_id is not None:
        keyset_sql = (
            "AND (persisted_at < :b_persisted_at "
            "OR (persisted_at = :b_persisted_at AND measurement_id < :b_mid)) "
        )
        params["b_persisted_at"] = boundary_persisted_at
        params["b_mid"] = int(boundary_measurement_id)
    rows = (
        session.execute(
            text(
                "SELECT TOP (:limit) measurement_id, node_id, boot_id, seq, "
                "t_node, time_status, recv_time, persisted_at "
                "FROM dbo.Measurement "
                "WHERE node_id = :node_id AND t_node IS NULL "
                "AND measurement_id <= :cap "
                + keyset_sql
                + "ORDER BY persisted_at DESC, measurement_id DESC"
            ),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def count_node_existence_up_to_cap(session, *, node_id: int, cap: int) -> tuple[int, int]:
    if cap == 0:
        return 0, 0
    row = (
        session.execute(
            text(
                "SELECT COUNT_BIG(*) AS total_count, COUNT_BIG(t_node) AS known_count "
                "FROM dbo.Measurement "
                "WHERE node_id = :node_id AND measurement_id <= :cap"
            ),
            {"node_id": int(node_id), "cap": int(cap)},
        )
        .mappings()
        .one()
    )
    return int(row["total_count"]), int(row["known_count"])


def get_farm_summary(session, farm_id: str) -> dict | None:
    row = (
        session.execute(
            text("SELECT farm_id, farm_code, name, archived_at FROM dbo.Farm WHERE farm_id = :id"),
            {"id": int(farm_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


_DASHBOARD_NODE_SELECT = (
    "SELECT n.node_id, n.device_code, n.name, n.receiving_enabled, "
    "z.zone_id, z.farm_id, z.zone_code, z.name AS zone_name, z.archived_at AS zone_archived_at, "
    "g.gateway_id, g.gateway_code, g.name AS gateway_name, "
    "g.receiving_enabled AS gateway_receiving_enabled "
    "FROM dbo.Node AS n "
    "JOIN dbo.Zone AS z ON z.zone_id = n.zone_id "
    "LEFT JOIN dbo.Gateway AS g ON g.gateway_id = n.gateway_id "
)


def get_dashboard_node(session, node_id: str) -> dict | None:
    row = (
        session.execute(
            text(_DASHBOARD_NODE_SELECT + "WHERE n.node_id = :id"),
            {"id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def list_farm_dashboard_nodes(session, farm_id: str) -> list[dict]:
    rows = (
        session.execute(
            text(_DASHBOARD_NODE_SELECT + "WHERE z.farm_id = :farm_id ORDER BY n.node_id ASC"),
            {"farm_id": int(farm_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def load_latest_known_headers_for_nodes(session, node_ids: list[int]) -> dict[int, dict | None]:
    if not node_ids:
        return {}
    result: dict[int, dict | None] = {int(nid): None for nid in node_ids}
    unique_ids = list(result.keys())
    for offset in range(0, len(unique_ids), _CHUNK_SIZE):
        chunk = unique_ids[offset : offset + _CHUNK_SIZE]
        params = {f"nid_{idx}": nid for idx, nid in enumerate(chunk)}
        placeholders = ", ".join(f":nid_{idx}" for idx in range(len(chunk)))
        rows = (
            session.execute(
                text(
                    "SELECT n.node_id, "
                    "m.measurement_id, m.boot_id, m.seq, m.t_node, m.time_status, "
                    "m.recv_time, m.persisted_at "
                    "FROM dbo.Node AS n "
                    "OUTER APPLY ("
                    "  SELECT TOP (1) x.measurement_id, x.boot_id, x.seq, x.t_node, "
                    "  x.time_status, x.recv_time, x.persisted_at "
                    "  FROM dbo.Measurement AS x "
                    "  WHERE x.node_id = n.node_id AND x.t_node IS NOT NULL "
                    "  ORDER BY x.t_node DESC, x.boot_id DESC, x.seq DESC"
                    ") AS m "
                    f"WHERE n.node_id IN ({placeholders})"
                ),
                params,
            )
            .mappings()
            .all()
        )
        for row in rows:
            nid = int(row["node_id"])
            if row["measurement_id"] is not None:
                result[nid] = dict(row)
    return result


def find_nodes_with_any_measurement(session, node_ids: list[int]) -> set[int]:
    if not node_ids:
        return set()
    unique_ids = list(dict.fromkeys(int(nid) for nid in node_ids))
    present: set[int] = set()
    for offset in range(0, len(unique_ids), _CHUNK_SIZE):
        chunk = unique_ids[offset : offset + _CHUNK_SIZE]
        params = {f"nid_{idx}": nid for idx, nid in enumerate(chunk)}
        placeholders = ", ".join(f":nid_{idx}" for idx in range(len(chunk)))
        rows = session.execute(
            text(f"SELECT DISTINCT node_id FROM dbo.Measurement WHERE node_id IN ({placeholders})"),
            params,
        ).scalars()
        present.update(int(nid) for nid in rows)
    return present
